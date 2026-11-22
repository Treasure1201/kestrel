"""The sidebar on every page: app name, assistant picker, navigation, model, user and audit status."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import streamlit as st
import yaml

from kestrel.contracts import AssistantService, BotSummary, Principal

from kestrel_app import runtime
from kestrel_app.components import chips, model_locality, plural, section_label

APP_NAME = "Kestrel Assistant"


def tested_models(config_dir: Path) -> list[str]:
    """`<provider>:<model>` names listed under `tested:` in `config/models.yaml`; empty if absent."""
    path = config_dir / "models.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, yaml.YAMLError):
        return []
    tested = data.get("tested") if isinstance(data, dict) else None
    if not isinstance(tested, dict):
        return []
    return [f"{provider}:{model}" for provider, models in tested.items() if isinstance(models, list) for model in models]


def active_model(bot: BotSummary) -> str:
    """The model override chosen on the Ask page, or the bot's default."""
    override = st.session_state.get("model_override", {}).get(bot.id)
    return override or bot.model


def _bot_picker(svc: AssistantService, who: Principal, bots: list[BotSummary]) -> BotSummary | None:
    section_label("Assistant")
    if not bots:
        st.caption("No assistants are available to your roles.")
        return None
    ids = [b.id for b in bots]
    current = st.session_state.get("bot_id")
    default_id = runtime.settings().default_bot
    index = ids.index(current) if current in ids else (ids.index(default_id) if default_id in ids else 0)
    names = {b.id: b.name for b in bots}
    choice = st.selectbox(
        "Assistant", ids, index=index, format_func=lambda i: names[i], label_visibility="collapsed", key="bot_picker"
    )
    st.session_state["bot_id"] = choice
    bot = next(b for b in bots if b.id == choice)
    docs = runtime.attempt(runtime.memo, f"docs:{bot.id}", lambda: svc.list_documents(bot.id, who), where=st.sidebar)
    chips([bot.classification, plural(len(docs), "document") if docs is not None else "documents unknown"])
    if bot.description:
        st.caption(bot.description)
    return bot


def _audit_status(svc: AssistantService, who: Principal) -> None:
    section_label("Audit trail")
    if not runtime.is_operator(who):
        st.caption("Chain status is shown to operators only.")
        return
    stamp_key = "audit_checked_at"
    result = runtime.attempt(runtime.memo, "audit_verify", lambda: svc.audit_verify(who), where=st.sidebar)
    if result is None:
        return
    st.session_state.setdefault(stamp_key, datetime.now().strftime("%H:%M"))
    if result.ok:
        st.badge("Chain intact", color="blue", icon=":material/link:")
    else:
        where = f" at record {result.first_broken_id}" if result.first_broken_id is not None else ""
        st.badge(f"Chain broken{where}", color="red", icon=":material/link_off:")
    st.caption(f"{plural(result.checked, 'record')} checked · {st.session_state[stamp_key]}")


def render(svc: AssistantService | None, pages: Sequence[st.Page]) -> BotSummary | None:
    """Draw the sidebar and return the selected bot (None when there is none)."""
    who = runtime.principal()
    with st.sidebar:
        st.markdown(f"### :material/visibility: {APP_NAME}")
        bot: BotSummary | None = None
        if svc is not None:
            bots = runtime.attempt(runtime.memo, "bots", lambda: svc.list_bots(who), default=[], where=st.sidebar) or []
            bot = _bot_picker(svc, who, bots)
        for page in pages:
            st.page_link(page)
        st.divider()
        if bot is not None:
            model = active_model(bot)
            section_label("Model")
            st.markdown(f"`{model}`")
            tested = model in tested_models(runtime.settings().config_dir)
            chips([model_locality(model), "tested" if tested else "not on the tested list"])
        st.caption(f"Signed in as {who.user}. Roles: {', '.join(sorted(who.roles)) or 'none'}.")
        if svc is not None:
            _audit_status(svc, who)
    return bot
