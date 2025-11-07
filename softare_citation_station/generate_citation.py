"""
Utility to gather Software Citation Station metadata from PyPI and emit a Markdown
file shaped like `<package>_citation.md`.

The script is intentionally lightweight (standard library only) so it can be
copied as a snippet into other projects or run ad-hoc from the CLI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
import urllib.error
import urllib.request
from typing import Dict, Iterable, List


PYPI_JSON_URL = "https://pypi.org/pypi/{package}/json"
USER_AGENT = "SoftwareCitationStation/0.1 (+https://github.com/zonca/software_citation_station)"

def get_pypi_payload(package: str) -> Dict:
    url = PYPI_JSON_URL.format(package=package)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response:  # type: ignore[call-arg]
        return json.load(response)


def normalize_keywords(raw_keywords: str | None) -> List[str]:
    if not raw_keywords:
        return []
    # PyPI keywords are usually a comma or space separated string.
    cleaned = re.sub(r"[;|]", ",", raw_keywords)
    parts = [part.strip() for part in cleaned.replace("\n", ",").split(",")]
    return [part for part in parts if part]


def extract_language(classifiers: Iterable[str]) -> str:
    for classifier in classifiers:
        if classifier.startswith("Programming Language :: Python"):
            return "Python"
    for classifier in classifiers:
        if classifier.startswith("Programming Language ::"):
            return classifier.split("::")[-1].strip()
    return ""


def extract_category(classifiers: Iterable[str]) -> str:
    for classifier in classifiers:
        if classifier.startswith("Topic ::"):
            return classifier.split("::")[-1].strip()
    for classifier in classifiers:
        if classifier.startswith("Intended Audience ::"):
            return classifier.split("::")[-1].strip()
    return ""


def simplify_dependency(requirement: str) -> str:
    requirement = requirement.split(";", 1)[0].strip()
    requirement = requirement.split("[", 1)[0].strip()
    requirement = re.split(r"[<>=!~() ]", requirement, maxsplit=1)[0].strip()
    return requirement


def gather_dependencies(requires_dist: Iterable[str] | None) -> List[str]:
    if not requires_dist:
        return []
    simplified = {
        simplify_dependency(req)
        for req in requires_dist
        if req and "extra ==" not in req
    }
    return sorted(dep for dep in simplified if dep)


def normalize_github_repo(url: str) -> str:
    if not url:
        return ""
    match = re.search(r"https?://github\.com/([^/\s]+)/([^/\s#]+)", url)
    if not match:
        return ""
    owner, repo = match.groups()
    repo = repo.rstrip(".git")
    return f"https://github.com/{owner}/{repo}"


def extract_repo_path(repo_url: str) -> str:
    path = repo_url.replace("https://github.com/", "").replace("http://github.com/", "").strip("/")
    parts = path.split("/")
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return ""


def url_exists(url: str) -> bool:
    if not url:
        return False
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request):
            return True
    except urllib.error.HTTPError as error:
        if error.code == 405:
            fallback_request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(fallback_request):
                    return True
            except urllib.error.URLError:
                return False
        return False
    except urllib.error.URLError:
        return False


def find_github_repo(info: Dict) -> str:
    project_urls = info.get("project_urls") or {}
    candidates = list(project_urls.values())
    for key in ("home_page", "project_url"):
        val = info.get(key)
        if val:
            candidates.append(val)
    for candidate in candidates:
        repo = normalize_github_repo(candidate or "")
        if repo:
            return repo
    description = info.get("description") or ""
    repo = normalize_github_repo(description)
    if repo:
        return repo
    return ""


def primary_homepage(info: Dict) -> str:
    project_urls = info.get("project_urls") or {}
    homepage_keys = ["Homepage", "homepage", "Home", "home", "Source"]
    for key in homepage_keys:
        if key in project_urls:
            return project_urls[key]
    return info.get("home_page") or ""


def find_attribution_link(info: Dict) -> str:
    project_urls = info.get("project_urls") or {}
    for key, url in project_urls.items():
        if "cite" in key.lower() or "citation" in key.lower():
            return url
    repo_url = find_github_repo(info)
    if repo_url:
        citation_paths = ["CITATION", "CITATION.cff", "CITATION.md"]
        branches = ["main", "master"]
        for branch in branches:
            for name in citation_paths:
                blob_url = f"{repo_url}/blob/{branch}/{name}"
                raw_url = f"https://raw.githubusercontent.com/{extract_repo_path(repo_url)}/{branch}/{name}"
                if url_exists(raw_url):
                    return blob_url
    return ""


def cleaned_summary(info: Dict) -> str:
    summary = (info.get("summary") or "").strip()
    if summary:
        return summary
    description = (info.get("description") or "").strip()
    description = re.sub(r"\s+", " ", description)
    description = re.sub(r"[`*_#<>]", "", description)
    return textwrap.shorten(description, width=240, placeholder="...")


def extract_dois(info: Dict) -> List[str]:
    text_fragments = [
        info.get("summary") or "",
        info.get("description") or "",
    ]
    project_urls = info.get("project_urls") or {}
    text_fragments.extend(project_urls.values())
    doi_pattern = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
    found = {match.rstrip(".,)") for match in doi_pattern.findall(" ".join(text_fragments))}
    return sorted(found)


def fetch_bibtex(doi: str) -> str | None:
    url = f"https://doi.org/{doi}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/x-bibtex; charset=utf-8",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request) as response:  # type: ignore[call-arg]
            return response.read().decode("utf-8").strip()
    except urllib.error.URLError:
        return None


def zenodo_doi(dois: Iterable[str]) -> str:
    for doi in dois:
        if "10.5281/zenodo" in doi.lower():
            return doi
    return ""


def ensure_value(value, *, fallback: str | List[str]):
    if isinstance(value, str):
        return value if value else fallback
    if isinstance(value, list):
        return value if value else fallback
    return fallback


def split_bibtex_fields(fields_part: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    brace_depth = 0
    in_quotes = False
    i = 0
    while i < len(fields_part):
        char = fields_part[i]
        if char == '"':
            in_quotes = not in_quotes
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(brace_depth - 1, 0)

        if char == "," and brace_depth == 0 and not in_quotes:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(char)
        i += 1

    remainder = "".join(current).strip()
    if remainder:
        parts.append(remainder)
    return parts


def format_bibtex_entry(entry: str, indent: str = "  ") -> str:
    text = entry.strip()
    if not text.startswith("@") or "{" not in text:
        return text
    try:
        type_start = 1
        type_end = text.index("{")
    except ValueError:
        return text

    entry_type = text[type_start:type_end].strip()
    remainder = text[type_end + 1 :]
    brace_depth = 1
    index = 0
    while index < len(remainder) and brace_depth > 0:
        char = remainder[index]
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth -= 1
        index += 1
    if brace_depth != 0:
        return text

    body = remainder[: index - 1].strip()
    if "," not in body:
        return text

    key, fields_part = body.split(",", 1)
    key = key.strip()
    fields = split_bibtex_fields(fields_part.strip())
    if not fields:
        return text

    lines = [f"@{entry_type}{{{key},"]
    for idx, field in enumerate(fields):
        field = field.strip()
        if not field:
            continue
        if "=" in field:
            name, value = field.split("=", 1)
            name = name.strip()
            value = value.strip()
            field = f"{name} = {value}"
        suffix = "," if idx < len(fields) - 1 else ""
        lines.append(f"{indent}{field}{suffix}")
    lines.append("}")
    return "\n".join(lines)


def build_metadata(package: str, payload: Dict) -> Dict:
    info = payload["info"]
    classifiers = info.get("classifiers") or []
    keywords = normalize_keywords(info.get("keywords"))
    deps = gather_dependencies(info.get("requires_dist"))
    langs = extract_language(classifiers)
    category = extract_category(classifiers)
    doi_list = extract_dois(info)
    defaults = {
        "tags": ["FIXME"],
        "logo": "FIXME",
        "language": "FIXME",
        "category": "FIXME",
        "keywords": ["FIXME"],
        "description": "FIXME",
        "link": "FIXME",
        "attribution_link": "FIXME",
        "zenodo_doi": "FIXME",
        "custom_citation": "FIXME",
        "dependencies": ["FIXME"],
    }

    populated = {
        "tags": defaults["tags"],
        "logo": defaults["logo"],
        "language": ensure_value(langs, fallback=defaults["language"]),
        "category": ensure_value(category, fallback=defaults["category"]),
        "keywords": ensure_value(keywords, fallback=defaults["keywords"]),
        "description": ensure_value(cleaned_summary(info), fallback=defaults["description"]),
        "link": ensure_value(primary_homepage(info), fallback=defaults["link"]),
        "attribution_link": ensure_value(find_attribution_link(info), fallback=defaults["attribution_link"]),
        "zenodo_doi": ensure_value(zenodo_doi(doi_list), fallback=defaults["zenodo_doi"]),
        "custom_citation": defaults["custom_citation"],
        "dependencies": ensure_value(deps, fallback=defaults["dependencies"]),
    }
    return populated


def build_markdown(package: str, metadata: Dict, bibtex_entries: List[str]) -> str:
    lines = ["# Citation information", "```"]
    metadata_json = json.dumps(metadata, indent=4, sort_keys=False)
    metadata_lines = metadata_json.splitlines()
    if metadata_lines:
        metadata_lines[0] = f"\"{package}\": {metadata_lines[0]}"
    content = "\n".join(metadata_lines)
    lines.append(content)
    lines.append("```")
    lines.append("")
    lines.append("# BibTeX")
    lines.append("```")
    if bibtex_entries:
        entries = [
            format_bibtex_entry(entry.strip())
            for entry in bibtex_entries
            if entry.strip()
        ]
        if entries:
            lines.append("\n\n".join(entries))
    else:
        lines.append("No BibTeX entries discovered.")
    lines.append("```")
    return "\n".join(lines) + "\n"


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", help="PyPI package name to analyse.")
    parser.add_argument(
        "-o",
        "--output",
        help="Optional output Markdown path. Defaults to stdout.",
    )
    args = parser.parse_args(argv)

    package = args.package

    try:
        payload = get_pypi_payload(package)
    except urllib.error.HTTPError as error:
        parser.error(f"Failed to fetch '{package}' from PyPI: {error}")
        return 1

    metadata = build_metadata(package, payload)
    dois = extract_dois(payload["info"])
    bib_entries = []
    for doi in dois:
        bib = fetch_bibtex(doi)
        if bib:
            bib_entries.append(bib)

    if not metadata["tags"]:
        metadata["tags"] = ["FIXME"]

    markdown = build_markdown(package, metadata, bib_entries)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(markdown)
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
