"""Ask: a chat with the selected assistant, and the evidence for the selected answer beside it."""

from __future__ import annotations

import streamlit as st

from kestrel.contracts import MAX_QUESTION_CHARS, AskResult, BotSummary

from kestrel_app import answer_card, evidence, runtime
from kestrel_app.components import chips, model_locality
from kestrel_app.flags import append_flag, flag_record
from kestrel_app.sidebar import tested_models

PRIVACY_NOTE = (
    "Do not enter names, ID numbers, contact details or medical details. Anything that looks like "
    "personal information is removed before the question is sent, or the question is blocked, "
    "depending on this assistant's policy."
)


def _flag(result: AskResult, user: str) -> None:
    flagged: set[str] = st.session_state.setdefault("flags", set())
    flagged.add(result.trace_id)
    try:
        append_flag(runtime.results_root(), flag_record(result, user))
        st.toast("Flagged for review (kept locally in the results/flags folder).", icon=":material/flag:")
    except OSError as exc:
        st.toast(f"Flag kept for this session only; the flags file could not be written ({type(exc).__name__}).")


def _select(bot_id: str, index: int) -> None:
    st.session_state.setdefault("selected", {})[bot_id] = index


def _set_model(bot_id: str) -> None:
    st.session_state.setdefault("model_override", {})[bot_id] = st.session_state.get(f"model-{bot_id}") or None


def _model_picker(bot: BotSummary) -> str | None:
    """Optional model override, limited to the tested models (on-device only for confidential bots)."""
    options = [m for m in tested_models(runtime.settings().config_dir) if m != bot.model]
    if bot.classification == "confidential":
        options = [m for m in options if model_locality(m) == "on-device"]
    current = st.session_state.get("model_override", {}).get(bot.id)
    if not options:
        return None
    choices = ["", *options]
    st.selectbox(
        "Model for the next question",
        choices,
        index=choices.index(current) if current in choices else 0,
        format_func=lambda m: m or f"Assistant default ({bot.model})",
        key=f"model-{bot.id}",
        on_change=_set_model,
        args=(bot.id,),
        help="Tested models only. The service still applies the assistant's provider and data rules.",
    )
    return current


def _empty_state(bot: BotSummary) -> None:
    st.info(
        f"Ask {bot.name} a question. Answers come only from its approved documents, with a quote for "
        "every claim, or a plain statement that the documents do not say.",
        icon=":material/chat:",
    )


def _ask_example(question: str) -> None:
    st.session_state["pending_question"] = question


def _examples(bot: BotSummary) -> None:
    """The bot's example questions as buttons just above the chat input; a click asks the question."""
    if not bot.examples:
        return
    st.caption("Try an example question:")
    with st.container(horizontal=True):
        for n, example in enumerate(bot.examples):
            st.button(example, key=f"ex-{bot.id}-{n}", on_click=_ask_example, args=(example,), type="secondary")


ctx = runtime.context()
if ctx is None:
    st.stop()
svc, who, bot = ctx

history: list[AskResult] = st.session_state.setdefault("chats", {}).setdefault(bot.id, [])
selected_map: dict[str, int] = st.session_state.setdefault("selected", {})
flags: set[str] = st.session_state.setdefault("flags", set())

question = st.chat_input("Ask a question about the approved documents", max_chars=MAX_QUESTION_CHARS, key=f"ask-{bot.id}")
question = question or st.session_state.pop("pending_question", None)

left, right = st.columns([3, 2], gap="large")
with left:
    st.title(bot.name)
    chips(["Answers only from approved documents", "Current editions unless a question names another"])
    override = _model_picker(bot)

    if question and question.strip():
        with st.spinner("Searching the approved documents and checking the answer..."):
            result = runtime.attempt(svc.ask, bot.id, question, who, model=override)
        if result is not None:
            history.append(result)
            selected_map[bot.id] = len(history) - 1

    if not history:
        _empty_state(bot)
    chosen = min(selected_map.get(bot.id, len(history) - 1), len(history) - 1)
    for index, result in enumerate(history):
        answer_card.user_turn(result)
        answer_card.answer_turn(
            result,
            selected=index == chosen,
            flagged=result.trace_id in flags,
            on_select=lambda i=index: _select(bot.id, i),
            on_flag=lambda r=result: _flag(r, who.user),
        )
    _examples(bot)
    st.caption(PRIVACY_NOTE)

with right:
    evidence.render(history[chosen] if history else None)
