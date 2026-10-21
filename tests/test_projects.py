import sys
from datetime import date
from pathlib import Path

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module
import database
import modules.projects as projects_module


@pytest.fixture
def client(tmp_path):
    database.DATABASE = str(tmp_path / "projects_test.db")
    database.create_tables()
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users")
    cursor.executemany(
        "INSERT INTO users (username, password_hash, full_name, role, system) VALUES (?, ?, ?, ?, ?)",
        [
            ("dist_admin", generate_password_hash("dist-password"), "Distribution Admin", "admin", "distribution"),
            ("mfg_admin", generate_password_hash("mfg-password"), "Manufacturing Admin", "admin", "manufacturing"),
        ],
    )
    conn.commit()
    conn.close()
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


def login_as(client, username, system):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, role FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    conn.close()
    with client.session_transaction() as session:
        session["user_id"] = user["id"]
        session["username"] = username
        session["full_name"] = username
        session["role"] = user["role"]
        session["system"] = system


def test_project_date_binding_is_backend_compatible(monkeypatch):
    monkeypatch.setattr(database, "is_postgres_configured", lambda: True)
    assert projects_module._date_value("2026-10-20") == date(2026, 10, 20)
    monkeypatch.setattr(database, "is_postgres_configured", lambda: False)
    assert projects_module._date_value("2026-10-20") == "2026-10-20"


