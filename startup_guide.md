# Startup Guide — AI Requirements Traceability Engine v2.0

> **B.Tech Capstone Project**  
> Enterprise full-stack: FastAPI + React 18 + Three.js + Qdrant Vector DB + Google OAuth 2.0

---

## 1. Prerequisites

| Requirement | Minimum Version | Check Command |
|---|---|---|
| **Docker Desktop** | 24.x | `docker --version` |
| **Node.js** | 18.x LTS | `node --version` |
| **Python** | 3.10+ | `python --version` |
| **npm** | 9.x | `npm --version` |

---

## 2. Project Structure

```
AI-Assisted-Requirements-Traceability-Specification-Analysis/
├── backend/
│   ├── app/
│   │   ├── main.py            ← FastAPI app entry point
│   │   ├── database.py        ← SQLAlchemy async models
│   │   ├── schemas.py         ← Pydantic v2 schemas
│   │   ├── core/
│   │   │   ├── config.py      ← Settings from .env
│   │   │   └── security.py    ← JWT + get_current_user
│   │   ├── routers/
│   │   │   ├── auth.py        ← Google OAuth endpoint
│   │   │   ├── projects.py    ← Project CRUD + file upload
│   │   │   └── analysis.py    ← Pipeline + matrix + drift + PR bot
│   │   └── services/
│   │       ├── vector_store.py← Qdrant multi-tenant client
│   │       └── pipeline.py    ← Full analysis orchestrator
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx            ← React Router v6 routes
│   │   ├── context/AuthContext.jsx
│   │   ├── services/api.js    ← Axios + JWT interceptor
│   │   ├── components/
│   │   │   ├── Sidebar.jsx
│   │   │   └── Traceability3DGraph.jsx ← react-force-graph-3d
│   │   └── pages/
│   │       ├── LoginPage.jsx
│   │       ├── DashboardPage.jsx
│   │       ├── TraceabilityPage.jsx
│   │       ├── DriftPage.jsx
│   │       ├── PRBotPage.jsx
│   │       └── BenchmarksPage.jsx
│   ├── package.json
│   ├── vite.config.js
│   └── tailwind.config.js
├── src/                       ← Original NLP pipeline modules (reused)
│   ├── parser.py
│   ├── embedder.py
│   ├── similarity.py
│   ├── drift_detector.py
│   ├── explainer.py
│   ├── pr_reviewer.py
│   └── evaluator.py
├── data/
│   ├── requirements/ecommerce_srs.md
│   ├── api_specs/store_v1.json + store_v2.json
│   └── ground_truth/traceability_labels.csv
└── docker-compose.yml
```

---

## 3. Environment Configuration

### 3a. Backend — `backend/.env`

```bash
# Copy and fill in your values
cp backend/.env.example backend/.env
```

Minimum required variables:

```env
SECRET_KEY=your-random-32-char-string-here   # python -c "import secrets;print(secrets.token_urlsafe(48))"
GOOGLE_CLIENT_ID=your-project.apps.googleusercontent.com
OPENAI_API_KEY=sk-...                          # PRIMARY LLM provider
# GROQ_API_KEY=gsk_...                         # optional fallback only
```

> **How to get GOOGLE_CLIENT_ID (and avoid the 403 / redirect-loop):**
> 1. Go to [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials)
> 2. Create **OAuth 2.0 Client ID → Web application**
> 3. Under **Authorised JavaScript origins**, add BOTH:
>    `http://localhost:5173` **and** `http://127.0.0.1:5173`
> 4. Under **Authorised redirect URIs**, add `http://localhost:5173`
>    (the frontend uses the Google Identity Services popup flow — no backend redirect URI is needed,
>    but adding the origin is mandatory)
> 5. Open **APIs & Services → OAuth consent screen**. If **Publishing status = Testing**,
>    scroll to **Test users** and add the exact Google account email you will log in with.
> 6. Copy the Client ID into **both** `backend/.env` (`GOOGLE_CLIENT_ID`) and
>    `frontend/.env.local` (`VITE_GOOGLE_CLIENT_ID`) — they must be identical.
> 7. Always open the app at **`http://localhost:5173`**, never `http://127.0.0.1:5173`
>    (unless you added that origin too). Google's `origin_mismatch` check is exact-string.

### 3b. Frontend — `frontend/.env.local`

```bash
cp frontend/.env.local.example frontend/.env.local
```

```env
VITE_GOOGLE_CLIENT_ID=your-project.apps.googleusercontent.com
```

---

## 4. Launch Sequence

Open **3 terminal tabs** in the project root.

### Terminal 1 — Qdrant (Docker)

```powershell
# Qdrant is already running if you see it via:
docker ps

# If not running, start it:
docker compose up -d

# Verify health (HTTP port is 6338, set in docker-compose.yml):
curl http://localhost:6338/readyz
# Expected: HTTP 200 "all shards are ready"
curl http://localhost:6338
# Expected: {"title":"qdrant - vector search engine","version":"..."}
```

### Terminal 2 — FastAPI Backend

```powershell
# Navigate to backend directory context
cd D:\AI-Assisted-Requirements-Traceability-Specification-Analysis\backend

# Install dependencies
pip install -r requirements.txt

# Start FastAPI (binding to 0.0.0.0 fixes Node.js proxy connection issues)
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# API Docs: http://localhost:8000/api/docs
# Health:   http://localhost:8000/api/health
```

