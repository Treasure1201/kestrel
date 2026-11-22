"""Find the source files of a collection, skipping operating-system and editor artefacts."""

from __future__ import annotations

from pathlib import Path

from kestrel.errors import ConfigurationError

_SKIP_DIRS = frozenset({"__MACOSX", ".git", ".svn", "__pycache__", ".ipynb_checkpoints"})
_SKIP_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini", ".gitkeep", ".gitignore"})


def artefact_reason(relative: Path) -> str | None:
    """Why a file is an artefact rather than content, or None when it is content."""
    if any(part in _SKIP_DIRS for part in relative.parts[:-1]):
        return "inside a tool or system folder"
    name = relative.name
    if name.casefold() in _SKIP_NAMES:
        return "operating-system file"
    if name.startswith("~$"):
        return "office lock file"
    if name.startswith("._"):
        return "resource-fork file"
    if name.startswith(".") or name.endswith(("~", ".tmp", ".bak")):
        return "hidden or temporary file"
    return None


def discover_files(folder: Path) -> list[Path]:
    """Content files directly in `folder` (not subfolders), sorted by name.

    Raises `ConfigurationError` when the folder does not exist.
    """
    if not folder.is_dir():
        raise ConfigurationError(f"Source folder not found: {folder}.")
    files = [p for p in folder.iterdir() if p.is_file() and artefact_reason(p.relative_to(folder)) is None]
    return sorted(files, key=lambda p: p.name.casefold())
