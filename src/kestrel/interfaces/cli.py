"""Command-line interface over `AssistantService`.

Every command builds the local principal from settings (KESTREL_USER, KESTREL_ROLES)
on the `cli` channel and calls the service. Expected failures print one line to
stderr and exit non-zero; `--debug` adds the traceback. Output is ASCII-safe.
"""

from __future__ import annotations

import importlib
from typing import Annotated

import typer

from kestrel import __version__
from kestrel.interfaces import cli_runtime as rt
from kestrel.interfaces.cli_admin import audit_app, docs_app
from kestrel.interfaces.cli_render import ask_json, exit_code, render_ask, render_search

app = typer.Typer(
    help="Kestrel assistant: governed, cited answers from approved documents.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    add_completion=False,
    rich_markup_mode=None,
)
app.add_typer(docs_app, name="docs")
app.add_typer(audit_app, name="audit")

BotOption = Annotated[str | None, typer.Option("--bot", "-b", help="Bot id. Defaults to the configured default bot.")]
AllBots = Annotated[bool, typer.Option("--all", help="Every bot you may use, instead of one.")]

EXIT_WORDS = {"exit", "quit"}


@app.callback()
def main(
    debug: Annotated[bool, typer.Option("--debug", help="Show a traceback when a command fails.")] = False,
) -> None:
    """Kestrel assistant command line."""
    rt.STATE.debug = debug


@app.command()
def version() -> None:
    """Print the installed version."""
    rt.out(f"kestrel {__version__}")


@app.command()
@rt.handle_errors
def doctor() -> None:
    """Check the environment end to end; exit 1 if any check fails."""
    settings = rt.get_settings()
    report = rt.get_service(settings).doctor()
    rows = [(c.name, c.status.upper(), c.detail) for c in report.checks]
    rt.print_table(["Check", "Status", "Detail"], rows, max_width=70)
    fixes = [c for c in report.checks if c.status != "ok" and c.fix]
    if fixes:
        rt.out()
        rt.out("To fix:")
        for check in fixes:
            rt.out(f"  {check.name}: {check.fix}")
    failed = sum(1 for c in report.checks if c.status == "fail")
    warned = sum(1 for c in report.checks if c.status == "warn")
    rt.out()
    rt.out(f"{len(report.checks)} check(s): {failed} failed, {warned} warning(s).")
    if not report.ok:
        raise typer.Exit(1)


@app.command()
@rt.handle_errors
def ingest(bot: BotOption = None, all_bots: AllBots = False) -> None:
    """Ingest the bot's source folders. Safe to repeat: unchanged files are left alone."""
    settings = rt.get_settings()
    service = rt.get_service(settings)
    bot_id = None if all_bots else (bot or settings.default_bot)
    report = service.ingest(rt.principal(settings), bot_id=bot_id)
    rows = [
        (f.collection, f.filename, f.action, f.status.value if f.status else "", f.findings, f.detail or "")
        for f in report.files
    ]
    rt.print_table(["Collection", "File", "Action", "Status", "Findings", "Detail"], rows)
    for warning in report.warnings:
        rt.out(f"Warning: {warning}")
    counts: dict[str, int] = {}
    for f in report.files:
        counts[f.action] = counts.get(f.action, 0) + 1
    summary = ", ".join(f"{n} {action}" for action, n in sorted(counts.items())) or "nothing to do"
    rt.out()
    rt.out(f"{len(report.files)} file(s): {summary}.")
    if counts.get("failed"):
        raise typer.Exit(1)


@app.command()
@rt.handle_errors
def bots() -> None:
    """Bots you may use."""
    settings = rt.get_settings()
    summaries = rt.get_service(settings).list_bots(rt.principal(settings))
    if not summaries:
        rt.out("No bots are available to you. Check KESTREL_ROLES.")
        return
    for b in summaries:
        default = "  (default)" if b.id == settings.default_bot else ""
        rt.out(f"{b.id} - {b.name}{default}")
        if b.description:
            rt.out(f"    {' '.join(b.description.split())}")
        rt.out(f"    classification: {b.classification}; model: {b.model}; collections: {', '.join(b.collections)}")


@app.command()
@rt.handle_errors
def ask(
    question: Annotated[str, typer.Argument(help='The question, in quotes: kestrel ask "your question"')],
    bot: BotOption = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Override the model, as provider:model.")] = None,
    trace: Annotated[bool, typer.Option("--trace", help="Also show retrieved evidence and check details.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the full result as JSON and nothing else.")] = False,
) -> None:
    """Ask one question. Exit 0 when answered, partial, not found or refused; 2 when withheld; 1 on error."""
    if not question.strip():
        rt.fail("Error: the question is empty.")
    settings = rt.get_settings()
    service = rt.get_service(settings)
    result = service.ask(bot or settings.default_bot, question, rt.principal(settings), model=model)
    if as_json:
        typer.echo(ask_json(result))
    else:
        render_ask(result, trace=trace)
    code = exit_code(result)
    if code:
        raise typer.Exit(code)


@app.command()
@rt.handle_errors
def chat(
    bot: BotOption = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Override the model, as provider:model.")] = None,
    trace: Annotated[bool, typer.Option("--trace", help="Also show retrieved evidence and check details.")] = False,
) -> None:
    """Ask questions one after another. Type exit or quit (or end input) to leave."""
    settings = rt.get_settings()
    service = rt.get_service(settings)
    bot_id = bot or settings.default_bot
    principal = rt.principal(settings)
    rt.out(f"Chatting with {bot_id}. Each question is answered on its own. Type exit to leave.")
    while True:
        typer.echo("> ", nl=False)
        try:
            line = input()
        except EOFError:
            rt.out()
            break
        question = line.strip()
        if not question:
            continue
        if question.lower() in EXIT_WORDS:
            break
        render_ask(service.ask(bot_id, question, principal, model=model), trace=trace)
        rt.out()
    rt.out("Goodbye.")


@app.command()
@rt.handle_errors
def search(
    query: Annotated[str, typer.Argument(help='What to look for: kestrel search "your words"')],
    bot: BotOption = None,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=50, help="Most passages to show.")] = 8,
) -> None:
    """Search the bot's current documents without calling a model."""
    if not query.strip():
        rt.fail("Error: the search query is empty.")
    settings = rt.get_settings()
    service = rt.get_service(settings)
    render_search(service.search(bot or settings.default_bot, query, rt.principal(settings), limit=limit))


@app.command()
@rt.handle_errors
def mcp() -> None:
    """Start the MCP server on stdio, for desktop MCP clients."""
    try:
        server = importlib.import_module("kestrel.interfaces.mcp_server")
    except ImportError:
        rt.fail("Error: the MCP server is not available in this installation.")
    entry = getattr(server, "main", None)
    if not callable(entry):
        rt.fail("Error: the MCP server module has no main() entry point.")
    entry()


__all__ = ["app"]
