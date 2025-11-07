# Software Citation Station Helper

`generate_citation.py` is a small, standard-library Python helper that turns the JSON
metadata exposed by PyPI into the Markdown structure expected by the Software
Citation Station project. It fills in all fields that can be inferred from PyPI
(description, language, dependencies, Zenodo DOI, etc.), looks for a GitHub
repository and its `CITATION*` files to build the attribution link, downloads
BibTeX entries for any DOI it finds, and prints the final Markdown block to
stdout unless you target a file.

## Usage

```bash
python generate_citation.py <package>            # write Markdown to stdout
python generate_citation.py <package> -o output.md
```

Example:

```bash
python generate_citation.py healpy -o healpy_citation_generated.md
```

This produces a file with the same structure as `healpy_citation.md`. Any fields
that PyPI cannot provide are filled with `FIXME` so they can be reviewed and
edited manually before inclusion in Software Citation Station.
