"""Ask: a two-pane workspace. The conversation on the left, the evidence for the selected answer on the right.

Each pane is its own scroll area sized to the window, so the page itself does not
scroll while chatting. The conversation sticks to the newest message; the evidence
pane is a new scroll area per selected answer, so it always opens at the top.
"""

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
# A fixed height turns a container into a scroll area; the style sheet then sizes it to the window.
PANE_HEIGHT = 560


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
        label_visibility="collapsed",
        width=300,
    )
    return current


def _ask_example(question: str) -> None:
    st.session_state["pending_question"] = question


def _empty_conversation(bot: BotSummary) -> None:
    """The start of a conversation: what the assistant does, and its example questions as buttons."""
    st.info(
        f"Ask {bot.name} a question. Answers come only from its approved documents, with a quote for "
        "every claim, or a plain statement that the documents do not say.",
        icon=":material/chat:",
    )
    if bot.examples:
        st.caption("Try an example question:")
        with st.container(horizontal=True, gap="xsmall"):
            for n, example in enumerate(bot.examples):
                st.button(example, key=f"ex-{bot.id}-{n}", on_click=_ask_example, args=(example,), type="secondary")


@st.fragment
def workspace(bot: BotSummary, override: str | None) -> None:
    """The two panes. A fragment, so choosing an answer or asking reruns only this part of the page."""
    svc, who = runtime.service(), runtime.principal()
    history: list[AskResult] = st.session_state.setdefault("chats", {}).setdefault(bot.id, [])
    selected_map: dict[str, int] = st.session_state.setdefault("selected", {})
    flags: set[str] = st.session_state.setdefault("flags", set())

    with st.container(key="ask-grid"):
        left, right = st.columns([3, 2], gap="medium")
    with left:
        conversation = st.container(key="conv", height=PANE_HEIGHT, autoscroll=True, border=False)
        with st.container(key="ask-input"):
            typed = st.chat_input(
                "Ask a question about the approved documents", max_chars=MAX_QUESTION_CHARS, key=f"ask-{bot.id}"
            )
        with st.container(key="ask-privacy"):
            st.caption(PRIVACY_NOTE)
    question = typed or st.session_state.pop("pending_question", None)

    asking = bool(question and question.strip())
    chosen = min(selected_map.get(bot.id, len(history) - 1), len(history) - 1)

    def turn(index: int, result: AskResult) -> None:
        answer_card.user_turn(result)
        answer_card.answer_turn(
            result,
            index=index,
            selected=index == chosen,
            flagged=result.trace_id in flags,
            on_select=lambda: _select(bot.id, index),
            on_flag=lambda: _flag(result, who.user),
        )

    with conversation:
        if not history and not asking:
            _empty_conversation(bot)
        if asking:
            chosen = -2  # no earlier card is marked while the new answer is on its way
        for index, result in enumerate(history):
            turn(index, result)
        if asking:
            with st.spinner("Searching the approved documents and checking the answer..."):
                answer = runtime.attempt(svc.ask, bot.id, question, who, model=override)
            if answer is not None:
                history.append(answer)
                selected_map[bot.id] = chosen = len(history) - 1
                turn(chosen, answer)
        chosen = min(selected_map.get(bot.id, len(history) - 1), len(history) - 1)

    with right, st.container(key=f"evpane-{max(chosen, 0)}", height=PANE_HEIGHT + 90, autoscroll=False, border=False):
        evidence.render(history[chosen] if history else None)


ctx = runtime.context()
if ctx is None:
    st.stop()
_svc, _who, current_bot = ctx

with st.container(key="ask-head", horizontal=True, vertical_alignment="center", gap="small"):
    st.title(current_bot.name)
    chips(["Approved documents only", "Current editions unless a question names another"])
    st.space("stretch")
    model_override = _model_picker(current_bot)

workspace(current_bot, model_override)
