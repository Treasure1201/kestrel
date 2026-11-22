"""Shared code for the Streamlit app: runtime seam, pure metrics, result files and components.

The pages reach the application only through `AssistantService`. `metrics`, `results`
and `flags` import no Streamlit, so they are unit-tested on their own.
"""
