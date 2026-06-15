"""Task 1 — paste your Tableau view URLs in DASHBOARDS; each one renders as an embed."""
import streamlit as st
import streamlit.components.v1 as components

MAIN_HEADER = "Task 1 · KPI Dashboards"

DASHBOARDS = [
    {"header": "Visualisation 1", "url": ""},   # ← paste Tableau view URL here
    {"header": "Visualisation 2", "url": ""},
]


def render():
    st.title(MAIN_HEADER)
    for d in DASHBOARDS:
        if not d["url"]:
            continue                              # nothing shows until you add a URL
        st.subheader(d["header"])
        components.html(
            f'<iframe src="{d["url"]}?:embed=y&:showVizHome=no&:toolbar=yes" '
            f'width="100%" height="800" style="border:none"></iframe>',
            height=820, scrolling=True,
        )