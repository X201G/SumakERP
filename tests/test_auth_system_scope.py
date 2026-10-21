import os
import sqlite3
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module
import database


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    previous_database = database.DATABASE
    previous_testing = app_module.app.config.get("TESTING")
    database.close_postgres_pool()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database.DATABASE = str(tmp_path / "auth_test.db")
    with redirect_stdout(StringIO()):
        database.create_tables()
    connection = database.get_connection()
    try:
        connection.execute("DELETE FROM users")
        for username, password, system in (
            ("system_admin", "distribution-password", "distribution"),
            ("system_admin", "manufacturing-password", "manufacturing"),
        ):
            connection.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, role, system)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    username,
                    generate_password_hash(password),
                    f"{system.title()} Admin",
                    "admin",
                    system,
                ),
            )
        connection.commit()
    finally:
        connection.close()

    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    yield client
    app_module.app.config["TESTING"] = previous_testing
    database.close_postgres_pool()
    database.DATABASE = previous_database


def login(client, system, password, username="system_admin"):
    return client.post(
        "/login",
        data={"username": username, "password": password, "system": system},
    )


def setup_db(tmp_path):
    db_path = tmp_path / "sumak_erp_test.db"
    database.DATABASE = str(db_path)
    database.create_tables()

    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users")
    cursor.execute(
        "INSERT INTO users (username, password_hash, full_name, role, system, is_active) VALUES (?,?,?,?,?,?)",
        ("dist_admin", "dummy_hash", "Distribution Admin", "admin", "distribution", 1),
    )
    cursor.execute(
        "INSERT INTO users (username, password_hash, full_name, role, system, is_active) VALUES (?,?,?,?,?,?)",
        ("mfg_admin", "dummy_hash", "Manufacturing Admin", "admin", "manufacturing", 1),
    )
    conn.commit()
    conn.close()


def test_distribution_users_list_only_shows_distribution_users():
    db_path = "sumak_erp_test_scope.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    database.DATABASE = db_path
    setup_db(__import__("pathlib").Path("."))

    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "dist_admin"
        session["role"] = "admin"
        session["system"] = "distribution"

    response = client.get("/users")

    assert response.status_code == 200
    assert "dist_admin" in response.get_data(as_text=True)
    assert "mfg_admin" not in response.get_data(as_text=True)


