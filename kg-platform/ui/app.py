"""Streamlit interface: ask questions (GraphRAG), explore analytics, visualise the graph, read the benchmark."""
import json
import os
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

API_URL = os.getenv("API_URL", "http://api:8000")
st.set_page_config(page_title="Knowledge Graph Platform", layout="wide")
st.title("AI-powered Knowledge Graph Analytics")

with st.sidebar:
    api_url = st.text_input("API URL", API_URL)
    api_key = st.text_input("API key", os.getenv("API_KEY", ""), type="password")
    st.caption("Relations are extracted automatically: treat them as hypotheses, not verified facts.")


def call(method, path, **kw):
    r = requests.request(method, api_url + path, headers={"X-API-Key": api_key}, timeout=300, **kw)
    if r.status_code >= 400:
        st.error(f"{r.status_code}: {r.text[:300]}")
        return None
    return r.json()


tab_ask, tab_an, tab_graph, tab_bench = st.tabs(["GraphRAG", "Analytics", "Graph view", "Benchmark"])

with tab_ask:
    q = st.text_input("Question", "Which investors have funded companies that partner with Orion Cloud?")
    hops = st.slider("Retrieval hops", 1, 3, 2)
    if st.button("Ask") and q:
        with st.spinner("Retrieving subgraph and generating answer..."):
            out = call("POST", "/ask", json={"question": q, "hops": hops})
        if out:
            st.subheader("Answer")
            st.write(out["answer"])
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Latency (s)", out["latency_s"])
            c2.metric("Facts retrieved", out["n_facts"])
            c3.metric("Tokens in/out", f"{out['usage']['in_tokens']}/{out['usage']['out_tokens']}")
            c4.metric("Cost (USD)", f"{out['usage']['cost_usd']:.4f}")
            st.caption(f"Entities linked in the question: {', '.join(out['seeds']) or 'none (global question)'}")
            if out["entities"]:
                st.write("**Entities in the answer:**", ", ".join(out["entities"]))
            st.subheader("Provenance")
            st.dataframe(pd.DataFrame(out["provenance"]), use_container_width=True)

with tab_an:
    st.subheader("Graph statistics")
    s = call("GET", "/stats")
    if s:
        a, b, c = st.columns(3)
        a.metric("Nodes", s["total_nodes"]); b.metric("Relations", s["total_relations"]); c.metric("Documents", s["documents"])
        a.dataframe(pd.DataFrame(s["nodes_by_type"])); b.dataframe(pd.DataFrame(s["relations_by_type"]))
    algo = st.selectbox("Algorithm", ["pagerank", "communities", "betweenness", "degree"])
    if st.button("Run algorithm"):
        res = call("GET", f"/analytics/{algo}", params={"limit": 15})
        if res:
            st.caption(f"engine: {res['engine']}")
            st.dataframe(pd.DataFrame(res["rows"]), use_container_width=True)
    st.subheader("Shortest path")
    c1, c2 = st.columns(2)
    src, dst = c1.text_input("From", "Karim Benali"), c2.text_input("To", "Sophie Laurent")
    if st.button("Find path"):
        res = call("GET", "/path", params={"source": src, "target": dst})
        if res:
            hops = " ".join(f"({n}) -[{r}]->" for n, r in zip(res["nodes"], res["relations"])) + f" ({res['nodes'][-1]})"
            st.code(hops)

with tab_graph:
    name = st.text_input("Entity", "Atlas Dynamics")
    depth = st.slider("Depth", 1, 3, 1)
    if st.button("Draw"):
        res = call("GET", "/subgraph", params={"name": name, "hops": depth})
        if res and res["edges"]:
            from pyvis.network import Network
            colors = {"Person": "#e07a5f", "Organization": "#3d85c6", "Location": "#81b29a", "Product": "#f2cc8f", "Technology": "#9d84b7"}
            net = Network(height="600px", width="100%", directed=True)
            for e in res["edges"]:
                net.add_node(e["source"], label=e["source"], color=colors.get(e["source_type"], "#999"))
                net.add_node(e["target"], label=e["target"], color=colors.get(e["target_type"], "#999"))
                net.add_edge(e["source"], e["target"], label=e["relation"])
            components.html(net.generate_html(), height=620, scrolling=True)
        elif res is not None:
            st.info("No relations found for this entity.")

with tab_bench:
    f = Path("/app/reports/benchmark.md")
    if f.exists():
        st.markdown(f.read_text())
    else:
        st.info("No report yet. Run:  docker compose run --rm tools python -m kgp.pipeline eval")
