# Deploying OBLIGRAPH on Render

This guide walks through deploying **OBLIGRAPH** to [Render](https://render.com) with a managed **PostgreSQL** database.

---

## Option A: One-Click Blueprint Deployment (`render.yaml`)

1. Push this repository to your GitHub or GitLab account.
2. In the [Render Dashboard](https://dashboard.render.com), click **New +** -> **Blueprint**.
3. Connect your repository. Render will automatically detect `render.yaml`, provision the `obligraph-db` PostgreSQL instance, configure environment variables (`SECRET_KEY`, `DATABASE_URL`, `MOCK_LLM=true`, `FLASK_ENV=production`), and deploy the web service.

---

## Option B: Step-by-Step Manual Setup on Render

### Step 1: Create the PostgreSQL Service
1. Log in to the [Render Dashboard](https://dashboard.render.com).
2. Click **New +** -> **PostgreSQL**.
3. Configure the database:
   - **Name**: `obligraph-db`
   - **Database**: `obligraph`
   - **User**: `obligraph`
   - **Region**: Choose the region closest to your users (e.g., Oregon or Frankfurt)
   - **Instance Type**: `Free` (or a paid tier for production persistence)
4. Click **Create Database**.
5. Once provisioned, copy the **Internal Database URL** (starts with `postgresql://` or `postgres://`).

### Step 2: Create the Web Service
1. Click **New +** -> **Web Service** and connect your GitHub/GitLab repository.
2. Configure the service settings:
   - **Name**: `obligraph`
   - **Region**: Same region as `obligraph-db`
   - **Runtime**: `Python 3`
   - **Build Command**:
     ```bash
     pip install -r requirements.txt
     ```
   - **Start Command**:
     ```bash
     gunicorn run:app --workers 1 --threads 4 --bind 0.0.0.0:$PORT
     ```
     *(Using `--workers 1 --threads 4` ensures APScheduler runs a single dispute-expiry background thread without duplicate jobs across workers.)*
   - **Health Check Path**: `/healthz`

### Step 3: Set Environment Variables
In the Web Service **Environment** tab, add the following variables:

| Key | Value | Notes |
| :--- | :--- | :--- |
| `FLASK_ENV` | `production` | Enables secure session cookies (`HttpOnly`, `SameSite=Lax`, `Secure`). |
| `SECRET_KEY` | *(Click "Generate" or paste a 64-char random string)* | Signs session cookies and CSRF tokens. |
| `DATABASE_URL` | *(Paste Internal Database URL from Step 1)* | Connects to Render PostgreSQL (`postgres://` is automatically normalized to `postgresql://`). |
| `MOCK_LLM` | `true` | Production default so clause extraction and NL-to-SQL work out of the box without paid API keys. Set to `false` if providing an API key below. |
| `LLM_PROVIDER` | `gemini` | `gemini` or `anthropic`. |
| `GEMINI_API_KEY` | *(Optional)* | Required only if `MOCK_LLM=false` and `LLM_PROVIDER=gemini`. |
| `ANTHROPIC_API_KEY` | *(Optional)* | Required only if `MOCK_LLM=false` and `LLM_PROVIDER=anthropic`. |

### Step 4: Deploy & Verify
1. Click **Create Web Service**.
2. Render will install dependencies, initialize the PostgreSQL schema automatically on startup (`init_db()`), and verify `/healthz` returns `200 OK`.
3. Open your `<your-service>.onrender.com` URL:
   - The public landing page renders at `/`.
   - Click **Sign Up Free** (`/signup`) to create your first account.
   - New accounts start with an empty, isolated workspace where you can **Upload your contract** (`.pdf` or `.docx`, up to 10 MB) or click **Load sample battery-materials contract** to populate the demo dataset into your account.

