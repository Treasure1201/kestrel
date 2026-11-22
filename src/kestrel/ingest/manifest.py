"""Load a bot's `manifest.yaml` and decide each file's lifecycle status.

Manifest format:

```yaml
collection: my-collection          # optional; checked against the collection ingested
documents:
  - filename: Guide_2026.docx
    sha256: <64 hex characters of the approved bytes>
    label: Product Guide 2026
    family: product-guide          # editions of one guide share a family
    edition: "2026"
    effective_from: 2026-01-01
    supersedes: "2025"             # optional: edition label in the same family
    approved: true
    approved_by: reviewer-name
```

Status rules, applied to every file found in the source folder:

- an existing `blocked` status is kept until an operator unblocks the document;
- with approval `required`, a file is `pending` when it is not listed, its bytes no
  longer match the approved hash, or its entry is not approved;
- a listed, matching (and, if required, approved) file is `superseded` when another
  entry of the same family that is itself present, matching and approved declares
  `supersedes: <its edition>`; otherwise it is `current`;
- with approval `none` (also accepted as `auto`), files that are not listed, or whose
  bytes differ from their entry, are `current` and carry no manifest metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import ValidationError

from kestrel.contracts import DocumentRecord, DocumentStatus, ManifestEntry
from kestrel.errors import ConfigurationError

Approval = Literal["required", "none", "auto"]
_TOP_LEVEL = {"collection", "documents"}


@dataclass(frozen=True)
class Manifest:
    """The approved entries of one collection."""

    entries: tuple[ManifestEntry, ...] = ()
    collection: str | None = None
    source: Path | None = None

    def entry_for(self, filename: str) -> ManifestEntry | None:
        """The entry listed for a file name, compared case-insensitively."""
        key = filename.casefold()
        return next((e for e in self.entries if e.filename.casefold() == key), None)


@dataclass(frozen=True)
class StatusDecision:
    """The status of one file, why, and the manifest entry that applies (if any)."""

    status: DocumentStatus
    reason: str | None
    entry: ManifestEntry | None


def _field_errors(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        where = ".".join(str(p) for p in error["loc"]) or "entry"
        parts.append(f"{where}: {error['msg']}")
    return "; ".join(parts)


def parse_manifest(data: object, *, source: Path | None = None) -> Manifest:
    """Validate an already-loaded YAML document into a `Manifest`.

    Raises `ConfigurationError` naming the entry and field for every problem.
    """
    where = str(source) if source else "manifest"
    if data is None:
        return Manifest(source=source)
    if not isinstance(data, dict):
        raise ConfigurationError(f"{where}: expected a mapping with a 'documents' list.")
    unknown = set(data) - _TOP_LEVEL
    if unknown:
        raise ConfigurationError(f"{where}: unknown top-level field(s): {', '.join(sorted(map(str, unknown)))}.")
    raw = data.get("documents") or []
    if not isinstance(raw, list):
        raise ConfigurationError(f"{where}: 'documents' must be a list.")
    entries: list[ManifestEntry] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigurationError(f"{where}: documents[{index}] must be a mapping.")
        item = dict(item)
        if isinstance(item.get("sha256"), str):
            item["sha256"] = item["sha256"].strip().lower()
        for key in ("edition", "supersedes"):
            if isinstance(item.get(key), (int, float)):
                item[key] = str(item[key])  # an unquoted `edition: 2026` is still a label
        try:
            entries.append(ManifestEntry.model_validate(item))
        except ValidationError as exc:
            name = item.get("filename", f"documents[{index}]")
            raise ConfigurationError(f"{where}: invalid entry {name}: {_field_errors(exc)}.") from exc
    _check_consistency(entries, where)
    collection = data.get("collection")
    if collection is not None and not isinstance(collection, str):
        raise ConfigurationError(f"{where}: 'collection' must be a string.")
    return Manifest(entries=tuple(entries), collection=collection, source=source)


def _check_consistency(entries: list[ManifestEntry], where: str) -> None:
    seen_files: set[str] = set()
    seen_hashes: set[str] = set()
    editions: dict[tuple[str, str], str] = {}
    for entry in entries:
        key = entry.filename.casefold()
        if key in seen_files:
            raise ConfigurationError(f"{where}: {entry.filename} is listed more than once.")
        if entry.sha256 in seen_hashes:
            raise ConfigurationError(f"{where}: {entry.filename} has the same sha256 as another entry.")
        if (entry.family, entry.edition) in editions:
            other = editions[(entry.family, entry.edition)]
            raise ConfigurationError(
                f"{where}: {entry.filename} and {other} are both edition {entry.edition!r} of family {entry.family!r}."
            )
        if entry.approved and not entry.approved_by:
            raise ConfigurationError(f"{where}: {entry.filename} is approved but approved_by is missing.")
        seen_files.add(key)
        seen_hashes.add(entry.sha256)
        editions[(entry.family, entry.edition)] = entry.filename
    for entry in entries:
        if entry.supersedes is None:
            continue
        if entry.supersedes == entry.edition:
            raise ConfigurationError(f"{where}: {entry.filename} cannot supersede its own edition.")
        if (entry.family, entry.supersedes) not in editions:
            raise ConfigurationError(
                f"{where}: {entry.filename} supersedes edition {entry.supersedes!r}, "
                f"which is not listed in family {entry.family!r}."
            )


def load_manifest(path: Path) -> Manifest:
    """Read and validate a manifest file. Raises `ConfigurationError` if it is missing or invalid."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Manifest not found: {path}. Create it or set the bot's approval to 'none'.") from exc
    except OSError as exc:
        raise ConfigurationError(f"Manifest could not be read: {path}.", detail=str(exc)) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Manifest is not valid YAML: {path}.", detail=str(exc)) from exc
    return parse_manifest(data, source=path)


