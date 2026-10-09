import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module
import database


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
