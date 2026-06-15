"""
Data Assignment — Streamlit app.   Run:  streamlit run app.py

  Page 1 · Task 1 · KPI Dashboards   — edit MAIN_HEADER + DASHBOARDS below
  Page 2 · Task 2 · KPI Verifier     — the full tool (lives in verifier.py)
"""
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Sattva — Data Assignment", page_icon="📊", layout="wide")

import verifier

# ───────── PAGE 1 — edit these two things ───────── #
MAIN_HEADER = ":green[Dashboards]"

DASHBOARDS = [
    {"header": ":blue[Monthly Update Compliance]", "url": "https://public.tableau.com/views/KPIUpdate-Compliance/MonthlyEntry-Presence?:language=en-US&:sid=&:redirect=auth&:display_count=n&:origin=viz_share_link"},   # ← paste the Tableau view URL here
    {"header": ":blue[KPI-wise Completion]", "url": "https://public.tableau.com/views/CompletionbyKPI/CompletionbyKPI?:language=en-US&:sid=&:redirect=auth&:display_count=n&:origin=viz_share_link"},
    {"header": ":blue[Project-wise Completion]", "url": "https://public.tableau.com/views/CompletionbyProject/CompletionbyProject?:language=en-US&:sid=&:redirect=auth&:display_count=n&:origin=viz_share_link"},
    {"header": ":blue[Location-wise Completion]", "url": "https://public.tableau.com/views/CompletionbyLocation/CompletionbyLocation?:language=en-US&:sid=&:redirect=auth&:display_count=n&:origin=viz_share_link"},
]


def task1():
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
        st.divider()
# ─────────────────────────────────────────────────── #

nav = st.navigation([
    st.Page(task1,           title="Task 1 · KPI Dashboards",
            icon=":material/insights:", url_path="task1", default=True),
    st.Page(verifier.render, title="Task 2 · KPI Evidence Verifier",
            icon=":material/search:", url_path="task2"),
])
nav.run()