"""Kestrel Assistant: the Streamlit front end over `AssistantService`.

    uv run streamlit run app/streamlit_app.py

Three pages (Ask; Documents and findings; Quality and audit) share one sidebar. Every
read and action goes through the synchronous service, exactly as the CLI does, on the
`ui` channel with the local principal from settings.
"""

from __future__ import annotations

import sys
from pathlib import Path

_APP_DIR = str(Path(__file__).resolve().parent)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import streamlit as st  # noqa: E402

from kestrel_app import runtime, sidebar  # noqa: E402
from kestrel_app.components import inject_css  # noqa: E402

st.set_page_config(page_title="Kestrel Assistant", page_icon=":material/visibility:", layout="wide")
inject_css()

PAGES = [
    st.Page("views/ask.py", title="Ask", icon=":material/chat:", url_path="ask", default=True),
    st.Page("views/documents.py", title="Documents and findings", icon=":material/description:", url_path="documents"),
    st.Page("views/quality.py", title="Quality and audit", icon=":material/monitoring:", url_path="quality"),
]

navigation = st.navigation(PAGES, position="hidden")
service = runtime.attempt(runtime.service)
st.session_state["_bot"] = sidebar.render(service, PAGES, navigation)
navigation.run()
