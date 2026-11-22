"""`kestrel docs ...` and `kestrel audit ...`: documents, findings and the audit trail."""

from __future__ import annotations

from typing import Annotated

import typer

from kestrel.config import Settings
from kestrel.contracts import AuditRecord
from kestrel.interfaces import cli_runtime as rt
from kestrel.interfaces.cli_render import snippet, source_line

docs_app = typer.Typer(help="List documents and findings, show a section, block or unblock a document.", no_args_is_help=True, rich_markup_mode=None)
audit_app = typer.Typer(help="Read, verify and prune the audit trail.", no_args_is_help=True, rich_markup_mode=None)

BotOption = Annotated[str | None, typer.Option("--bot", "-b", help="Bot id. Defaults to the configured default bot.")]
AllBots = Annotated[bool, typer.Option("--all", help="Every bot you may use, instead of one.")]
Reason = Annotated[str, typer.Option("--reason", "-r", help="Why; recorded in the audit trail.")]
DocumentId = Annotated[str, typer.Argument(help="Document id, as shown by `kestrel docs list`.")]


def _bot(settings: Settings, bot: str | None) -> str:
    return bot or settings.default_bot


def _reason(reason: str) -> str:
    text = reason.strip()
    if not text:
        rt.fail("Error: --reason must not be empty.")
    return text


# ----------------------------------------------------------------------------- docs


@docs_app.command("list")
@rt.handle_errors
def docs_list(bot: BotOption = None) -> None:
    """Documents in the bot's collections, with status."""
    settings = rt.get_settings()
    service = rt.get_service(settings)
    records = service.list_documents(_bot(settings, bot), rt.principal(settings))
    rows = [
        (d.id, d.collection, d.filename, d.edition or "", d.status.value, d.section_count, d.chunk_count, d.table_count,
         d.finding_count, d.status_reason or "")
        for d in records
    ]
    head = ["ID", "Collection", "File", "Edition", "Status", "Sections", "Chunks", "Tables", "Findings", "Reason"]
    rt.print_table(head, rows, max_width=40)


@docs_app.command("compare")
@rt.handle_errors
def docs_compare(document_id: DocumentId, bot: BotOption = None) -> None:
    """What changed between a document and the other edition of its family (no model is used)."""
    settings = rt.get_settings()
    service = rt.get_service(settings)
    result = service.compare_editions(_bot(settings, bot), document_id, rt.principal(settings))
    rt.out(f"Editions of {result.family or 'this document'}:")
    rt.print_table(
        ["ID", "File", "Edition", "Status", "Effective from"],
        [(d.id, d.filename, d.edition or "", d.status.value, d.effective_from.isoformat() if d.effective_from else "")
         for d in result.editions],
        max_width=50,
    )
    rt.out()
    if result.older is None or result.newer is None:
        rt.out(result.note or "Nothing to compare.")
        return
    rt.out(f"Comparing {result.older.filename} (edition {result.older.edition}) with {result.newer.filename} (edition {result.newer.edition}).")
    if result.values:
        rt.out()
        rt.out("Values that changed:")
        rows = [(change.heading.split(" > ")[-1], value.before or "-", value.after or "-", value.context) for change, value in result.values]
        rt.print_table(["Section", result.older.edition or "older", result.newer.edition or "newer", "Context"], rows, max_width=50)
    for change in result.changes:
        rt.out()
        rt.out(f"[{change.change}] {change.heading}")
        if change.older_text:
            rt.out(f"  {result.older.edition or 'older'}: {snippet(change.older_text, 240)}")
        if change.newer_text:
            rt.out(f"  {result.newer.edition or 'newer'}: {snippet(change.newer_text, 240)}")
    if result.note:
        rt.out()
        rt.out(result.note)
    rt.out()
    rt.out(f"{len(result.changes)} section(s) differ; {len(result.values)} value(s) changed.")


@docs_app.command("findings")
@rt.handle_errors
def docs_findings(
    document_id: Annotated[str | None, typer.Argument(help="Limit to one document id.")] = None,
    bot: BotOption = None,
) -> None:
    """Scanner findings: hidden, low-contrast, tiny or off-page text, and instruction-like text."""
    settings = rt.get_settings()
    service = rt.get_service(settings)
    findings = service.list_findings(_bot(settings, bot), rt.principal(settings), document_id)
    if not findings:
        rt.out("No findings.")
        return
    for i, f in enumerate(findings, start=1):
        rt.out(f"[{i}] {f.severity.upper()} {f.kind}  {f.document_id}  {f.locator}")
        rt.out(f"    {f.detail}")
        if f.excerpt:
            rt.out(f'    text: "{snippet(f.excerpt, 200)}"')
    rt.out()
    rt.out(f"{len(findings)} finding(s).")


