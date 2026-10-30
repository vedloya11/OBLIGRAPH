import io
import pytest
from pathlib import Path
from config import TestConfig
from app import create_app
from app.database import get_db_connection
from engine.nl_query import execute_sandboxed_query


@pytest.fixture
def app():
    test_db_path = Path("test_obligraph.db").resolve()
    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except Exception:
            pass

    app = create_app(TestConfig)
    yield app

    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except Exception:
            pass


def test_healthz_endpoint(app):
    """Verify /healthz returns 200 OK without authentication."""
    client = app.test_client()
    res = client.get("/healthz")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"


def test_unauthenticated_redirect_to_login(app):
    """Verify unauthenticated users opening any protected route are redirected to /login."""
    client = app.test_client()

    # Public landing page "/" should render 200 for logged-out users
    res_home = client.get("/")
    assert res_home.status_code == 200
    assert "Sign Up Free" in res_home.get_data(as_text=True)

    protected_routes = [
        "/dashboard",
        "/contracts/",
        "/contracts/upload",
        "/obligations/",
        "/obligations/1",
        "/obligations/graph",
        "/evidence/",
        "/disputes/",
        "/escalations/",
        "/query/",
        "/settings/",
        "/account",
    ]
    for route in protected_routes:
        res = client.get(route, follow_redirects=False)
        assert res.status_code in (301, 302), f"Expected redirect on {route}, got {res.status_code}"
        assert "/login" in res.headers.get("Location", "")


def test_signup_login_logout_and_validation(app):
    """Test user signup, password/email validation, duplicate email rejection, wrong password, login, and logout."""
    client = app.test_client()

    # 1. Short password (< 8 chars) rejected
    res_short = client.post("/signup", data={
        "name": "Alice Smith",
        "email": "alice@example.com",
        "password": "short",
        "confirm_password": "short"
    }, follow_redirects=True)
    assert res_short.status_code in (200, 400)
    assert "at least 8 characters" in res_short.get_data(as_text=True)

    # 2. Invalid email rejected
    res_bad_email = client.post("/signup", data={
        "name": "Alice Smith",
        "email": "not-an-email",
        "password": "password123",
        "confirm_password": "password123"
    }, follow_redirects=True)
    assert res_bad_email.status_code in (200, 400)
    assert "valid email" in res_bad_email.get_data(as_text=True).lower()

    # 3. Valid signup creates account with empty dashboard
    res_signup = client.post("/signup", data={
        "name": "Alice Smith",
        "email": "Alice@Example.com",
        "password": "password123",
        "confirm_password": "password123"
    }, follow_redirects=True)
    assert res_signup.status_code == 200
    html = res_signup.get_data(as_text=True)
    assert "Alice Smith" in html
    assert "Upload your contract" in html
    assert "Load sample battery-materials contract" in html

    # Verify password is stored hashed (never plain text) and email is lowercase
    conn = get_db_connection(TestConfig.DATABASE_PATH)
    user_row = conn.execute("SELECT * FROM users WHERE email = 'alice@example.com'").fetchone()
    conn.close()
    assert user_row is not None
    assert user_row["password_hash"] != "password123"
    assert len(user_row["password_hash"]) > 20

    # Logged-in user visiting "/" is redirected to /dashboard
    res_root = client.get("/", follow_redirects=False)
    assert res_root.status_code in (301, 302)
    assert "/dashboard" in res_root.headers.get("Location", "")

    # 4. Logout
    res_logout = client.get("/logout", follow_redirects=True)
    assert res_logout.status_code == 200

    # 5. Duplicate email rejection (case-insensitive)
    res_dup = client.post("/signup", data={
        "name": "Another Alice",
        "email": "alice@example.com",
        "password": "password456",
        "confirm_password": "password456"
    }, follow_redirects=True)
    assert res_dup.status_code in (200, 400)
    assert "already exists" in res_dup.get_data(as_text=True).lower()

    # 6. Wrong password rejected on login
    res_wrong_pw = client.post("/login", data={
        "email": "alice@example.com",
        "password": "wrongpassword"
    }, follow_redirects=True)
    assert res_wrong_pw.status_code in (200, 401)
    assert "invalid email or password" in res_wrong_pw.get_data(as_text=True).lower()

    # 7. Correct password logs in
    res_login = client.post("/login", data={
        "email": "ALICE@EXAMPLE.COM",
        "password": "password123"
    }, follow_redirects=True)
    assert res_login.status_code == 200
    assert "Obligation Intelligence Dashboard" in res_login.get_data(as_text=True)


