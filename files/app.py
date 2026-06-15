"""
Data Assignment — multi-page Streamlit app.

  • Task 2 · KPI Evidence Verifier  (NER-based on-demand verification)  -> verifier.py
  • Task 1 · KPI Dashboards         (embedded Tableau visualisations)   -> dashboards.py

Run:  streamlit run app.py
"""
import streamlit as st

st.set_page_config(page_title="Sattva — Data Assignment", page_icon="📊", layout="wide")

import verifier
import dashboards

nav = st.navigation([
    st.Page(dashboards.render, title="Task 1 · KPI Dashboards",
            icon=":material/insights:", url_path="task1-dashboards", default=True),
    st.Page(verifier.render,   title="Task 2 · KPI Evidence Verifier",
            icon=":material/search:", url_path="task2-verifier"),
])
nav.run()
