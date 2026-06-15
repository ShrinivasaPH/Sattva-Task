"""
Task 1 — KPI Reporting Completion dashboards (Tableau embeds).

This page embeds the Tableau visualisations built for Task 1. To wire in your own
views, either:
  • edit the DASHBOARDS list below with your Tableau Public (or Tableau Cloud) view
    URLs, or
  • use the "Preview any Tableau link" box at the bottom to paste a URL on the fly.

Getting a view URL on Tableau Public: open the published viz → Share → copy the link,
or use the URL in the address bar of the form
    https://public.tableau.com/views/<Workbook>/<Dashboard>
"""
import streamlit as st
import streamlit.components.v1 as components

# --------------------------------------------------------------------------- #
#  EDIT ME — your Task 1 Tableau views. Leave url="" to show a placeholder.
# --------------------------------------------------------------------------- #
DASHBOARDS = [
    {
        "title": "Portfolio & Project Completion",
        "desc": "Overall reporting completion across the portfolio, broken down by project.",
        "url": "",        # e.g. "https://public.tableau.com/views/SattvaKPI/Portfolio"
        "height": 720,
    },
    {
        "title": "KPI-level Completion",
        "desc": "Completion rate for each KPI, sortable to surface the worst-reported indicators.",
        "url": "",
        "height": 720,
    },
    {
        "title": "Completion Matrix (heatmap)",
        "desc": "Project × KPI heatmap of months reported vs the 12 expected — the at-a-glance view.",
        "url": "",
        "height": 760,
    },
    {
        "title": "At-risk Watchlist",
        "desc": "Reporting units below threshold, ranked — where follow-up is needed.",
        "url": "",
        "height": 680,
    },
]

# Headline numbers from the Task 1 completion analysis (for context above the views).
HEADLINE = [
    ("Portfolio completion", "35.8%", "984 of 2,748 expected entries"),
    ("Units at risk", "182 / 229", "below threshold"),
    ("Best project", "Project 4", "≈55% completion"),
    ("Periods expected", "12", "monthly, FY 2025-26"),
]


def embed_tableau(url: str, height: int = 760, toolbar: str = "bottom"):
    """Embed a Tableau view via the Tableau Embedding API v3 web component."""
    src = url.strip()
    html = f"""
        <div style="width:100%;font-family:sans-serif">
          <script type="module"
            src="https://public.tableau.com/javascripts/api/tableau.embedding.3.latest.min.js"></script>
          <tableau-viz src="{src}" width="100%" height="{height}"
                       hide-tabs toolbar="{toolbar}"></tableau-viz>
        </div>
    """
    components.html(html, height=height + 40, scrolling=True)


def _placeholder(d):
    st.info(
        f"**No link wired up yet for “{d['title']}”.**\n\n"
        "Add your Tableau Public view URL to the `DASHBOARDS` list in `dashboards.py` "
        "(field `url`), or paste it into *Preview any Tableau link* below to test it live."
    )


def render():
    st.title("Task 1 · KPI Reporting Completion")
    st.caption("Monitoring which KPIs are reported across the 12 monthly periods, by project, "
               "KPI, and reporting unit. The Tableau views below are the interactive deliverables.")

    # headline context
    cols = st.columns(len(HEADLINE))
    for c, (label, val, sub) in zip(cols, HEADLINE):
        c.metric(label, val)
        c.caption(sub)
    st.caption("Figures from the Task 1 completion analysis (presence-based; denominator = 12 months).")
    st.divider()

    # embedded dashboards — two per row (side-by-side)
    for i in range(0, len(DASHBOARDS), 2):
        cols = st.columns(2)
        for col, d in zip(cols, DASHBOARDS[i:i + 2]):
            with col:
                st.subheader(d["title"])
                st.caption(d["desc"])
                if d.get("url"):
                    embed_tableau(d["url"], height=d.get("height", 760))
                    st.markdown(f"[↗ Open in Tableau]({d['url']})")
                else:
                    _placeholder(d)
        st.divider()

    # live preview for any link
    with st.expander("Preview any Tableau link"):
        url = st.text_input("Tableau view URL",
                            placeholder="https://public.tableau.com/views/<Workbook>/<Dashboard>")
        h = st.slider("Height (px)", 400, 1200, 760, step=20)
        if url:
            embed_tableau(url, height=h)
        else:
            st.caption("Paste a published Tableau Public (or Tableau Cloud) view URL to embed it.")

    with st.expander("How to get your Tableau Public link"):
        st.markdown(
            "1. Publish the workbook to **Tableau Public** (File → Save to Tableau Public).\n"
            "2. Open the published viz, click **Share**, and copy the **Link**, or copy the URL "
            "from the address bar — it looks like "
            "`https://public.tableau.com/views/<Workbook>/<Dashboard>`.\n"
            "3. Paste it into the `DASHBOARDS` list (or the box above). The embed uses Tableau's "
            "Embedding API v3, so the viz must be **public**.\n\n"
            "Tableau **Cloud/Server** views work too, as long as the viewer is authenticated."
        )