def test_account_update_name_and_password(app):
    """Test /account route for updating user name and password."""
    client = app.test_client()
    client.post("/signup", data={
        "name": "Initial Name",
        "email": "user1@example.com",
        "password": "oldpassword123",
        "confirm_password": "oldpassword123"
    }, follow_redirects=True)

    # Change display name
    res_name = client.post("/account", data={
        "action": "update_name",
        "name": "Updated Legal Director"
    }, follow_redirects=True)
    assert res_name.status_code == 200
    assert "Updated Legal Director" in res_name.get_data(as_text=True)

    # Change password
    res_pw = client.post("/account", data={
        "action": "update_password",
        "current_password": "oldpassword123",
        "new_password": "newpassword456",
        "confirm_password": "newpassword456"
    }, follow_redirects=True)
    assert res_pw.status_code == 200
    assert "updated successfully" in res_pw.get_data(as_text=True).lower()

    # Logout and verify new password works while old password fails
    client.get("/logout")
    res_old = client.post("/login", data={"email": "user1@example.com", "password": "oldpassword123"}, follow_redirects=True)
    assert "invalid email or password" in res_old.get_data(as_text=True).lower()

    res_new = client.post("/login", data={"email": "user1@example.com", "password": "newpassword456"}, follow_redirects=True)
    assert "Obligation Intelligence Dashboard" in res_new.get_data(as_text=True)


def test_cross_user_isolation_and_404_enforcement(app):
    """
    Verify strict multi-tenant data isolation:
    User A cannot read or modify User B's contract, obligation, dependency,
    evidence, dispute, hash-chain ledger block, or report.
    """
    client_a = app.test_client()
    client_b = app.test_client()

    # Sign up User A (empty account)
    client_a.post("/signup", data={
        "name": "User A",
        "email": "usera@company-a.com",
        "password": "password123",
        "confirm_password": "password123"
    }, follow_redirects=True)

    # Sign up User B and load sample battery-materials contract into User B's account
    client_b.post("/signup", data={
        "name": "User B",
        "email": "userb@company-b.com",
        "password": "password123",
        "confirm_password": "password123"
    }, follow_redirects=True)

    res_load_b = client_b.post("/contracts/load-sample", follow_redirects=True)
    assert res_load_b.status_code == 200
    assert "Global Lithium" in res_load_b.get_data(as_text=True)

    # Inspect User B's created resource IDs from the database
    conn = get_db_connection(TestConfig.DATABASE_PATH)
    user_b_row = conn.execute("SELECT id FROM users WHERE email = 'userb@company-b.com'").fetchone()
    uid_b = user_b_row["id"]
    b_contract = conn.execute("SELECT id FROM contracts WHERE user_id = ?", (uid_b,)).fetchone()
    b_ob = conn.execute("SELECT id FROM obligations WHERE contract_id = ? ORDER BY id ASC LIMIT 1", (b_contract["id"],)).fetchone()
    b_dep = conn.execute("SELECT id FROM dependencies WHERE upstream_id = ? OR downstream_id = ? LIMIT 1", (b_ob["id"], b_ob["id"])).fetchone()
    b_ev = conn.execute("SELECT id FROM evidence WHERE obligation_id = ? LIMIT 1", (b_ob["id"],)).fetchone()
    b_disp = conn.execute(
        "SELECT d.id FROM disputes d JOIN obligations o ON d.obligation_id = o.id WHERE o.contract_id = ? LIMIT 1",
        (b_contract["id"],)
    ).fetchone()
    b_esc = conn.execute("SELECT id FROM escalations WHERE user_id = ? LIMIT 1", (uid_b,)).fetchone()
    conn.close()

    # 1. User A's dashboard and lists must be completely empty (0 obligations, 0 contracts, 0 blocks)
    res_dash_a = client_a.get("/dashboard")
    html_dash_a = res_dash_a.get_data(as_text=True)
    assert "Global Lithium" not in html_dash_a
    assert "Upload your contract" in html_dash_a

    res_obs_a = client_a.get("/obligations/")
    assert "Clause 1.1" not in res_obs_a.get_data(as_text=True)

    res_ev_a = client_a.get("/evidence/")
    assert "Inbound freight manifest" not in res_ev_a.get_data(as_text=True)

    res_disp_a = client_a.get("/disputes/")
    assert "SGS Minerals" not in res_disp_a.get_data(as_text=True)

    res_esc_a = client_a.get("/escalations/")
    assert "Uncured monthly delivery" not in res_esc_a.get_data(as_text=True)

    # 2. User A accessing User B's resources by ID must return 404
    # Contract switch / filter / report
    assert client_a.get(f"/switch-contract/{b_contract['id']}").status_code == 404
    assert client_a.get(f"/obligations/?contract_id={b_contract['id']}").status_code == 404
    assert client_a.get(f"/obligations/api/graph-data?contract_id={b_contract['id']}").status_code == 404
    assert client_a.get(f"/reports/export?contract_id={b_contract['id']}").status_code == 404

    # Obligation detail & explain API
    assert client_a.get(f"/obligations/{b_ob['id']}").status_code == 404
    assert client_a.get(f"/obligations/api/explain/{b_ob['id']}").status_code == 404

    # Dependency delete & add on User B's obligations
    assert client_a.post(f"/obligations/dependencies/delete/{b_dep['id']}").status_code == 404
    assert client_a.post("/obligations/dependencies/add", data={
        "upstream_id": str(b_ob["id"]),
        "downstream_id": str(b_ob["id"] + 1),
        "note": "Cross-tenant link attempt"
    }).status_code == 404

    # Evidence detail & adding evidence to User B's obligation
    assert client_a.get(f"/evidence/{b_ev['id']}").status_code == 404
    assert client_a.post("/evidence/add", data={
        "obligation_id": str(b_ob["id"]),
        "source": "manual",
        "description": "Malicious cross-user evidence"
    }).status_code == 404

    # Dispute detail, create page, submit, and resolve on User B's resources
    assert client_a.get(f"/disputes/{b_disp['id']}").status_code == 404
    assert client_a.get(f"/disputes/create/{b_ob['id']}").status_code == 404
    assert client_a.post("/disputes/submit", data={
        "obligation_id": str(b_ob["id"]),
        "submitted_by": "Attacker",
        "counter_evidence": "Cross-user dispute attempt"
    }).status_code == 404
    assert client_a.post(f"/disputes/resolve/{b_disp['id']}").status_code == 404

    # Escalation ledger block detail, tamper, and create on User B's resources
    assert client_a.get(f"/escalations/{b_esc['id']}").status_code == 404
    assert client_a.post("/escalations/tamper", data={"block_id": str(b_esc["id"])}).status_code == 404
    assert client_a.post("/escalations/create", data={
        "obligation_id": str(b_ob["id"]),
        "reason": "Cross-user escalation attempt"
    }).status_code == 404

    # 3. Verify User B's hash chain is untouched when User A loads demo data, tampers, or resets
    client_a.post("/settings/reset-demo", follow_redirects=True)
    client_a.post("/escalations/tamper", follow_redirects=True)

    # User B's chain must still be 100% valid!
    res_verify_b = client_b.post("/escalations/verify", headers={"X-Requested-With": "XMLHttpRequest"})
    assert res_verify_b.status_code == 200
    assert res_verify_b.get_json()["is_valid"] is True


