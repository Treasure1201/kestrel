"""Shared plumbing for the command line: settings, the service, safe output and error handling.

The CLI reaches the application only through `AssistantService`. The factory is
imported lazily in `get_service`, so commands that need no service (`version`) work
even when the service cannot be built, and tests can replace `get_service`.
"""

from __future__ import annotations

import functools
import traceback
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar

import typer
from pydantic import ValidationError

from kestrel.config import Settings
from kestrel.contracts import AssistantService, Principal
from kestrel.errors import ConfigurationError, KestrelError

if TYPE_CHECKING:
    from collections.abc import Sequence

F = TypeVar("F", bound=Callable[..., Any])

_REPLACEMENTS = {
    " ": " ",  # no-break space
    " ": " ",
    " ": " ",
    " ": " ",
    "​": "",  # zero-width space
    "﻿": "",
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "′": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "″": '"',
    "«": '"',
    "»": '"',
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
    "−": "-",
    "…": "...",
    "•": "*",
    "·": "*",
    "→": "->",
    "←": "<-",
    "×": "x",
    "÷": "/",
    "≤": "<=",
    "≥": ">=",
    "≠": "!=",
    "±": "+/-",
    "€": "EUR",
    "£": "GBP",
    "©": "(c)",
    "®": "(R)",
    "™": "(TM)",
    "°": " deg",
    "½": "1/2",
    "¼": "1/4",
    "¾": "3/4",
}
_TRANSLATE = str.maketrans(_REPLACEMENTS)


def safe(text: object) -> str:
    """Return `text` as plain ASCII, so any console code page (cp1252, cp437) can print it.

    Typographic punctuation becomes its ASCII equivalent, accented letters lose their
    accents, and anything else that has no ASCII form becomes `?`.
    """
    value = str(text).translate(_TRANSLATE)
    if value.isascii():
        return value
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.encode("ascii", "replace").decode("ascii")


def out(text: object = "") -> None:
    """Print one line to stdout, ASCII-safe."""
    typer.echo(safe(text))


def err(text: object) -> None:
    """Print one line to stderr, ASCII-safe."""
    typer.echo(safe(text), err=True)


@dataclass
class RunState:
    """Options set once per invocation by the top-level callback."""

    debug: bool = False


STATE = RunState()


def get_settings() -> Settings:
    """Load settings from the environment and `.env`."""
    try:
        return Settings()
    except ValidationError as exc:
        fields = ", ".join(".".join(str(p) for p in e["loc"]) or "?" for e in exc.errors())
        raise ConfigurationError(
            f"Invalid setting ({fields}); check your environment variables and .env file.",
            detail=str(exc),
        ) from exc


def get_service(settings: Settings | None = None) -> AssistantService:
    """Build the assistant service. Imported lazily; replaced by a fake in tests."""
    try:
        from kestrel.service import build_service  # noqa: PLC0415 - lazy by design
    except ImportError as exc:
        raise ConfigurationError(
            "The assistant service is not available in this installation; reinstall with `uv sync`.",
            detail=repr(exc),
        ) from exc
    return build_service(settings if settings is not None else get_settings())


def principal(settings: Settings) -> Principal:
    """The local principal (KESTREL_USER, KESTREL_ROLES) on the `cli` channel."""
    return settings.principal("cli")


def fail(message: str, code: int = 1) -> NoReturn:
    """Print one line to stderr and exit with `code`."""
    err(message)
    raise typer.Exit(code)


def handle_errors(func: F) -> F:
    """Turn expected and unexpected failures into one stderr line and a non-zero exit.

    With `--debug` the traceback is printed as well.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except (typer.Exit, typer.Abort, SystemExit):
            raise
        except KestrelError as exc:
            _maybe_traceback()
            fail(f"Error: {exc.user_message}")
        except KeyboardInterrupt:
            fail("Interrupted.", 130)
        except Exception as exc:  # noqa: BLE001 - last line of defence for the user
            _maybe_traceback()
            hint = "" if STATE.debug else " Run again with --debug for details."
            fail(f"Error: unexpected {type(exc).__name__}.{hint}")

    return wrapper  # type: ignore[return-value]


def _maybe_traceback() -> None:
    if STATE.debug:
        err(traceback.format_exc().rstrip())


def table(headers: Sequence[str], rows: Sequence[Sequence[object]], max_width: int = 60) -> list[str]:
    """Render rows as a plain, left-aligned text table. Cells longer than `max_width` are cut."""

    def cell(value: object) -> str:
        text = " ".join(safe("" if value is None else value).split())
        return text if len(text) <= max_width else text[: max_width - 3] + "..."

    body = [[cell(v) for v in row] for row in rows]
    head = [cell(h) for h in headers]
    widths = [max([len(head[i])] + [len(r[i]) for r in body]) for i in range(len(head))]

    def line(values: Sequence[str]) -> str:
        return "  ".join(v.ljust(widths[i]) for i, v in enumerate(values)).rstrip()

    return [line(head), line(["-" * w for w in widths])] + [line(r) for r in body]


def print_table(headers: Sequence[str], rows: Sequence[Sequence[object]], max_width: int = 60) -> None:
    """Print a plain text table, or a note when there are no rows."""
    if not rows:
        out("(none)")
        return
    for text in table(headers, rows, max_width):
        out(text)