@docs_app.command("show")
@rt.handle_errors
def docs_show(
    section_id: Annotated[str, typer.Argument(help="Section id, as shown by `kestrel search`.")],
    bot: BotOption = None,
) -> None:
    """Print one whole section."""
    settings = rt.get_settings()
    service = rt.get_service(settings)
    view = service.get_section(_bot(settings, bot), section_id, rt.principal(settings))
    rt.out(source_line(view.filename, view.locator))
    edition = f", edition {view.edition}" if view.edition else ""
    rt.out(f"{view.title} | {view.kind} | {view.status.value}{edition} | document {view.document_id}")
    if view.heading_path:
        rt.out(" > ".join(view.heading_path))
    rt.out()
    if view.table is not None:
        rows = [(row.row_number, *row.cells) for row in view.table.rows]
        width = max([len(view.table.header)] + [len(r.cells) for r in view.table.rows])
        header = ["Row", *view.table.header, *([""] * (width - len(view.table.header)))]
        rows = [tuple(r) + ("",) * (len(header) - len(r)) for r in rows]
        rt.print_table(header, rows, max_width=40)
    else:
        for line in view.text.splitlines():
            rt.out(line)


@docs_app.command("block")
@rt.handle_errors
def docs_block(document_id: DocumentId, reason: Reason) -> None:
    """Withdraw a document from search immediately."""
    settings = rt.get_settings()
    service = rt.get_service(settings)
    record = service.block_document(document_id, rt.principal(settings), _reason(reason))
    rt.out(f"Blocked {record.id} ({record.filename}); status is now {record.status.value}.")
    rt.out(f"Run `kestrel docs affected {record.id}` to list past answers that cited it.")


@docs_app.command("unblock")
@rt.handle_errors
def docs_unblock(document_id: DocumentId, reason: Reason) -> None:
    """Restore a blocked document to the status its manifest gives it."""
    settings = rt.get_settings()
    service = rt.get_service(settings)
    record = service.unblock_document(document_id, rt.principal(settings), _reason(reason))
    rt.out(f"Unblocked {record.id} ({record.filename}); status is now {record.status.value}.")


@docs_app.command("affected")
@rt.handle_errors
def docs_affected(document_id: DocumentId) -> None:
    """Past answers that cited a document, most recent first."""
    settings = rt.get_settings()
    service = rt.get_service(settings)
    records = service.affected_answers(document_id, rt.principal(settings))
    if not records:
        rt.out("No past answers cited this document.")
        return
    _print_audit(records)
    rt.out()
    rt.out(f"{len(records)} answer(s) cited {document_id}.")


# ----------------------------------------------------------------------------- audit


def _print_audit(records: list[AuditRecord]) -> None:
    rows = []
    for r in records:
        e = r.entry
        what = e.outcome.value if e.outcome is not None else ""
        if e.error_category is not None:
            what = f"{what} ({e.error_category.value})"
        subject = e.question_text or e.detail or e.document_id or (f"digest {e.question_digest[:12]}" if e.question_digest else "")
        why = e.reason or ("failed: " + ", ".join(e.failed_checks) if e.failed_checks else "")
        rows.append(
            (r.id, r.created_at.strftime("%Y-%m-%d %H:%M:%S"), e.kind, e.bot_id or "", e.channel, e.user, what, e.trace_id,
             subject, why, e.closest or "")
        )
    head = ["ID", "Time", "Kind", "Bot", "Channel", "User", "Outcome", "Trace", "Question / detail", "Why", "Closest section"]
    rt.print_table(head, rows, max_width=50)


@audit_app.command("tail")
@rt.handle_errors
def audit_tail(
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=500, help="How many records (at most 500).")] = 20,
    bot: BotOption = None,
    all_bots: AllBots = False,
) -> None:
    """The most recent audit records you may see."""
    settings = rt.get_settings()
    service = rt.get_service(settings)
    bot_id = None if all_bots else _bot(settings, bot)
    _print_audit(service.audit_tail(rt.principal(settings), limit=limit, bot_id=bot_id))


@audit_app.command("verify")
@rt.handle_errors
def audit_verify() -> None:
    """Check the whole hash chain; exit 1 if any record was edited or removed."""
    settings = rt.get_settings()
    service = rt.get_service(settings)
    result = service.audit_verify(rt.principal(settings))
    if result.ok:
        rt.out(f"Audit chain intact: {result.checked} record(s) checked.")
        return
    where = f" at record {result.first_broken_id}" if result.first_broken_id is not None else ""
    detail = f": {result.detail}" if result.detail else ""
    rt.out(f"Audit chain BROKEN{where}{detail} ({result.checked} record(s) checked).")
    raise typer.Exit(1)


@audit_app.command("prune")
@rt.handle_errors
def audit_prune(bot: BotOption = None, all_bots: AllBots = False) -> None:
    """Remove records past each bot's retention period, keeping the chain verifiable."""
    settings = rt.get_settings()
    service = rt.get_service(settings)
    bot_id = None if all_bots else _bot(settings, bot)
    results = service.audit_prune(rt.principal(settings), bot_id=bot_id)
    total = sum(r.removed for r in results)
    for r in results:
        checkpoint = f"; checkpoint record {r.checkpoint_id}" if r.checkpoint_id is not None else ""
        rt.out(f"Removed {r.removed} record(s){checkpoint}.")
    scope = "every bot" if bot_id is None else bot_id
    rt.out(f"Pruned {total} record(s) past retention for {scope}.")