def test_nl_query_sandbox_cannot_read_other_users_rows(app):
    """
    Verify the NL Query Studio SQL sandbox physically isolates rows per user:
    User A cannot query User B's contracts, obligations, evidence, disputes, or escalations,
    nor read the users table.
    """
    client_a = app.test_client()
    client_b = app.test_client()

    # User A signs up (empty account)
    client_a.post("/signup", data={
        "name": "User A",
        "email": "sandbox_a@example.com",
        "password": "password123",
        "confirm_password": "password123"
    }, follow_redirects=True)

    # User B signs up and loads the 10-obligation demo dataset
    client_b.post("/signup", data={
        "name": "User B",
        "email": "sandbox_b@example.com",
        "password": "password123",
        "confirm_password": "password123"
    }, follow_redirects=True)
    client_b.post("/contracts/load-sample", follow_redirects=True)

    # User B runs "SELECT * FROM obligations" -> gets 10 rows
    res_b = client_b.post("/query/api", json={"sql": "SELECT id, clause_text FROM obligations"})
    assert res_b.status_code == 200
    data_b = res_b.get_json()
    assert data_b["error"] is None
    assert data_b["row_count"] == 10

    # User A runs "SELECT * FROM obligations" -> MUST get 0 rows (cannot see User B's obligations)
    res_a = client_a.post("/query/api", json={"sql": "SELECT id, clause_text FROM obligations"})
    assert res_a.status_code == 200
    data_a = res_a.get_json()
    assert data_a["error"] is None
    assert data_a["row_count"] == 0
    assert data_a["rows"] == []

    # User A trying to query `users` table directly fails because `users` is not in the sandbox
    res_users = client_a.post("/query/api", json={"sql": "SELECT * FROM users"})
    assert res_users.status_code == 200
    data_users = res_users.get_json()
    assert data_users["error"] is not None
    assert "no such table" in data_users["error"].lower()


def test_file_upload_restrictions_per_user_folder(app):
    """Verify only .pdf and .docx are allowed and files are saved under uploads/user_<id>/."""
    client = app.test_client()
    client.post("/signup", data={
        "name": "Uploader User",
        "email": "uploader@example.com",
        "password": "password123",
        "confirm_password": "password123"
    }, follow_redirects=True)

    # Reject .txt / .exe files
    bad_file = (io.BytesIO(b"malicious content"), "exploit.txt")
    res_bad = client.post(
        "/contracts/upload",
        data={"title": "Bad File", "contract_file": bad_file},
        content_type="multipart/form-data",
        follow_redirects=True
    )
    assert res_bad.status_code == 200
    assert "Invalid file type" in res_bad.get_data(as_text=True)

    # Accept valid .docx from seed/demo_contract.docx and verify per-user folder
    seed_docx_bytes = Path("seed/demo_contract.docx").read_bytes()
    good_file = (io.BytesIO(seed_docx_bytes), "my_agreement.docx")
    res_good = client.post(
        "/contracts/upload",
        data={"title": "My Valid DOCX", "contract_file": good_file},
        content_type="multipart/form-data",
        follow_redirects=True
    )
    assert res_good.status_code == 200
    assert "Review & Validate Extracted Obligations" in res_good.get_data(as_text=True)

    user_upload_dir = Path("uploads") / "user_1"
    assert user_upload_dir.exists()
    uploaded_files = list(user_upload_dir.glob("*_my_agreement.docx"))
    assert len(uploaded_files) >= 1
