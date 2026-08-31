"""
streamlit_app.py — AI Requirements Traceability Engine (v2 — Multi-Tenant)
===========================================================================
Architecture:
  - Auth gate:      unauthenticated users see only Login / Register
  - Project layer:  authenticated users create / resume named projects
  - Pipeline:       parser → embedder → Qdrant (upsert) → Qdrant (query) → UI matrix
  - Persistence:    AnalysisState saved to SQLite, reloaded on project resume

Tabs (post-analysis):
  Tab 1: Traceability Heatmap & Compliance Summary
  Tab 2: Version Drift Analysis
  Tab 3: AI Pull Request Reviewer
  Tab 4: Evaluation Metrics
  Tab 5: Vector DB Inspector (Qdrant health, per-project stats)
"""

import io
import json
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

# ── Module path setup ────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Project imports ──────────────────────────────────────────────────────────
from src.auth import (
    is_authenticated,
    logout_session,
    render_auth_page,
    get_current_user_id,
    assert_project_owner,
)
from src.database import (
    DocType,
    init_db,
    create_project,
    list_projects,
    get_project,
    delete_project,
    save_document,
    get_documents,
    save_analysis_state,
    load_analysis_state,
)
from src.parser import extract_requirements, extract_endpoints
from src.embedder import SemanticEmbedder
from src.similarity import (
    compute_similarity_matrix,
    build_traceability_matrix,
    compute_overall_compliance_score,
    get_compliance_summary,
    get_compliance_category,
)
from src.drift_detector import (
    compute_drift_report,
    get_drifted_pairs,
    get_compliance_kpis,
    classify_severity_color,
    _normalise_endpoint_path,
)
from src.explainer import ComplianceExplainer
from src.pr_reviewer import PRReviewer, SIMULATED_PR_SCENARIOS
from src.evaluator import (
    load_ground_truth,
    generate_evaluation_report,
    get_per_requirement_metrics,
    compute_tfidf_similarity_matrix,
    compute_map,
)
from src.vector_store import VectorStore

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Bootstrap DB ─────────────────────────────────────────────────────────────
init_db()

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Traceability Engine",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