### Terminal 3 — React Frontend

```powershell
cd D:\AI-Assisted-Requirements-Traceability-Specification-Analysis\frontend

# Install npm packages (first time only)
npm install

# Copy and edit .env.local
cp .env.local.example .env.local
# Edit VITE_GOOGLE_CLIENT_ID

# Start Vite dev server
npm run dev

# Open: http://localhost:5173
```

---

## 5. End-to-End Testing Workflow

### Step 1: Verify backend

```bash
curl http://localhost:8000/api/health
```
Should return `{"status":"ok","qdrant":{"status":"green",...}}`

### Step 2: Login

1. Open [http://localhost:5173](http://localhost:5173)
2. Click **Sign in with Google** → authenticate with your Google account
3. You should be redirected to the **Dashboard**

### Step 3: Create a project

1. Click **New Project** in the dashboard
2. Name: `E-Commerce Demo`
3. Click **Create Project** → you are redirected to the project view

### Step 4: Run analysis with built-in demo data

1. No file upload needed — click **Run Analysis** on the Traceability page
2. The pipeline will:
   - Parse 10 requirements + 20 endpoints from the built-in SRS + OpenAPI specs
   - Embed with `all-mpnet-base-v2` (768-dim)
   - Upsert vectors into Qdrant (collection: `requirements_traceability`)
   - Build the N×M similarity matrix
   - Run drift detection (v1 → v2)
3. Wait ~30–60 seconds on first run (model loads once)

### Step 5: Explore the 3D Graph

1. Click **Traceability** → **3D Graph** view
2. Rotate/zoom with mouse
3. Click a **blue sphere** (requirement) to highlight edges
4. Switch to **Split** view to see heatmap + 3D simultaneously

### Step 6: View Drift Analysis

1. Navigate to **Drift Analysis**
2. See the v1.0 → v2.0 compliance timeline
3. Click any drifted pair card to fetch **LLM root-cause explanation**
   (requires `OPENAI_API_KEY` in `backend/.env`; falls back to `GROQ_API_KEY` if set)

### Step 7: Run PR Bot

1. Navigate to **PR Bot**
2. Select scenario: *"Remove idempotency field"*
3. Click **Run PR Review** → bot detects REQ-04 violation
4. Download the GitHub-format review comment

### Step 8: View Benchmarks

1. Navigate to **Benchmarks**
2. View Transformer vs TF-IDF comparison (F1, Precision, Recall, MAP)

---

## 6. API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/auth/google` | Exchange Google ID token for JWT |
| `GET`  | `/api/auth/me` | Current user profile |
| `POST` | `/api/projects` | Create project |
| `GET`  | `/api/projects` | List user projects |
| `POST` | `/api/projects/{id}/upload` | Upload SRS / API spec |
| `POST` | `/api/projects/{id}/analyze` | Run full pipeline |
| `GET`  | `/api/projects/{id}/matrix` | Traceability matrix + 3D graph |
| `GET`  | `/api/projects/{id}/drift` | Drift delta report |
| `POST` | `/api/projects/{id}/pr-review` | Simulate PR review |
| `GET`  | `/api/projects/{id}/benchmark` | IR evaluation metrics |
| `GET`  | `/api/projects/{id}/explain/{req_id}` | LLM explanation for a req |

Full interactive docs: **[http://localhost:8000/api/docs](http://localhost:8000/api/docs)**

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `streamlit: not recognized` | Use `python -m streamlit run ...` |
| `uvicorn: not recognized` | Use `python -m uvicorn app.main:app --reload --port 8000` from inside `backend/` |
| Qdrant port 6338 already in use | An existing Qdrant container is running — fine, it will be reused |
| **Google button won't load / `Error 403: origin_mismatch`** | The browser URL origin must be in the OAuth client's **Authorised JavaScript origins**. Add both `http://localhost:5173` and `http://127.0.0.1:5173`, then open the app via `http://localhost:5173`. |
| **Login succeeds then bounces back to `/login`** | Almost always `GOOGLE_CLIENT_ID` is missing/mismatched in `backend/.env`, or your email isn't a **Test user** on the OAuth consent screen. The `/api/auth/google` call is returning 401. Check the backend terminal + browser Network tab for the exact error. `VITE_GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_ID` must be byte-identical. |
| `Token has wrong audience …, expected one of ['']` | The backend loaded an **empty** `GOOGLE_CLIENT_ID` — it wasn't reading `backend/.env`. `config.py` now resolves `backend/.env` by absolute path, so just **restart uvicorn**. Verify with: `cd backend && python -c "from app.core.config import settings; print(settings.GOOGLE_CLIENT_ID)"` — it must print your client ID, not `''`. You do **not** need a new OAuth client. |
| Login `403: access_denied` | OAuth consent screen is in **Testing** mode and your account is not in **Test users**. Add it, or publish the app. |
| LLM explanation shows "unavailable" | Set a valid `OPENAI_API_KEY` in `backend/.env` (primary). Optionally set `GROQ_API_KEY` as fallback. |
| `ModuleNotFoundError: No module named 'app'` | Run `uvicorn` from inside the `backend/` directory. |
| Frontend 401 redirect loop | Clear `localStorage` in DevTools → re-login. The 401 interceptor no longer force-redirects on `/auth/*` calls, so the real error will now surface as a toast. |

---

*Generated automatically — AI Requirements Traceability Engine v2.0 · B.Tech Capstone*