def test_project_board_stage_detail_and_tasks(client):
    conn = database.get_connection()
    conn.execute(
        "INSERT INTO users (username, password_hash, role, system) VALUES (?, ?, ?, ?)",
        ("project_admin", generate_password_hash("project-password"), "admin", "projects"),
    )
    conn.commit()
    conn.close()
    login_as(client, "project_admin", "projects")

    board = client.get("/projects")
    assert board.status_code == 200
    assert b"Start with a project" in board.data

    response = client.post(
        "/projects/new",
        data={
            "name": "Warehouse rollout",
            "client": "Operations",
            "description": "Prepare the new site",
            "priority": "high",
            "start_date": "2026-10-01",
            "due_date": "2026-11-15",
        },
    )
    assert response.status_code == 302
    project_id = int(response.headers["Location"].rsplit("/", 1)[1])

    response = client.post(
        f"/projects/{project_id}/update",
        data={
            "name": "Warehouse rollout",
            "client": "Operations",
            "description": "Prepare the new site",
            "stage": "in_progress",
            "priority": "high",
            "start_date": "2026-10-01",
            "due_date": "2026-11-15",
        },
    )
    assert response.status_code == 302
    board = client.get("/projects")
    assert b"In Progress" in board.data
    assert b'draggable="true"' in board.data
    assert b'data-stage="in_progress"' in board.data

    response = client.post(
        f"/projects/{project_id}/tasks",
        data={"title": "Confirm site access", "details": "Coordinate keys", "due_date": "2026-10-03", "stage": "awaiting_quotation"},
    )
    assert response.status_code == 302
    conn = database.get_connection()
    task = conn.execute("SELECT id, stage FROM project_tasks WHERE project_id = ?", (project_id,)).fetchone()
    conn.close()
    assert task["stage"] == "awaiting_quotation"

    response = client.post(f"/projects/{project_id}/tasks/{task['id']}/toggle")
    assert response.status_code == 302
    detail = client.get(f"/projects/{project_id}")
    assert detail.status_code == 200
    assert b"Confirm site access" in detail.data
    assert f'<a class="project-flow-node active" href="/projects/{project_id}" aria-current="page">Warehouse rollout</a>'.encode() in detail.data
    assert b"Completed" in detail.data
    assert b'class="hidden" id="projectDetailsForm"' in detail.data
    assert b'class="hidden" id="projectEditActionsPanel"' in detail.data
    assert b'aria-label="Edit project details"' in detail.data
    assert b'aria-expanded="false" aria-controls="newItemForm">\xef\xbc\x8b New Item' in detail.data
    assert b'class="task-form hidden" id="newItemForm"' in detail.data
    assert b'aria-label="Initial task stage"' in detail.data
    assert b'aria-label="Assign task"' not in detail.data
    assert b'class="task-ribbon">Completed</div>' in detail.data
    assert b'task-stage-select' not in detail.data
    assert f'/projects/{project_id}/items/{task["id"]}'.encode() in detail.data

    response = client.post(
        f"/projects/{project_id}/tasks/{task['id']}/stage",
        json={"stage": "awaiting_visit"},
    )
    assert response.status_code == 200
    conn = database.get_connection()
    task_state = conn.execute("SELECT stage, is_complete FROM project_tasks WHERE id = ?", (task["id"],)).fetchone()
    conn.close()
    assert task_state["stage"] == "awaiting_visit"
    assert task_state["is_complete"] == 0

    item_url = f"/projects/{project_id}/items/{task['id']}"
    response = client.get(item_url)
    assert response.status_code == 200
    assert b'name="stage"' in response.data
    assert f'<a class="project-flow-node active" href="/projects/{project_id}" aria-current="page">Warehouse rollout</a>'.encode() in response.data
    response = client.post(
        item_url,
        data={
            "title": "Confirm site access and safety",
            "details": "Schedule a site walk-through",
            "stage": "selected",
            "due_date": "2026-10-04",
        },
    )
    assert response.status_code == 302
    response = client.post(
        f"{item_url}/notes",
        data={"note": "Customer confirmed access window."},
    )
    assert response.status_code == 302
    response = client.get(item_url)
    assert response.status_code == 200
    assert b"Confirm site access and safety" in response.data
    assert b"Customer confirmed access window." in response.data

    response = client.post(
        f"/projects/{project_id}/stage",
        json={"stage": "completed"},
    )
    assert response.status_code == 200
    assert b"Completed" in client.get("/projects").data

    response = client.post(
        f"/projects/{project_id}/update",
        json={
            "name": "Warehouse rollout revised",
            "description": "Updated scope",
            "client": "Operations",
            "stage": "planning",
            "priority": "high",
            "start_date": "2026-10-01",
            "due_date": "2026-11-15",
        },
    )
    assert response.status_code == 200
    conn = database.get_connection()
    project_state = conn.execute("SELECT name, description, stage FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    assert tuple(project_state) == ("Warehouse rollout revised", "Updated scope", "planning")


def test_projects_admin_bootstrap_is_one_time_and_creates_isolated_admin(client):
    login_as(client, "dist_admin", "distribution")
    response = client.get("/users/setup-projects")
    assert response.status_code == 200

    response = client.post(
        "/users/setup-projects",
        data={"username": "projects_admin", "full_name": "Projects Admin", "password": "secure-pass-9"},
    )
    assert response.status_code == 302

    conn = database.get_connection()
    user = conn.execute("SELECT * FROM users WHERE username = ?", ("projects_admin",)).fetchone()
    conn.close()
    assert user["system"] == "projects"
    assert user["role"] == "admin"
    assert check_password_hash(user["password_hash"], "secure-pass-9")

    response = client.post(
        "/login?system=projects",
        data={"username": "projects_admin", "password": "secure-pass-9", "system": "projects"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/projects")
    assert client.get("/users/setup-projects").status_code == 302


def test_system_logins_cannot_cross_into_projects(client):
    login_as(client, "dist_admin", "distribution")
    response = client.get("/projects")
    assert response.status_code == 302
    assert "system=projects" in response.headers["Location"]


def test_login_chooser_offers_projects(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"/login?system=projects" in response.data
    assert b'https://sumak-visits.web.app/" class="system-btn fuel-btn"' in response.data


def test_projects_login_page_has_projects_heading(client):
    response = client.get("/login?system=projects")
    assert response.status_code == 200
    assert b"PROJECTS LOGIN" in response.data
    assert b"DISTRIBUTION LOGIN" not in response.data


def test_database_migrates_existing_project_tasks_to_stages(tmp_path):
    database.DATABASE = str(tmp_path / "legacy_projects.db")
    conn = database.get_connection()
    conn.execute(
        """CREATE TABLE project_tasks (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               project_id INTEGER NOT NULL,
               title TEXT NOT NULL,
               details TEXT,
               assigned_to INTEGER,
               due_date DATE,
               is_complete INTEGER NOT NULL DEFAULT 0,
               created_by INTEGER NOT NULL,
               created_at DATETIME DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    conn.execute(
        "INSERT INTO project_tasks (project_id, title, is_complete, created_by) VALUES (1, 'Legacy complete task', 1, 1)"
    )
    conn.commit()
    conn.close()

    database.create_tables()

    conn = database.get_connection()
    task = conn.execute("SELECT stage, is_complete FROM project_tasks WHERE title = ?", ("Legacy complete task",)).fetchone()
    conn.close()
    assert task["stage"] == "completed"
    assert task["is_complete"] == 1


def test_database_migrates_legacy_project_roles_to_user(tmp_path):
    database.DATABASE = str(tmp_path / "legacy_project_roles.db")
    database.create_tables()
    conn = database.get_connection()
    conn.executemany(
        "INSERT INTO users (username, password_hash, role, system) VALUES (?, ?, ?, 'projects')",
        [
            ("legacy_staff", generate_password_hash("legacy-password"), "sales"),
            ("projects_admin", generate_password_hash("admin-password"), "admin"),
        ],
    )
    conn.commit()
    conn.close()

    database.create_tables()

    conn = database.get_connection()
    roles = dict(conn.execute("SELECT username, role FROM users WHERE system = 'projects'").fetchall())
    conn.close()
    assert roles == {"legacy_staff": "user", "projects_admin": "admin"}


def test_projects_users_page_uses_separate_list_add_and_edit_screens(client):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (username, password_hash, full_name, role, system) VALUES (?, ?, ?, ?, ?)",
        ("projects_admin", generate_password_hash("project-password"), "Projects Admin", "admin", "projects"),
    )
    conn.commit()
    conn.close()
    login_as(client, "projects_admin", "projects")

    users_page = client.get("/users")
    assert users_page.status_code == 200
    assert b"class=\"users-table\"" in users_page.data
    assert b"Add User" in users_page.data
    assert b'name="password"' not in users_page.data

    add_page = client.get("/users/add")
    assert add_page.status_code == 200
    assert b'name="username"' in add_page.data
    assert b'name="password"' in add_page.data

    response = client.post(
        "/users/add",
        data={"username": "project_staff", "full_name": "Project Staff", "role": "user", "password": "staff-pass-9"},
    )
    assert response.status_code == 302
    conn = database.get_connection()
    staff = conn.execute("SELECT id FROM users WHERE username = ?", ("project_staff",)).fetchone()
    assert conn.execute("SELECT role FROM users WHERE id = ?", (staff["id"],)).fetchone()["role"] == "user"
    conn.close()

    edit_page = client.get(f"/users/edit/{staff['id']}")
    assert edit_page.status_code == 200
    assert b"Project Staff" in edit_page.data
    assert b"Active account" in edit_page.data
    assert b"External (read-only)" in add_page.data


def test_projects_are_private_until_shared_and_external_users_are_read_only(client):
    conn = database.get_connection()
    conn.executemany(
        "INSERT INTO users (username, password_hash, full_name, role, system) VALUES (?, ?, ?, ?, 'projects')",
        [
            ("project_owner", generate_password_hash("owner-password"), "Project Owner", "user"),
            ("project_peer", generate_password_hash("peer-password"), "Project Peer", "user"),
            ("project_external", generate_password_hash("external-password"), "Project External", "external"),
            ("project_other", generate_password_hash("other-password"), "Other User", "user"),
        ],
    )
    owner_id = conn.execute("SELECT id FROM users WHERE username = 'project_owner'").fetchone()["id"]
    peer_id = conn.execute("SELECT id FROM users WHERE username = 'project_peer'").fetchone()["id"]
    external_id = conn.execute("SELECT id FROM users WHERE username = 'project_external'").fetchone()["id"]
    other_id = conn.execute("SELECT id FROM users WHERE username = 'project_other'").fetchone()["id"]
    cursor = conn.cursor()
    cursor.execute("INSERT INTO projects (name, created_by) VALUES (?, ?)", ("Private project", owner_id))
    project_id = cursor.lastrowid
    cursor.execute("INSERT INTO projects (name, created_by) VALUES (?, ?)", ("Another shared project", owner_id))
    another_project_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO project_tasks (project_id, title, details, created_by) VALUES (?, ?, ?, ?)",
        (project_id, "Private work item", "Read-only context", owner_id),
    )
    task_id = cursor.lastrowid
    cursor.execute("INSERT INTO project_task_notes (task_id, note, created_by) VALUES (?, ?, ?)", (task_id, "Shared note", owner_id))
    cursor.execute("INSERT INTO projects (name, created_by) VALUES (?, ?)", ("Other private project", other_id))
    other_project_id = cursor.lastrowid
    conn.commit()
    conn.close()

    login_as(client, "project_owner", "projects")
    board = client.get("/projects")
    assert b"Private project" in board.data
    assert b"Other private project" not in board.data
    assert client.post(
        f"/projects/{project_id}/share",
        data={"user_ids": [str(peer_id), str(external_id)]},
    ).status_code == 302
    assert client.post(
        f"/projects/{another_project_id}/share",
        data={"user_ids": [str(peer_id)]},
    ).status_code == 302

    login_as(client, "project_peer", "projects")
    peer_detail = client.get(f"/projects/{project_id}")
    assert peer_detail.status_code == 200
    assert f'class="project-flow-node active" href="/projects/{project_id}" aria-current="page">Private project</a>'.encode() in peer_detail.data
    assert f'class="project-flow-node" href="/projects/{another_project_id}">Another shared project</a>'.encode() in peer_detail.data
    assert client.get(f"/projects/{other_project_id}").status_code == 404
    assert b"Private project" in client.get("/projects").data
    response = client.post(
        f"/projects/{project_id}/update",
        data={
            "name": "Updated by shared user",
            "stage": "planning",
            "priority": "normal",
            "start_date": "",
            "due_date": "",
        },
    )
    assert response.status_code == 302
    conn = database.get_connection()
    assert conn.execute("SELECT name FROM projects WHERE id = ?", (project_id,)).fetchone()["name"] == "Updated by shared user"
    conn.close()

    login_as(client, "project_external", "projects")
    board = client.get("/projects")
    assert b"Updated by shared user" in board.data
    assert b"Other private project" not in board.data
    detail = client.get(f"/projects/{project_id}")
    assert detail.status_code == 200
    assert b"Private work item" in detail.data
    assert b'id="editDetailsToggle"' not in detail.data
    assert b'id="newItemToggle"' not in detail.data
    assert b"Shared access" not in detail.data
    item_url = f"/projects/{project_id}/items/{task_id}"
    item_detail = client.get(item_url)
    assert item_detail.status_code == 200
    assert b"Shared note" in item_detail.data
    assert b"Add a note" not in item_detail.data
    assert client.post(item_url, data={"title": "Changed", "stage": "completed"}).status_code == 403
    assert client.post(f"{item_url}/notes", data={"note": "Blocked"}).status_code == 403
    assert client.post(f"/projects/{project_id}/tasks", data={"title": "Blocked"}).status_code == 403
    assert client.post(f"/projects/{project_id}/tasks/{task_id}/toggle").status_code == 403
    assert client.post(f"/projects/{project_id}/stage", json={"stage": "completed"}).status_code == 403
    assert client.post(f"/projects/{project_id}/tasks/{task_id}/stage", json={"stage": "completed"}).status_code == 403
    assert client.post(f"{item_url}/delete").status_code == 403
    assert client.post(f"/projects/{project_id}/delete").status_code == 403
    assert client.post(f"/projects/{project_id}/share", data={"user_ids": [str(other_id)]}).status_code == 403


def test_users_can_delete_projects_and_work_items_they_can_access(client):
    conn = database.get_connection()
    conn.executemany(
        "INSERT INTO users (username, password_hash, role, system) VALUES (?, ?, ?, 'projects')",
        [
            ("delete_owner", generate_password_hash("owner-password"), "user"),
            ("delete_peer", generate_password_hash("peer-password"), "user"),
        ],
    )
    owner_id = conn.execute("SELECT id FROM users WHERE username = 'delete_owner'").fetchone()["id"]
    peer_id = conn.execute("SELECT id FROM users WHERE username = 'delete_peer'").fetchone()["id"]
    cursor = conn.cursor()
    cursor.execute("INSERT INTO projects (name, created_by) VALUES ('Shared for deletion', ?)", (owner_id,))
    shared_project_id = cursor.lastrowid
    cursor.execute("INSERT INTO project_tasks (project_id, title, created_by) VALUES (?, 'Delete this item', ?)", (shared_project_id, owner_id))
    task_id = cursor.lastrowid
    cursor.execute("INSERT INTO projects (name, created_by) VALUES ('Owned for deletion', ?)", (owner_id,))
    owned_project_id = cursor.lastrowid
    conn.commit()
    conn.close()

    login_as(client, "delete_owner", "projects")
    assert client.post(f"/projects/{shared_project_id}/share", data={"user_ids": [str(peer_id)]}).status_code == 302
    login_as(client, "delete_peer", "projects")
    assert client.post(f"/projects/{shared_project_id}/items/{task_id}/delete").status_code == 302
    assert client.get(f"/projects/{shared_project_id}/items/{task_id}").status_code == 404
    assert client.post(f"/projects/{shared_project_id}/delete").status_code == 302
    assert client.get(f"/projects/{shared_project_id}").status_code == 404

    login_as(client, "delete_owner", "projects")
    assert client.post(f"/projects/{owned_project_id}/delete").status_code == 302
    assert client.get(f"/projects/{owned_project_id}").status_code == 404

    login_as(client, "delete_peer", "projects")
    response = client.post("/projects/new", data={"name": "Peer-owned project"})
    assert response.status_code == 302
    conn = database.get_connection()
    created_by = conn.execute(
        "SELECT created_by FROM projects WHERE name = 'Peer-owned project'"
    ).fetchone()["created_by"]
    assert created_by == peer_id
    conn.close()