def decide_statuses(
    files: Mapping[str, str],
    manifest: Manifest,
    approval: Approval,
    existing: Mapping[str, DocumentRecord] | None = None,
) -> dict[str, StatusDecision]:
    """Decide the status of every file.

    Args:
        files: file name to sha256 of its current bytes, for every file in the folder.
        manifest: the collection's manifest (empty when there is none).
        approval: the bot's approval setting.
        existing: stored records by file name, used to keep a `blocked` status.
    """
    existing = existing or {}
    required = approval == "required"

    def usable(entry: ManifestEntry | None, sha256: str | None) -> bool:
        return entry is not None and sha256 == entry.sha256 and (entry.approved or not required)

    # Entries whose file is present with the approved bytes can supersede others.
    active = [e for e in manifest.entries if usable(e, _sha_for(files, e.filename))]
    decisions: dict[str, StatusDecision] = {}
    for filename, sha256 in files.items():
        entry = manifest.entry_for(filename)
        record = existing.get(filename)
        if record is not None and record.status == DocumentStatus.BLOCKED:
            reason = record.status_reason or "blocked by an operator"
            decisions[filename] = StatusDecision(DocumentStatus.BLOCKED, reason, entry if usable(entry, sha256) else None)
            continue
        if entry is None:
            if required:
                decisions[filename] = StatusDecision(DocumentStatus.PENDING, "not listed in the manifest", None)
            else:
                decisions[filename] = StatusDecision(DocumentStatus.CURRENT, "approval not required", None)
            continue
        if sha256 != entry.sha256:
            if required:
                reason = f"content changed since approval (sha256 {sha256[:12]}..., approved {entry.sha256[:12]}...)"
                decisions[filename] = StatusDecision(DocumentStatus.PENDING, reason, None)
            else:
                decisions[filename] = StatusDecision(DocumentStatus.CURRENT, "differs from manifest; approval not required", None)
            continue
        if required and not entry.approved:
            decisions[filename] = StatusDecision(DocumentStatus.PENDING, "listed but not approved", entry)
            continue
        successor = next(
            (o for o in active if o is not entry and o.family == entry.family and o.supersedes == entry.edition), None
        )
        if successor is not None:
            reason = f"superseded by edition {successor.edition} ({successor.filename})"
            decisions[filename] = StatusDecision(DocumentStatus.SUPERSEDED, reason, entry)
        else:
            decisions[filename] = StatusDecision(DocumentStatus.CURRENT, None, entry)
    return decisions


def _sha_for(files: Mapping[str, str], filename: str) -> str | None:
    key = filename.casefold()
    return next((sha for name, sha in files.items() if name.casefold() == key), None)