.main-header {
    background: linear-gradient(135deg, #0d1b4e 0%, #1a237e 50%, #283593 100%);
    padding: 1.8rem 2.5rem;
    border-radius: 14px;
    color: white;
    box-shadow: 0 8px 32px rgba(13,27,78,0.35);
    margin-bottom: 1.5rem;
}
.main-header h1 { font-size: 1.7rem; font-weight: 700; margin: 0; }
.main-header .sub { font-size: 0.88rem; opacity: 0.82; margin-top: 0.4rem; }
.main-header .user-pill {
    display: inline-block;
    background: rgba(255,255,255,0.15);
    border-radius: 20px;
    padding: 2px 12px;
    font-size: 0.82rem;
    margin-top: 0.5rem;
}

.project-card {
    background: linear-gradient(145deg, #ffffff, #f8f9ff);
    border: 1px solid #e3e8f8;
    border-radius: 12px;
    padding: 1.2rem 1.5rem;
    margin: 0.5rem 0;
    transition: box-shadow 0.2s ease;
    cursor: pointer;
}
.project-card:hover { box-shadow: 0 4px 20px rgba(26,35,126,0.12); }
.project-card h4 { font-size: 1rem; font-weight: 600; color: #1a237e; margin: 0; }
.project-card .meta { font-size: 0.78rem; color: #6b7280; margin-top: 0.3rem; }

.qdrant-badge {
    display: inline-block;
    background: linear-gradient(90deg, #dc143c, #8b0000);
    color: white;
    border-radius: 6px;
    padding: 2px 10px;
    font-size: 0.75rem;
    font-weight: 600;
}
.sqlite-badge {
    display: inline-block;
    background: linear-gradient(90deg, #0078d7, #005a9e);
    color: white;
    border-radius: 6px;
    padding: 2px 10px;
    font-size: 0.75rem;
    font-weight: 600;
}
section[data-testid="stSidebar"] { background: #f0f4ff; }
</style>
""", unsafe_allow_html=True)

# =============================================================================
# AUTH GATE — show login page if not authenticated
# =============================================================================

if not is_authenticated():
    st.markdown("""
    <div style="text-align:center; margin-bottom:2rem;">
        <h2 style="color:#1a237e;">🔍 AI Requirements Traceability Engine</h2>
        <p style="color:#6b7280;">Semantic compliance verification · Multi-tenant · Persistent</p>
    </div>
    """, unsafe_allow_html=True)
    render_auth_page()
    st.stop()


# =============================================================================
# AUTHENTICATED — initialise shared resources once per session
# =============================================================================

user_id  = get_current_user_id()
username = st.session_state.get("username", "user")

# Lazy-init shared singleton resources
if "embedder" not in st.session_state:
    st.session_state.embedder = None
if "vector_store" not in st.session_state:
    st.session_state.vector_store = None
if "explainer" not in st.session_state:
    st.session_state.explainer = None
if "current_project_id" not in st.session_state:
    st.session_state.current_project_id = None
if "analysis_done" not in st.session_state:
    st.session_state.analysis_done = False


def _get_embedder() -> SemanticEmbedder:
    if st.session_state.embedder is None:
        st.session_state.embedder = SemanticEmbedder()
    return st.session_state.embedder


def _get_vector_store() -> VectorStore:
    if st.session_state.vector_store is None:
        st.session_state.vector_store = VectorStore()
    return st.session_state.vector_store


def _get_explainer() -> ComplianceExplainer:
    if st.session_state.explainer is None:
        st.session_state.explainer = ComplianceExplainer()
    return st.session_state.explainer


# =============================================================================
# HEADER
# =============================================================================

st.markdown(f"""
<div class="main-header">
  <h1>🔍 AI Requirements Traceability Engine</h1>
  <div class="sub">Semantic compliance verification between business requirements and OpenAPI specifications</div>
  <div class="user-pill">👤 {username}</div>
</div>
""", unsafe_allow_html=True)


# =============================================================================
# SIDEBAR — navigation + settings
# =============================================================================

with st.sidebar:
    st.markdown(f"### 👤 {username}")
    if st.button("🚪 Sign Out", use_container_width=True):
        logout_session()
        st.rerun()

    st.markdown("---")
    st.markdown("### 📁 Projects")

    projects = list_projects(user_id)

    if projects:
        project_options = {f"{p.name} (#{p.id})": p.id for p in projects}
        selected_label = st.selectbox(
            "Select project:",
            options=["— Select a project —"] + list(project_options.keys()),
        )
        if selected_label != "— Select a project —":
            selected_project_id = project_options[selected_label]
            if st.session_state.current_project_id != selected_project_id:
                # Reset analysis state when switching projects
                st.session_state.current_project_id = selected_project_id
                st.session_state.analysis_done = False
                for k in ["requirements","endpoints_v1","endpoints_v2","req_embeddings",
                          "ep_embeddings_v1","ep_embeddings_v2","matrix_v1","matrix_v2",
                          "drift_report","drift_kpis","compliance_summary_v1",
                          "ground_truth","eval_report","per_req_metrics"]:
                    st.session_state.pop(k, None)
    else:
        st.info("No projects yet. Create one below ↓")

    st.markdown("---")
    st.markdown("### ➕ New Project")
    with st.form("new_project_form", clear_on_submit=True):
        proj_name = st.text_input("Project name", placeholder="E-Commerce API v2 Audit")
        proj_desc = st.text_area("Description (optional)", height=60, placeholder="Auditing Nexus API v2.0 against REQ baseline...")
        create_btn = st.form_submit_button("Create Project", type="primary", use_container_width=True)
    if create_btn and proj_name.strip():
        new_proj = create_project(user_id=user_id, name=proj_name.strip(), description=proj_desc.strip())
        st.session_state.current_project_id = new_proj.id
        st.session_state.analysis_done = False
        st.success(f"✅ Project '{new_proj.name}' created!")
        st.rerun()

    st.markdown("---")
    st.markdown("### ⚙️ Thresholds")
    sim_threshold   = st.slider("Compliance (green ≥)", 0.0, 1.0, 0.55, 0.05)
    amber_threshold = st.slider("Partial (amber ≥)",    0.0, 1.0, 0.35, 0.05)
    drift_threshold = st.slider("Drift alert (Δ ≤)",   -1.0, 0.0, -0.10, 0.05)

    st.markdown("---")
    enable_drift = st.checkbox("Enable drift analysis (v2 spec)", value=True)

    # Qdrant status
    st.markdown("---")
    st.markdown("### 🔌 Backend Status")
    vs = _get_vector_store()
    if vs.is_available:
        st.success("🟢 Qdrant connected")
    else:
        st.warning("🟡 Qdrant offline — using fallback")
    st.caption("SQLite: `app.db` (active)")


# =============================================================================
# MAIN CONTENT AREA
# =============================================================================

project_id = st.session_state.get("current_project_id")

if project_id is None:
    # ── No project selected — show dashboard ──────────────────────────────────
    st.markdown("## 📊 Your Projects")
    if not projects:
        st.markdown("""
        <div style="text-align:center; padding:4rem; color:#9e9e9e;">
            <div style="font-size:3rem;">📂</div>
            <div style="font-size:1.1rem; margin-top:1rem;">
                No projects yet. Create your first project using the sidebar.
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        cols = st.columns(3)
        for i, proj in enumerate(projects):
            with cols[i % 3]:
                docs = get_documents(proj.id)
                has_analysis = load_analysis_state(proj.id) is not None
                analysis_badge = "✅ Analysis saved" if has_analysis else "⏳ Not yet analysed"
                st.markdown(f"""
                <div class="project-card">
                    <h4>📁 {proj.name}</h4>
                    <div class="meta">
                        Created: {proj.created_at.strftime('%Y-%m-%d')}<br>
                        Documents: {len(docs)} uploaded<br>
                        {analysis_badge}
                    </div>
                </div>
                """, unsafe_allow_html=True)
                if st.button(f"Open Project →", key=f"open_{proj.id}"):
                    st.session_state.current_project_id = proj.id
                    st.session_state.analysis_done = False
                    st.rerun()
    st.stop()


# ── Validate project ownership (AuthZ) ───────────────────────────────────────
try:
    assert_project_owner(project_id, user_id)
except PermissionError as e:
    st.error(str(e))
    st.session_state.current_project_id = None
    st.stop()

project = get_project(project_id, user_id)
st.markdown(f"### 📁 Project: **{project.name}**")

# ── Check for cached analysis ──────────────────────────────────────────────
if not st.session_state.analysis_done:
    cached = load_analysis_state(project_id)
    if cached and cached.traceability_matrix:
        with st.expander("💾 Cached analysis found — load or re-run?", expanded=True):
            c1, c2 = st.columns(2)
            with c1:
                if st.button("⚡ Load saved analysis", type="primary", use_container_width=True):
                    # Deserialise matrix from JSON
                    matrix_df = pd.DataFrame(cached.traceability_matrix)
                    st.session_state.matrix_v1 = matrix_df
                    if cached.drift_report:
                        st.session_state.drift_report = pd.DataFrame(cached.drift_report)
                    st.session_state.analysis_done = True
                    st.session_state["_from_cache"] = True
                    st.success(f"✅ Loaded analysis from {cached.computed_at.strftime('%Y-%m-%d %H:%M')}")
                    st.rerun()
            with c2:
                if st.button("🔄 Re-run fresh analysis", use_container_width=True):
                    pass  # fall through to pipeline section below


# =============================================================================
# FILE UPLOAD & ANALYSIS PIPELINE
# =============================================================================

DEFAULT_SRS_PATH = ROOT / "data" / "requirements" / "ecommerce_srs.md"
DEFAULT_V1_PATH  = ROOT / "data" / "api_specs" / "store_v1.json"
DEFAULT_V2_PATH  = ROOT / "data" / "api_specs" / "store_v2.json"
DEFAULT_GT_PATH  = ROOT / "data" / "ground_truth" / "traceability_labels.csv"

UPLOAD_DIR = ROOT / "data" / "_uploads" / str(project_id)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _save_upload(uploaded_file, doc_type: DocType) -> str:
    path = UPLOAD_DIR / uploaded_file.name
    path.write_bytes(uploaded_file.read())
    save_document(project_id, doc_type, str(path), uploaded_file.name)
    return str(path)


with st.expander("📂 Input Files", expanded=not st.session_state.analysis_done):
    use_defaults = st.checkbox("Use built-in synthetic dataset (recommended for demo)", value=True)

    if use_defaults:
        srs_path = str(DEFAULT_SRS_PATH)
        v1_path  = str(DEFAULT_V1_PATH)
        v2_path  = str(DEFAULT_V2_PATH) if enable_drift else None
        st.info("✅ Using E-Commerce SRS + store_v1.json + store_v2.json (drift injected)")
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            srs_file = st.file_uploader("SRS Markdown (.md / .txt)", type=["md","txt"])
            srs_path = _save_upload(srs_file, DocType.srs) if srs_file else None
        with col2:
            v1_file = st.file_uploader("OpenAPI v1 (.json/.yaml)", type=["json","yaml","yml"])
            v1_path = _save_upload(v1_file, DocType.api_v1) if v1_file else None
        with col3:
            if enable_drift:
                v2_file = st.file_uploader("OpenAPI v2 (.json/.yaml)", type=["json","yaml","yml"])
                v2_path = _save_upload(v2_file, DocType.api_v2) if v2_file else None
            else:
                v2_path = None

analyse_btn = st.button(
    "🚀 Run Compliance Analysis",
    type="primary",
    use_container_width=True,
    disabled=st.session_state.analysis_done,
)

if analyse_btn:
    if not srs_path or not v1_path:
        st.error("Please provide both an SRS file and OpenAPI v1 spec.")
    else:
        t_start = time.time()
        with st.spinner("⚙️ Running multi-tenant compliance analysis pipeline..."):
            try:
                # Step 1: Parse
                with st.status("📄 Parsing artefacts...", expanded=False) as s:
                    requirements = extract_requirements(srs_path)
                    endpoints_v1 = extract_endpoints(v1_path)
                    st.session_state.requirements = requirements
                    st.session_state.endpoints_v1 = endpoints_v1
                    s.update(label=f"✅ {len(requirements)} requirements, {len(endpoints_v1)} endpoints", state="complete")

                # Step 2: Embed
                with st.status("🧠 Generating semantic embeddings...", expanded=False) as s:
                    embedder = _get_embedder()
                    req_embeddings  = embedder.encode_requirements(requirements)
                    ep_embeddings_v1 = embedder.encode_endpoints(endpoints_v1)
                    st.session_state.req_embeddings   = req_embeddings
                    st.session_state.ep_embeddings_v1 = ep_embeddings_v1
                    s.update(label=f"✅ Embeddings: {req_embeddings.shape}, {ep_embeddings_v1.shape}", state="complete")

                # Step 3: Upsert to Qdrant
                with st.status("📤 Uploading vectors to Qdrant...", expanded=False) as s:
                    vs = _get_vector_store()
                    if vs.is_available:
                        vs.ensure_collection(dim=embedder.dim)
                        n_req = vs.upsert_requirements(requirements, req_embeddings, user_id, project_id)
                        n_ep  = vs.upsert_endpoints(endpoints_v1, ep_embeddings_v1, user_id, project_id)
                        s.update(label=f"✅ Qdrant: {n_req} req + {n_ep} endpoint vectors upserted", state="complete")
                    else:
                        s.update(label="⚠️ Qdrant offline — using NumPy fallback", state="complete")

                # Step 4: Build traceability matrix (Qdrant or NumPy fallback)
                with st.status("📊 Building traceability matrix...", expanded=False) as s:
                    if vs.is_available:
                        matrix_df_v1 = vs.build_traceability_matrix(
                            requirements, req_embeddings, user_id, project_id, endpoints_v1
                        )
                    else:
                        # NumPy fallback when Qdrant is offline
                        from src.similarity import compute_similarity_matrix
                        sim = compute_similarity_matrix(req_embeddings, ep_embeddings_v1)
                        matrix_df_v1 = build_traceability_matrix(
                            requirements, endpoints_v1, sim, sim_threshold, amber_threshold
                        )

                    summary_v1 = get_compliance_summary(matrix_df_v1, requirements, sim_threshold, amber_threshold)
                    st.session_state.matrix_v1 = matrix_df_v1
                    st.session_state.compliance_summary_v1 = summary_v1
                    latency = time.time() - t_start
                    s.update(label=f"✅ Matrix computed in {latency:.2f}s", state="complete")

                # Step 5: Drift analysis
                if enable_drift and v2_path:
                    with st.status("🔄 Running drift detection...", expanded=False) as s:
                        endpoints_v2    = extract_endpoints(v2_path)
                        ep_embeddings_v2 = embedder.encode_endpoints(endpoints_v2)
                        st.session_state.endpoints_v2    = endpoints_v2
                        st.session_state.ep_embeddings_v2 = ep_embeddings_v2

                        if vs.is_available:
                            vs.upsert_endpoints(endpoints_v2, ep_embeddings_v2, user_id, project_id)

                        # Build v2 matrix (NumPy — v2 endpoints already embedded)
                        from src.similarity import compute_similarity_matrix
                        sim_v2   = compute_similarity_matrix(req_embeddings, ep_embeddings_v2)
                        matrix_df_v2 = build_traceability_matrix(
                            requirements, endpoints_v2, sim_v2, sim_threshold, amber_threshold
                        )
                        drift_df = compute_drift_report(
                            matrix_df_v1, matrix_df_v2,
                            requirements, endpoints_v1, endpoints_v2,
                            drift_threshold=drift_threshold,
                        )
                        drift_kpis = get_compliance_kpis(matrix_df_v1, matrix_df_v2, drift_df, threshold=sim_threshold)
                        st.session_state.matrix_v2   = matrix_df_v2
                        st.session_state.drift_report = drift_df
                        st.session_state.drift_kpis  = drift_kpis
                        n_drifted = int(drift_df["drifted"].sum())
                        s.update(label=f"✅ Drift: {n_drifted} regression(s) flagged", state="complete")

                # Step 6: Persist to SQLite
                with st.status("💾 Saving analysis to database...", expanded=False) as s:
                    save_analysis_state(
                        project_id=project_id,
                        matrix_df=matrix_df_v1,
                        drift_df=st.session_state.get("drift_report"),
                        summary_df=summary_v1,
                        embedding_model=embedder.model_name,
                    )
                    s.update(label="✅ Analysis state persisted to SQLite", state="complete")

                # Step 7: Evaluation
                if DEFAULT_GT_PATH.exists():
                    with st.status("📈 Evaluating metrics...", expanded=False) as s:
                        gt = load_ground_truth(str(DEFAULT_GT_PATH))
                        eval_report    = generate_evaluation_report(requirements, endpoints_v1, matrix_df_v1, gt, threshold=sim_threshold)
                        per_req_metrics = get_per_requirement_metrics(matrix_df_v1, gt, requirements, threshold=sim_threshold)
                        st.session_state.ground_truth    = gt
                        st.session_state.eval_report     = eval_report
                        st.session_state.per_req_metrics = per_req_metrics
                        f1 = eval_report.loc["Transformer (all-mpnet-base-v2)", "F1-Score"]
                        s.update(label=f"✅ Transformer F1={f1:.4f}", state="complete")

                st.session_state.analysis_done = True
                st.session_state["_from_cache"] = False
                total = time.time() - t_start
                st.success(f"✅ Analysis complete in **{total:.2f}s** — {len(requirements)} req × {len(endpoints_v1)} endpoints")

            except Exception as e:
                st.error(f"❌ Pipeline error: {e}")
                logger.exception("Pipeline failed")


# =============================================================================
# TABS (only shown after analysis)
# =============================================================================

if not st.session_state.analysis_done:
    st.info("👆 Configure files and click **Run Compliance Analysis** to begin.")
    st.stop()

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Traceability Matrix",
    "📈 Drift Analysis",
    "🤖 PR Reviewer",
    "🎯 Evaluation",
    "🔌 Vector DB",
])


# =============================================================================
# TAB 1: Traceability Heatmap
# =============================================================================

with tab1:
    matrix_df  = st.session_state.get("matrix_v1")
    requirements = st.session_state.get("requirements", [])
    summary    = st.session_state.get("compliance_summary_v1")

    if matrix_df is None:
        st.info("Run analysis to see the traceability matrix.")
        st.stop()

    overall = compute_overall_compliance_score(matrix_df, threshold=sim_threshold)
    from_cache = st.session_state.get("_from_cache", False)
    if from_cache:
        st.caption("📌 Loaded from saved analysis")

    n_compliant = int((summary["compliance_category"] == "Compliant").sum()) if summary is not None else 0
    n_partial   = int((summary["compliance_category"] == "Partial").sum())   if summary is not None else 0
    n_noncomp   = int((summary["compliance_category"] == "Non-Compliant").sum()) if summary is not None else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Overall Compliance", f"{overall:.1%}")
    c2.metric("✅ Compliant",     n_compliant)
    c3.metric("🟡 Partial",       n_partial)
    c4.metric("🔴 Non-Compliant", n_noncomp)

    st.markdown("---")
    st.markdown("### 🗂️ Requirement × Endpoint Heatmap")

    z_vals   = matrix_df.values
    x_labels = [ep.split("/")[-1] if "/" in ep else ep for ep in matrix_df.columns.tolist()]
    y_labels = matrix_df.index.tolist()
    text_vals = [[f"{v:.2f}" for v in row] for row in z_vals]

    fig = go.Figure(data=go.Heatmap(
        z=z_vals, x=x_labels, y=y_labels,
        text=text_vals, texttemplate="%{text}", textfont={"size": 9},
        colorscale=[
            [0.00, "#D32F2F"], [0.35, "#F9A825"],
            [0.55, "#66BB6A"], [1.00, "#1B5E20"],
        ],
        zmin=0.0, zmax=1.0,
        hovertemplate="<b>Req:</b> %{y}<br><b>Endpoint:</b> %{x}<br><b>Score:</b> %{z:.4f}<extra></extra>",
        colorbar=dict(
            title="Cosine Sim",
            tickvals=[0.0, 0.35, 0.55, 1.0],
            ticktext=["0.0<br>Non-Compliant", "0.35<br>Partial", "0.55<br>Compliant", "1.0"],
        ),
    ))
    fig.update_layout(
        title="Requirement-to-Endpoint Traceability Matrix",
        xaxis=dict(title="API Endpoints", tickangle=-45, tickfont=dict(size=9)),
        yaxis=dict(title="Requirements", tickfont=dict(size=10)),
        height=520, margin=dict(l=60, r=40, t=60, b=120),
    )
    st.plotly_chart(fig, use_container_width=True)

    if summary is not None:
        st.markdown("### 📋 Compliance Summary")
        display = summary[["requirement_title","max_score","best_endpoint","compliance_category"]].copy()
        display.columns = ["Title","Best Score","Best Endpoint","Status"]
        st.dataframe(display, use_container_width=True)

    buf = io.StringIO()
    matrix_df.round(4).to_csv(buf)
    st.download_button("⬇️ Download Matrix CSV", buf.getvalue(), "traceability_matrix.csv", "text/csv")


# =============================================================================
# TAB 2: Drift Analysis
# =============================================================================

with tab2:
    drift_df  = st.session_state.get("drift_report")
    drift_kpis = st.session_state.get("drift_kpis")
    matrix_v2 = st.session_state.get("matrix_v2")
    matrix_v1 = st.session_state.get("matrix_v1")

    if drift_df is None or drift_kpis is None:
        st.warning("Enable **drift analysis** in the sidebar and re-run to see this tab.")
        st.stop()

    c1, c2, c3, c4 = st.columns(4)
    delta_pct = (drift_kpis["compliance_v2"] - drift_kpis["compliance_v1"]) * 100
    c1.metric("Compliance v1.0", f"{drift_kpis['compliance_v1']:.1%}")
    c2.metric("Compliance v2.0", f"{drift_kpis['compliance_v2']:.1%}", delta=f"{delta_pct:+.1f}%", delta_color="inverse")
    c3.metric("Drifted Pairs",   drift_kpis["n_drifted_pairs"])
    c4.metric("Critical Drift",  drift_kpis["n_critical"])

    st.markdown("---")

    versions = ["v1.0 (Baseline)", "v2.0 (Current)"]
    scores   = [drift_kpis["compliance_v1"], drift_kpis["compliance_v2"]]
    fig_line = go.Figure(go.Scatter(
        x=versions, y=scores, mode="lines+markers+text",
        text=[f"{s:.1%}" for s in scores], textposition="top center",
        line=dict(color="#2196F3", width=3),
        marker=dict(size=14, color=["#388E3C","#D32F2F"], line=dict(width=2, color="#fff")),
    ))
    fig_line.add_shape(type="line", x0=0, x1=1, y0=sim_threshold, y1=sim_threshold,
                       line=dict(color="#FF9800", dash="dash", width=1.5))
    fig_line.update_layout(
        title="Compliance Score: v1.0 → v2.0",
        yaxis=dict(tickformat=".0%", range=[0,1.1], title="Compliance Score"),
        height=340, margin=dict(l=40,r=40,t=50,b=40),
    )
    st.plotly_chart(fig_line, use_container_width=True)

    st.markdown("### 🔍 Drifted Pairs")
    drifted_only = get_drifted_pairs(drift_df, min_severity="Minor Drift")
    if drifted_only.empty:
        st.success("✅ No drifted pairs above Minor Drift threshold.")
    else:
        display_drift = drifted_only[["requirement_id","endpoint_id","score_v1","score_v2","delta","severity","endpoint_status"]].copy()
        display_drift.columns = ["Requirement","Endpoint","Score v1.0","Score v2.0","Delta","Severity","Status"]
        st.dataframe(
            display_drift.style.format({"Score v1.0":"{:.4f}","Score v2.0":"{:.4f}","Delta":"{:.4f}"}),
            use_container_width=True
        )

    st.markdown("### 🧠 AI Root Cause Analysis")
    explainer   = _get_explainer()
    endpoints_v1 = st.session_state.get("endpoints_v1",[])
    endpoints_v2 = st.session_state.get("endpoints_v2",[])
    ep_text_v1  = {e["id"]: e["text"] for e in endpoints_v1}
    ep_text_v2  = {e["id"]: e["text"] for e in endpoints_v2}
    requirements = st.session_state.get("requirements",[])

    critical = drifted_only[drifted_only["severity"].isin(["Critical Drift","Major Drift"])]
    if critical.empty:
        st.info("No Major/Critical drift violations found.")
    else:
        for _, row in critical.iterrows():
            icon = "🔴" if "Critical" in row["severity"] else "🟠"
            with st.expander(f"{icon} [{row['severity']}] {row['requirement_id']} ↔ {row['endpoint_id']}", expanded=True):
                cols = st.columns(3)
                cols[0].metric("v1.0 Score", f"{row['score_v1']:.4f}")
                cols[1].metric("v2.0 Score", f"{row['score_v2']:.4f}")
                cols[2].metric("Delta",      f"{row['delta']:.4f}")
                req_text = next((r["text"] for r in requirements if r["id"] == row["requirement_id"]), "")
                st.markdown(f"**Requirement:** {req_text}")
                norm_ep = _normalise_endpoint_path(row["endpoint_id"])
                v1_text = next((t for eid,t in ep_text_v1.items() if _normalise_endpoint_path(eid)==norm_ep), "")
                v2_text = next((t for eid,t in ep_text_v2.items() if _normalise_endpoint_path(eid)==norm_ep), "")
                key = f"expl_{row['requirement_id']}_{row['endpoint_id']}"
                if st.button("🤖 Generate AI Explanation", key=f"btn_{key}"):
                    with st.spinner("Calling LLM..."):
                        st.session_state[key] = explainer.explain_drift(
                            {"requirement_id":row["requirement_id"],"requirement_text":req_text,
                             "endpoint_id":row["endpoint_id"],"score_v1":row["score_v1"],
                             "score_v2":row["score_v2"],"delta":row["delta"],"severity":row["severity"]},
                            v1_text, v2_text,
                        )
                if key in st.session_state:
                    st.markdown(st.session_state[key])


# =============================================================================
# TAB 3: PR Reviewer
# =============================================================================

with tab3:
    st.markdown("### 🤖 Simulated GitHub PR Compliance Review")
    st.caption("Simulates a CI/CD bot evaluating API changes against your requirement baseline.")

    embedder     = _get_embedder()
    requirements = st.session_state.get("requirements",[])
    req_embeddings = st.session_state.get("req_embeddings")

    if req_embeddings is None:
        st.info("Run the analysis pipeline first.")
        st.stop()

    scenario_key = st.selectbox(
        "Select a PR scenario:",
        list(SIMULATED_PR_SCENARIOS.keys()),
        format_func=lambda k: f"{SIMULATED_PR_SCENARIOS[k]['pr_title']}  (#{SIMULATED_PR_SCENARIOS[k]['pr_number']})",
    )
    if st.button("▶️ Run PR Compliance Check", type="primary"):
        pr_reviewer = PRReviewer(
            embedder=embedder,
            explainer=_get_explainer(),
            compliance_threshold=sim_threshold,
            drift_threshold=drift_threshold,
        )
        with st.spinner("Checking PR compliance..."):
            comment, violations = pr_reviewer.run_pr_review(scenario_key, requirements, req_embeddings)
            st.session_state["pr_comment"]    = comment
            st.session_state["pr_violations"] = violations
            st.session_state["pr_scenario"]   = scenario_key

    if st.session_state.get("pr_scenario") == scenario_key and "pr_comment" in st.session_state:
        violations = st.session_state["pr_violations"]
        if violations:
            st.error(f"🚫 PR BLOCKED — {len(violations)} violation(s) detected")
            for v in violations:
                icon = "🔴" if "Critical" in v["severity"] else "🟠"
                with st.expander(f"{icon} {v['requirement_id']} × {v['endpoint_id']} — {v['severity']}", expanded=True):
                    c1,c2,c3 = st.columns(3)
                    c1.metric("Score v1.0", f"{v['score_v1']:.4f}")
                    c2.metric("Score v2.0", f"{v['score_v2']:.4f}")
                    c3.metric("Delta",      f"{v['delta']:.4f}")
                    st.markdown(f"**Change:** {v.get('endpoint_change_description','')}")
                    st.markdown(v.get("explanation","_No explanation._"))
        else:
            st.success("✅ PR APPROVED — No compliance violations detected")

        st.markdown("---")
        st.markdown("#### 📋 Full GitHub Review Comment")
        st.code(st.session_state["pr_comment"], language="markdown")
        st.download_button("⬇️ Download Review (.md)", st.session_state["pr_comment"],
                           "pr_review.md", "text/markdown")


# =============================================================================
# TAB 4: Evaluation Metrics
# =============================================================================

with tab4:
    eval_report     = st.session_state.get("eval_report")
    per_req_metrics = st.session_state.get("per_req_metrics")

    if eval_report is None:
        st.info("Evaluation data unavailable. Run analysis with the default dataset.")
        st.stop()

    t_row = eval_report.loc["Transformer (all-mpnet-base-v2)"]
    b_row = eval_report.loc["TF-IDF Baseline (bigrams)"]

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Transformer F1",        f"{t_row['F1-Score']:.4f}", delta=f"+{t_row['F1-Score']-b_row['F1-Score']:.4f} vs TF-IDF")
    c2.metric("Transformer MAP",       f"{t_row['MAP']:.4f}",      delta=f"+{t_row['MAP']-b_row['MAP']:.4f} vs TF-IDF")
    c3.metric("Transformer Precision", f"{t_row['Precision']:.4f}")
    c4.metric("Transformer Recall",    f"{t_row['Recall']:.4f}")

    st.markdown("---")
    st.dataframe(eval_report.style.highlight_max(axis=0, color="#e8f5e9"), use_container_width=True)

    metrics = ["Precision","Recall","F1-Score","MAP"]
    fig_bar = go.Figure(data=[
        go.Bar(name="Transformer (ours)", x=metrics, y=[float(t_row[m]) for m in metrics],
               marker_color="#1976D2", text=[f"{float(t_row[m]):.4f}" for m in metrics], textposition="outside"),
        go.Bar(name="TF-IDF Baseline",   x=metrics, y=[float(b_row[m]) for m in metrics],
               marker_color="#90A4AE", text=[f"{float(b_row[m]):.4f}" for m in metrics], textposition="outside"),
    ])
    fig_bar.update_layout(
        barmode="group", title="Transformer vs TF-IDF IR Metrics",
        yaxis=dict(range=[0,1.15], title="Score"),
        height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig_bar, use_container_width=True)

    if per_req_metrics is not None:
        st.markdown("#### 📋 Per-Requirement Breakdown")
        st.dataframe(
            per_req_metrics.style.format({
                "precision":"{:.4f}","recall":"{:.4f}",
                "f1":"{:.4f}","avg_precision":"{:.4f}",
            }).background_gradient(subset=["f1"], cmap="RdYlGn"),
            use_container_width=True,
        )


# =============================================================================
# TAB 5: Vector DB Inspector
# =============================================================================

with tab5:
    st.markdown("### 🔌 Qdrant Vector Database Inspector")
    st.caption("Multi-tenant vector store status. All queries are isolated by user_id + project_id.")

    vs = _get_vector_store()

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("#### Qdrant Connection")
        if vs.is_available:
            st.success(f"🟢 Connected — `{vs.url}`")
            stats = vs.get_collection_stats()
            st.json(stats)
        else:
            st.error("🔴 Qdrant is not reachable.")
            st.markdown("""
            **Start Qdrant with Docker:**
            ```bash
            docker compose up -d
            ```
            Then re-run the analysis pipeline to push vectors.
            """)

    with col_b:
        st.markdown("#### Current Project Vectors")
        if vs.is_available and st.session_state.analysis_done:
            endpoints_stored = vs.scroll_endpoints(user_id=user_id, project_id=project_id)
            st.metric("Endpoints stored in Qdrant", len(endpoints_stored))
            if endpoints_stored:
                st.dataframe(
                    pd.DataFrame([
                        {"item_id": p.get("item_id",""), "method": p.get("method",""), "path": p.get("path","")}
                        for p in endpoints_stored
                    ]),
                    use_container_width=True,
                )
        else:
            st.info("Run the analysis pipeline to push vectors to Qdrant.")

    st.markdown("---")
    st.markdown("#### 🏗️ Multi-Tenancy Architecture")
    st.markdown("""
    | Layer | Isolation mechanism |
    |---|---|
    | **Vector DB (Qdrant)** | Every point carries `user_id` + `project_id` payload. All `search()` and `scroll()` calls include a mandatory `Filter(must=[user_id==X, project_id==Y])`. |
    | **Relational DB (SQLite)** | All `Project` / `Document` / `AnalysisState` queries include `.where(Project.user_id == user_id)`. |
    | **Session State** | `user_id` injected at login and validated on every route via `assert_project_owner()`. |
    | **File Uploads** | Stored under `data/_uploads/{project_id}/` — scoped by project. |
    """)

    st.markdown("---")
    st.markdown("#### ⚠️ Danger Zone")
    with st.expander("Delete this project and all its data"):
        st.warning("This will delete the project, all documents, analysis state, and Qdrant vectors. This cannot be undone.")
        if st.button("🗑️ Delete Project Permanently", type="primary"):
            if vs.is_available:
                vs.delete_project_vectors(user_id=user_id, project_id=project_id)
            delete_project(project_id=project_id, user_id=user_id)
            st.session_state.current_project_id = None
            st.session_state.analysis_done = False
            st.success("Project deleted.")
            st.rerun()


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.markdown("---")
st.markdown(
    "<div style='text-align:center;color:#9e9e9e;font-size:0.78rem;'>"
    "AI Requirements Traceability Engine v2.0 · "
    "<span class='qdrant-badge'>Qdrant</span> · "
    "<span class='sqlite-badge'>SQLite</span> · "
    "sentence-transformers · Streamlit"
    "</div>",
    unsafe_allow_html=True,
)
