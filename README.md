# OBLIGRAPH — Autonomous Contract-to-Execution Obligation Graph Engine

**OBLIGRAPH** is a multi-tenant, predictive compliance monitoring platform that converts signed agreements (`.pdf` and `.docx`) into autonomous, trackable obligation graphs. Clauses are extracted into structured 6-field legal records, correlated against multi-source operational evidence with calibrated trust weights, scored deterministically for compliance risk, governed by a 48-hour counter-evidence dispute window, and sealed into a per-user SHA-256 tamper-evident hash chain.

---

## Key Features

1. **Multi-Tenant Authentication & Account Isolation**
   - Full account lifecycle (`/signup`, `/login`, `/logout`, `/account`) powered by `Flask-Login`, `Werkzeug` password hashing, `Flask-WTF` CSRF protection, and `Flask-Limiter` rate limiting.
   - Strict per-user data isolation (`user_id` ownership across contracts, obligations, dependencies, evidence, disputes, SHA-256 hash-chain ledgers, and engine settings). Accessing another user's resource by ID returns `404`.
2. **Stage 01–02: Contract Ingestion & 6-Field Clause Extraction**
   - Upload `.pdf` or `.docx` agreements (up to 10 MB, saved under `uploads/user_<id>/`) or load the sample battery-materials supply agreement with one click.
   - Extracts each clause into a canonical 6-field schema: **Actor**, **Trigger / Deadline**, **Required Evidence**, **Expected State**, **Violation Condition**, and **Remediation Action**.
   - Works out-of-the-box with `MOCK_LLM=true` (deterministic offline extractor) or live **Google Gemini** / **Anthropic Claude** API keys.
3. **Stage 03: Interactive Dependency Topology Graph**
   - Directed upstream-to-downstream obligation graph (`vis-network`) modeling prerequisite clauses and downstream risk propagation.
4. **Stage 04: Multi-Source Evidence Feeds & Calibrated Trust Weights**
   - Correlates evidence across 3 channels: **IMAP Email** (`0.70` Counterparty Confirmed), **Google Sheets / CSV** (`1.00` Independently Verifiable), and **Manual Upload** (`0.40` Self-Reported).
5. **Stage 05: Deterministic Risk Scoring & 48h Counter-Evidence Dispute Window**
   - Transparent 3-factor risk equation:
     $$\text{Risk} = w_1 \cdot \text{Deviation} + w_2 \cdot \text{TrustDeficit} + w_3 \cdot \text{DeadlineProximity}$$
   - Flagged obligations open a 48-hour dispute window for counterparty rebuttal before irreversible escalation.
6. **Stage 06: Per-User SHA-256 Hash-Chain Escalation Ledger**
   - Each user account has its own cryptographic hash chain anchored at a 256-bit zero Genesis block (`0000...0000`), complete with one-click integrity verification, tamper simulation, and chain repair.
7. **Isolated Natural Language SQL Query Studio**
   - Plain-English to SQL translation executed inside an isolated, per-user in-memory read-only SQLite sandbox (`PRAGMA query_only = ON`) that physically contains only the logged-in user's rows and blocks all DDL/DML and multi-statement queries.

---

## Local Setup & Installation

### 1. Clone & Create Virtual Environment
```bash
git clone <your-repo-url>
cd OBLIGRAPH
python -m venv .venv

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `SECRET_KEY` | `obligraph-dev-secret-key-...` | Secret key for signing session cookies and CSRF tokens. |
| `FLASK_ENV` | `development` | Set to `production` on Render to enforce `Secure` cookies. |
| `DATABASE_URL` | *(Empty -> SQLite)* | PostgreSQL connection string (`postgresql://...`) in production; leave empty locally to use SQLite (`obligraph.db`). |
| `MOCK_LLM` | `true` | Uses deterministic offline extraction and NL-to-SQL when `true`. |
| `LLM_PROVIDER` | `gemini` | `gemini` or `anthropic` when `MOCK_LLM=false`. |
| `GEMINI_API_KEY` | *(Empty)* | Google Gemini API key (optional). |
| `ANTHROPIC_API_KEY` | *(Empty)* | Anthropic Claude API key (optional). |

### 4. Start the Application
```bash
python run.py
```
The server starts at `http://127.0.0.1:5000`.

---

## Testing Multi-Tenant Isolation Locally with Two Accounts

1. Start the server with `python run.py` and open `http://127.0.0.1:5000` in **Browser Window 1** (normal window).
2. Click **Sign Up Free** (`/signup`) and create **Account A** (e.g., `alice@company-a.com` / `password123`).
   - Notice Account A lands on an empty dashboard with two actions: **"Upload your contract"** and **"Load sample battery-materials contract"**.
   - Click **"Load sample battery-materials contract"** to load the 10-obligation Lithium Supply Agreement into Alice's account.
3. Open `http://127.0.0.1:5000` in **Browser Window 2** (Incognito / Private window).
4. Click **Sign Up Free** (`/signup`) and create **Account B** (e.g., `bob@company-b.com` / `password123`).
   - Notice Bob's dashboard is completely empty (`0` contracts, `0` obligations, `0` disputes, `0` ledger blocks).
   - While logged in as Bob, try visiting Alice's obligation URL directly (`http://127.0.0.1:5000/obligations/1`) or contract switcher (`http://127.0.0.1:5000/switch-contract/1`) — OBLIGRAPH returns a `404` page.
   - Open **NL Query Studio** (`/query/`) as Bob and run `SELECT * FROM obligations` — `0 rows` are returned because Bob's SQL sandbox is populated exclusively with Bob's own records.

---

## Running Automated Tests

Run the full `pytest` suite (57 tests across Phases 0–8, including authentication, CSRF/validation, cross-user 404 isolation, and NL SQL sandbox isolation):

```bash
pytest -v
```

---

## Deploying to Render (PostgreSQL + Gunicorn)

See [DEPLOY.md](DEPLOY.md) for step-by-step instructions to deploy OBLIGRAPH on Render using either the one-click `render.yaml` Blueprint or manual PostgreSQL + Web Service configuration.