def test_distribution_add_user_persists_distribution_system():
    db_path = "sumak_erp_test_scope.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    database.DATABASE = db_path
    setup_db(__import__("pathlib").Path("."))

    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "dist_admin"
        session["role"] = "admin"
        session["system"] = "distribution"

    response = client.post(
        "/users/add",
        data={
            "username": "new_dist_user",
            "full_name": "New Dist User",
            "role": "sales",
            "password": "secret123",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT system FROM users WHERE username = ?", ("new_dist_user",))
    user = cursor.fetchone()
    conn.close()
    assert user is not None
    assert user["system"] == "distribution"


def test_legacy_global_username_constraint_migrates_with_foreign_keys(tmp_path):
    conn = sqlite3.connect(tmp_path / "legacy-users.db")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            full_name TEXT,
            role TEXT NOT NULL DEFAULT 'staff',
            system TEXT NOT NULL DEFAULT 'distribution',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE user_references (
            user_id INTEGER REFERENCES users(id)
        )
    """)
    conn.execute(
        "INSERT INTO users (username, password_hash, system) VALUES (?, ?, ?)",
        ("admin", "hash", "distribution"),
    )
    conn.execute("INSERT INTO user_references (user_id) VALUES (1)")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    database._allow_system_scoped_usernames(conn)

    conn.execute(
        "INSERT INTO users (username, password_hash, system) VALUES (?, ?, ?)",
        ("admin", "delivery-hash", "delivery"),
    )
    assert conn.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'").fetchone()[0] == 2
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute(
        "SELECT user_id FROM user_references"
    ).fetchone()["user_id"] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO users (username, password_hash, system) VALUES (?, ?, ?)",
            ("admin", "duplicate-hash", "delivery"),
        )
    conn.close()


def test_login_credentials_are_scoped_to_the_selected_system(auth_client):
    assert login(auth_client, "distribution", "wrong-password").status_code == 302
    with auth_client.session_transaction() as session:
        assert "user_id" not in session

    assert login(auth_client, "distribution", "manufacturing-password").status_code == 302
    with auth_client.session_transaction() as session:
        assert "user_id" not in session

    assert login(auth_client, "distribution", "distribution-password").status_code == 302
    with auth_client.session_transaction() as session:
        assert session["system"] == "distribution"
        assert session["username"] == "system_admin"

    assert auth_client.get("/logout").status_code == 302
    assert login(auth_client, "manufacturing", "manufacturing-password").status_code == 302
    with auth_client.session_transaction() as session:
        assert session["system"] == "manufacturing"


def test_user_creation_and_password_changes_remain_system_scoped(auth_client):
    assert login(auth_client, "distribution", "distribution-password").status_code == 302
    assert auth_client.post(
        "/users/add",
        data={
            "username": "local_user",
            "full_name": "Local User",
            "role": "sales",
            "password": "old-password",
        },
    ).status_code == 302

    connection = database.get_connection()
    try:
        user = connection.execute(
            """SELECT id, password_hash, role, system
               FROM users WHERE username = ? AND system = ?""",
            ("local_user", "distribution"),
        ).fetchone()
        assert user["role"] == "sales"
        assert user["system"] == "distribution"
        assert check_password_hash(user["password_hash"], "old-password")
        connection.execute(
            """INSERT INTO users (username, password_hash, role, system)
               VALUES (?, ?, ?, ?)""",
            (
                "local_user",
                generate_password_hash("other-system-password"),
                "admin",
                "manufacturing",
            ),
        )
        connection.commit()
        user_id = user["id"]
    finally:
        connection.close()

    assert auth_client.post(
        f"/users/edit/{user_id}",
        data={
            "full_name": "Updated User",
            "role": "purchase",
            "is_active": "on",
            "password": "new-password",
        },
    ).status_code == 302
    connection = database.get_connection()
    try:
        updated = connection.execute(
            """SELECT password_hash, full_name, role, system
               FROM users WHERE id = ?""",
            (user_id,),
        ).fetchone()
        assert updated["full_name"] == "Updated User"
        assert updated["role"] == "purchase"
        assert updated["system"] == "distribution"
        assert check_password_hash(updated["password_hash"], "new-password")
    finally:
        connection.close()

    auth_client.get("/logout")
    assert login(auth_client, "distribution", "new-password", "local_user").status_code == 302
    with auth_client.session_transaction() as session:
        assert session["system"] == "distribution"
    auth_client.get("/logout")
    assert login(auth_client, "distribution", "other-system-password", "local_user").status_code == 302
    with auth_client.session_transaction() as session:
        assert "user_id" not in session
    assert login(auth_client, "manufacturing", "other-system-password", "local_user").status_code == 302
    with auth_client.session_transaction() as session:
        assert session["system"] == "manufacturing"


def test_flow_dashboard_is_manufacturing_scoped(auth_client):
    with auth_client.session_transaction() as session:
        session.update(user_id=1, username="system_admin", system="distribution")
    denied = auth_client.get("/flow")
    assert denied.status_code == 302
    assert "system=manufacturing" in denied.headers["Location"]

    with auth_client.session_transaction() as session:
        session.update(user_id=2, username="system_admin", system="manufacturing")
    assert auth_client.get("/flow").status_code == 200


@pytest.mark.parametrize(
    ("path", "system"),
    [("/", "distribution"), ("/flow", "manufacturing")],
)
def test_dashboard_connections_close_when_queries_raise(
    auth_client, monkeypatch, path, system
):
    class FailingCursor:
        def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("test-only query failure")

    class TrackedConnection:
        closed = False

        def cursor(self):
            return FailingCursor()

        def close(self):
            self.closed = True

    connection = TrackedConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    with auth_client.session_transaction() as session:
        session.update(user_id=1, username="system_admin", system=system)

    with pytest.raises(sqlite3.OperationalError):
        auth_client.get(path)
    assert connection.closed


def test_user_write_failure_rolls_back_and_connection_remains_usable(auth_client):
    assert login(auth_client, "distribution", "distribution-password").status_code == 302
    connection = database.get_connection()
    try:
        connection.execute(
            """CREATE TRIGGER reject_test_auth_user
               BEFORE INSERT ON users
               WHEN NEW.username = 'rollback_user'
               BEGIN SELECT RAISE(ABORT, 'test auth insert rejected'); END"""
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(sqlite3.IntegrityError):
        auth_client.post(
            "/users/add",
            data={
                "username": "rollback_user",
                "full_name": "Rollback User",
                "role": "sales",
                "password": "rollback-password",
            },
        )

    connection = database.get_connection()
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM users WHERE username = ?",
            ("rollback_user",),
        ).fetchone()[0] == 0
        connection.execute("DROP TRIGGER reject_test_auth_user")
        connection.commit()
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    finally:
        connection.close()


def test_duplicate_username_is_only_rejected_within_same_system(auth_client):
    assert login(auth_client, "distribution", "distribution-password").status_code == 302
    response = auth_client.post(
        "/users/add",
        data={
            "username": "system_admin",
            "full_name": "Duplicate User",
            "role": "sales",
            "password": "another-password",
        },
    )
    assert response.status_code == 302
    with auth_client.session_transaction() as session:
        flashes = session.get("_flashes", [])
    assert any("username is already taken" in message for _, message in flashes)
