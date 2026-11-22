"""Nothing under src/ may name the business domain, the organisation or a source document.

The application is a general platform; domain knowledge lives in bot folders and
documents only. Allowed: the package name `kestrel` in lower case, the `KESTREL_`
environment prefix, and CamelCase code identifiers that start with it (such as
`KestrelError`). Everything else spelling the organisation's name is forbidden.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
SOURCE_DOCUMENTS = REPO / "data" / "source"

DOMAIN_WORDS = (
    "insurance", "insurer", "insured", "assurance", "funeral", "disability", "disabled", "underwriting",
    "underwriter", "premium", "life cover", "group life", "death benefit", "dental", "actuar",
)
# The organisation name in any form except: `kestrel` (package), `KESTREL_` (env prefix), `KestrelSomething` (identifiers).
ORGANISATION = re.compile(r"Kestrel(?![A-Z])|KESTREL(?!_)|(?<![a-z_./\\])kestrel(?=\s+[A-Z])", re.MULTILINE)


def _source_files() -> list[Path]:
    return [p for p in SRC.rglob("*") if p.is_file() and "__pycache__" not in p.parts]


def _document_names() -> list[str]:
    names = {p.name for p in SOURCE_DOCUMENTS.rglob("*") if p.is_file()}
    return sorted(names | {Path(n).stem for n in names})


def test_there_are_source_files_to_check() -> None:
    assert len(_source_files()) > 20
    assert _document_names()


def test_no_domain_words_under_src() -> None:
    offenders = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        offenders += [f"{path.relative_to(REPO)}: {word}" for word in DOMAIN_WORDS if word in text]
    assert offenders == []


def test_no_organisation_name_under_src() -> None:
    offenders = []
    for path in _source_files():
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            if ORGANISATION.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{number}: {line.strip()}")
    assert offenders == []


def test_no_source_document_names_under_src() -> None:
    names = [n.lower() for n in _document_names()]
    offenders = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        offenders += [f"{path.relative_to(REPO)}: {name}" for name in names if name in text]
    assert offenders == []


def test_the_rule_itself_catches_what_it_should() -> None:
    assert ORGANISATION.search("the Kestrel Product Assistant")
    assert ORGANISATION.search("KESTREL ASSURANCE")
    assert not ORGANISATION.search("from kestrel.contracts import AskResult")
    assert not ORGANISATION.search("KESTREL_MODEL=anthropic:x")
    assert not ORGANISATION.search("class KestrelError(Exception):")
    assert not ORGANISATION.search('default_bot: str = "kestrel-products"')
