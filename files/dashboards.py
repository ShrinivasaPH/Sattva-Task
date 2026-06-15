"""Task 1 dashboards — hardcode your Tableau view URLs in DASHBOARDS below."""
import streamlit as st
import streamlit.components.v1 as components

# ───────────────────────────────────────────────────────────── #
#  EDIT: main header + your Tableau visualisations
# ───────────────────────────────────────────────────────────── #
MAIN_HEADER = "Task 1 · KPI Dashboards"

DASHBOARDS = [
    {"header": "Visualisation 1", "https://public.tableau.com/views/CompletionbyKPI/CompletionbyKPI?:language=en-US&publish=yes&:sid=&:redirect=auth&:display_count=n&:origin=viz_share_link": ""},   # ← paste the Tableau view URL here
    {"header": "Visualisation 2", "url": ""},
    {"header": "Visualisation 3", "url": ""},
    {"header": "Visualisation 4", "url": ""},
]

EMBED_HEIGHT = 720          # px, per visualisation
# ───────────────────────────────────────────────────────────── #


def embed_tableau(url: str, height: int = EMBED_HEIGHT):
    components.html(
        f'''<script type="module"
              src="https://public.tableau.com/views/CompletionbyKPI/CompletionbyKPI?:language=en-US&publish=yes&:sid=&:display_count=n&:origin=viz_share_link"></script>
            <tableau-viz src="{url.strip()}" width="100%" height="{height}"
                         hide-tabs toolbar="bottom"></tableau-viz>''',
        height=height + 30, scrolling=True,
    )



def render():
    st.title(MAIN_HEADER)
    for i in range(0, len(DASHBOARDS), 2):
        cols = st.columns(2)
        for col, d in zip(cols, DASHBOARDS[i:i + 2]):
            with col:
                st.subheader(d["header"])
                height = d.get("height", EMBED_HEIGHT)
                if d.get("url"):
                    embed_tableau(d["url"], height)
                else:
                    st.container(height=height, border=True)   # empty link space to fill
        st.divider()