"""`AssistantService.doctor`: environment checks, end to end. Never includes secret values."""

from __future__ import annotations

import sqlite3

from kestrel.contracts import DoctorCheck, DoctorReport, SearchScope
from kestrel.errors import KestrelError
from kestrel.llm import check_egress, check_model
from kestrel.retrieval import FastEmbedEmbedder
from kestrel.service.runtime import Runtime
from kestrel.store.schema import SCHEMA_VERSION, schema_version

_KEYS = (("anthropic_api_key", "ANTHROPIC_API_KEY"), ("openai_api_key", "OPENAI_API_KEY"), ("ollama_api_key", "OLLAMA_API_KEY"))


def _database(runtime: Runtime) -> DoctorCheck:
    try:
        with runtime.store.connections.connect() as conn:
            version = schema_version(conn)
    except (sqlite3.Error, KestrelError, OSError) as exc:
        return DoctorCheck(name="database", status="fail", detail=f"cannot read {runtime.store.path} ({type(exc).__name__})", fix="check KESTREL_DB in .env")
    if version != SCHEMA_VERSION:
        return DoctorCheck(name="database", status="fail", detail=f"schema version {version}, expected {SCHEMA_VERSION}", fix="move the old database aside and run: kestrel ingest")
    return DoctorCheck(name="database", status="ok", detail=f"{runtime.store.path} (schema {version})")


def _vectors(runtime: Runtime) -> DoctorCheck:
    if runtime.store.vectors.available:
        return DoctorCheck(name="vector extension", status="ok", detail="sqlite-vec loads")
    return DoctorCheck(name="vector extension", status="warn", detail="sqlite-vec cannot be loaded; search is keyword-only", fix="uv sync (reinstalls sqlite-vec)")


def _embedder(runtime: Runtime) -> DoctorCheck:
    name = runtime.embedder.model_name
    if runtime.embedder_injected:
        return DoctorCheck(name="embedding model", status="ok", detail=f"{name} (supplied by the caller)")
    try:
        FastEmbedEmbedder(name, local_files_only=True).load()
    except KestrelError:
        return DoctorCheck(name="embedding model", status="warn", detail=f"{name} is not cached; it is downloaded on first use",
                           fix="run kestrel ingest once with network access")
    return DoctorCheck(name="embedding model", status="ok", detail=f"{name} is cached")


def _keys(runtime: Runtime) -> DoctorCheck:
    present = [env for field, env in _KEYS if runtime.settings.key_present(field)]
    missing = [env for field, env in _KEYS if not runtime.settings.key_present(field)]
    detail = f"set: {', '.join(present) or 'none'}; not set: {', '.join(missing) or 'none'}"
    return DoctorCheck(name="provider keys", status="ok", detail=detail)


def _bots(runtime: Runtime) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    try:
        ids = runtime.registry.ids()
    except KestrelError as exc:
        return [DoctorCheck(name="bots", status="fail", detail=exc.user_message, fix="set KESTREL_BOTS_DIR in .env")]
    models: set[str] = set()
    for bot_id in ids:
        try:
            bot = runtime.registry.get(bot_id)
            specs = runtime.registry.mcp_specs(bot)
        except KestrelError as exc:
            checks.append(DoctorCheck(name=f"bot {bot_id}", status="fail", detail=exc.user_message, fix=f"fix bots/{bot_id}/bot.yaml"))
            continue
        checks.append(DoctorCheck(name=f"bot {bot_id}", status="ok", detail=f"loads; model {bot.model.default}"))
        count = len(runtime.store.documents.eligible_document_ids(SearchScope(collections=bot.knowledge.collections)))
        checks.append(DoctorCheck(
            name=f"documents {bot_id}", status="ok" if count else "warn",
            detail=f"{count} searchable document(s)", fix=None if count else f"kestrel ingest --bot {bot_id}",
        ))
        try:
            check_egress(bot, bot.model.default, specs, runtime.settings)
        except KestrelError as exc:
            checks.append(DoctorCheck(name=f"egress {bot_id}", status="fail", detail=exc.user_message, fix="change KESTREL_MODEL or the bot's privacy classification"))
        models.add(bot.model.default)
    for ref in sorted(models):
        check = check_model(ref, runtime.settings)
        checks.append(check.model_copy(update={"name": f"model {ref}"}))
    return checks


def _audit(runtime: Runtime) -> DoctorCheck:
    try:
        result = runtime.store.audit.verify()
    except (sqlite3.Error, KestrelError) as exc:
        return DoctorCheck(name="audit chain", status="fail", detail=f"cannot verify ({type(exc).__name__})")
    if result.ok:
        return DoctorCheck(name="audit chain", status="ok", detail=f"{result.checked} record(s) verified")
    return DoctorCheck(name="audit chain", status="fail", detail=f"broken at record {result.first_broken_id}: {result.detail or ''}".strip(),
                       fix="investigate the audit table; restore the database from a backup")


def doctor(runtime: Runtime) -> DoctorReport:
    """Run every check; each failure carries the exact fix."""
    checks = [_database(runtime), _vectors(runtime), _embedder(runtime), _keys(runtime)]
    checks += _bots(runtime)
    checks.append(_audit(runtime))
    return DoctorReport(checks=tuple(checks))
