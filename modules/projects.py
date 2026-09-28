from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from database import get_connection
from modules.auth import require_system

projects = Blueprint("projects", __name__)
projects.before_request(require_system("projects"))

STAGES = ("backlog", "planning", "in_progress", "on_hold", "completed")
STAGE_LABELS = {
    "backlog": "Backlog",
    "planning": "Planning",
    "in_progress": "In Progress",
    "on_hold": "On Hold",
    "completed": "Completed",
}
PRIORITIES = ("low", "normal", "high", "urgent")
TASK_STAGES = (
    "awaiting_response",
    "awaiting_quotation",
    "awaiting_visit",
    "negotiating",
    "rejected",
    "selected",
    "completed",
)
TASK_STAGE_LABELS = {
    "awaiting_response": "Awaiting Response",
    "awaiting_quotation": "Awaiting Quotation",
    "awaiting_visit": "Awaiting Visit",
    "negotiating": "Negotiating",
    "rejected": "Rejected",
    "selected": "Selected",
    "completed": "Completed",
}


def _project_users(cursor):
    cursor.execute(
        "SELECT id, username, full_name FROM users WHERE system = 'projects' AND is_active = 1 ORDER BY full_name, username"
    )
    return cursor.fetchall()


@projects.app_context_processor
def inject_sidebar_projects():
    user_id = session.get("user_id")
    if not user_id:
        return {"sidebar_projects": []}

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, name FROM projects
           WHERE (? = 1 OR created_by = ? OR EXISTS (
               SELECT 1 FROM project_user_shares
               WHERE project_user_shares.project_id = projects.id
                 AND project_user_shares.user_id = ?
           ))
           ORDER BY name COLLATE NOCASE, id""",
        (int(session.get("role") == "admin"), user_id, user_id),
    )
    sidebar_projects = cursor.fetchall()
    conn.close()
    return {"sidebar_projects": sidebar_projects}


def _valid_date(value):
    if not value:
        return True
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _get_project(cursor, project_id):
    cursor.execute(
        """SELECT projects.*, users.full_name AS owner_name, users.username AS owner_username
           FROM projects LEFT JOIN users ON users.id = projects.owner_id
           WHERE projects.id = ?""",
        (project_id,),
    )
    return cursor.fetchone()


def _can_view_project(cursor, project):
    user_id = session.get("user_id")
    if session.get("role") == "admin" or project["created_by"] == user_id:
        return True
    cursor.execute(
        "SELECT 1 FROM project_user_shares WHERE project_id = ? AND user_id = ?",
        (project["id"], user_id),
    )
    return cursor.fetchone() is not None


def _can_edit_projects():
    return session.get("role") in ("admin", "user")


def _accessible_project(cursor, project_id, write=False):
    project = _get_project(cursor, project_id)
    if project is None or not _can_view_project(cursor, project):
        return None, 404
    if write and not _can_edit_projects():
        return None, 403
    return project, None


def _get_task(cursor, project_id, task_id):
    cursor.execute(
        "SELECT * FROM project_tasks WHERE id = ? AND project_id = ?",
        (task_id, project_id),
    )
    return cursor.fetchone()


@projects.route("/projects")
def index():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT projects.*, users.full_name AS owner_name, users.username AS owner_username,
                  COUNT(project_tasks.id) AS task_count,
                  COALESCE(SUM(project_tasks.is_complete), 0) AS completed_tasks
           FROM projects
           LEFT JOIN users ON users.id = projects.owner_id
           LEFT JOIN project_tasks ON project_tasks.project_id = projects.id
           WHERE (? = 1 OR projects.created_by = ? OR EXISTS (
               SELECT 1 FROM project_user_shares
               WHERE project_user_shares.project_id = projects.id
                 AND project_user_shares.user_id = ?
           ))
           GROUP BY projects.id
           ORDER BY projects.updated_at DESC, projects.id DESC""",
        (int(session.get("role") == "admin"), session["user_id"], session["user_id"]),
    )
    all_projects = cursor.fetchall()
    conn.close()

    projects_by_stage = {stage: [] for stage in STAGES}
    for project in all_projects:
        if project["stage"] in projects_by_stage:
            projects_by_stage[project["stage"]].append(project)

    return render_template(
        "projects/board.html",
        projects_by_stage=projects_by_stage,
        stages=STAGES,
        stage_labels=STAGE_LABELS,
        total_projects=len(all_projects),
        active_projects=sum(project["stage"] != "completed" for project in all_projects),
        can_edit=_can_edit_projects(),
    )


@projects.route("/projects/new", methods=["GET", "POST"])
def create_project():
    if not _can_edit_projects():
        return "Forbidden", 403
    conn = get_connection()
    cursor = conn.cursor()
    users = _project_users(cursor)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        start_date = request.form.get("start_date", "").strip() or None
        due_date = request.form.get("due_date", "").strip() or None
        if not name:
            flash("Project name is required.", "danger")
        elif not _valid_date(start_date) or not _valid_date(due_date):
            flash("Enter valid project dates.", "danger")
        else:
            owner_id = request.form.get("owner_id", type=int) or None
            if owner_id and not any(user["id"] == owner_id for user in users):
                owner_id = None
            cursor.execute(
                """INSERT INTO projects
                   (name, description, client, stage, priority, owner_id, start_date, due_date, created_by)
                   VALUES (?, ?, ?, 'backlog', ?, ?, ?, ?, ?)""",
                (
                    name,
                    request.form.get("description", "").strip(),
                    request.form.get("client", "").strip(),
                    request.form.get("priority", "normal") if request.form.get("priority") in PRIORITIES else "normal",
                    owner_id,
                    start_date,
                    due_date,
                    session["user_id"],
                ),
            )
            project_id = cursor.lastrowid
            conn.commit()
            conn.close()
            flash("Project created.", "success")
            return redirect(url_for("projects.detail", project_id=project_id))

    conn.close()
    return render_template(
        "projects/project_form.html",
        project=None,
        users=users,
        priorities=PRIORITIES,
        page_heading="New project",
    )


@projects.route("/projects/<int:project_id>")
def detail(project_id):
    conn = get_connection()
    cursor = conn.cursor()
    project, error_status = _accessible_project(cursor, project_id)
    if error_status:
        conn.close()
        if error_status == 404:
            return render_template("projects/not_found.html"), 404
        return "Forbidden", error_status

    cursor.execute(
        """SELECT project_tasks.*, users.full_name AS assignee_name, users.username AS assignee_username
           FROM project_tasks LEFT JOIN users ON users.id = project_tasks.assigned_to
           WHERE project_tasks.project_id = ?
           ORDER BY project_tasks.is_complete, project_tasks.due_date IS NULL, project_tasks.due_date, project_tasks.id DESC""",
        (project_id,),
    )
    tasks = cursor.fetchall()
    users = _project_users(cursor)
    cursor.execute("SELECT user_id FROM project_user_shares WHERE project_id = ?", (project_id,))
    shared_user_ids = {row["user_id"] for row in cursor.fetchall()}
    conn.close()
    return render_template(
        "projects/detail.html",
        project=project,
        tasks=tasks,
        users=users,
        stages=STAGES,
        stage_labels=STAGE_LABELS,
        task_stages=TASK_STAGES,
        task_stage_labels=TASK_STAGE_LABELS,
        priorities=PRIORITIES,
        can_edit=_can_edit_projects(),
        can_manage_shares=_can_edit_projects() and (
            session.get("role") == "admin" or project["created_by"] == session["user_id"]
        ),
        shared_user_ids=shared_user_ids,
    )


@projects.route("/projects/<int:project_id>/update", methods=["POST"])
def update_project(project_id):
    conn = get_connection()
    cursor = conn.cursor()
    project, error_status = _accessible_project(cursor, project_id, write=True)
    if error_status:
        conn.close()
        if error_status == 404:
            return render_template("projects/not_found.html"), 404
        return "Forbidden", error_status

    data = request.get_json(silent=True) or request.form
    name = data.get("name", "").strip()
    stage = data.get("stage", "")
    priority = data.get("priority", "normal")
    start_date = data.get("start_date", "").strip() or None
    due_date = data.get("due_date", "").strip() or None
    users = _project_users(cursor)
    try:
        owner_id = int(data.get("owner_id") or 0) or None
    except (TypeError, ValueError):
        owner_id = None
    if owner_id and not any(user["id"] == owner_id for user in users):
        owner_id = None

    error = None
    if not name:
        error = "Project name is required."
    elif stage not in STAGES or priority not in PRIORITIES:
        error = "Choose a valid stage and priority."
    elif not _valid_date(start_date) or not _valid_date(due_date):
        error = "Enter valid project dates."

    if error:
        if request.is_json:
            conn.close()
            return {"error": error}, 400
        flash(error, "danger")
    else:
        cursor.execute(
            """UPDATE projects
               SET name = ?, description = ?, client = ?, stage = ?, priority = ?, owner_id = ?,
                   start_date = ?, due_date = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (
                name,
                data.get("description", "").strip(),
                data.get("client", "").strip(),
                stage,
                priority,
                owner_id,
                start_date,
                due_date,
                project_id,
            ),
        )
        conn.commit()
        if request.is_json:
            conn.close()
            return {"saved": True, "stage": stage}
        flash("Project updated.", "success")
    conn.close()
    return redirect(url_for("projects.detail", project_id=project_id))


@projects.route("/projects/<int:project_id>/share", methods=["POST"])
def share_project(project_id):
    conn = get_connection()
    cursor = conn.cursor()
    project, error_status = _accessible_project(cursor, project_id)
    if error_status:
        conn.close()
        if error_status == 404:
            return render_template("projects/not_found.html"), 404
        return "Forbidden", error_status
    if not _can_edit_projects() or (
        session.get("role") != "admin" and project["created_by"] != session["user_id"]
    ):
        conn.close()
        return "Forbidden", 403

    requested_user_ids = {int(value) for value in request.form.getlist("user_ids") if value.isdigit()}
    users = _project_users(cursor)
    valid_user_ids = {user["id"] for user in users}
    if not requested_user_ids.issubset(valid_user_ids):
        conn.close()
        return "Choose active Projects users only.", 400

    cursor.execute("DELETE FROM project_user_shares WHERE project_id = ?", (project_id,))
    cursor.executemany(
        "INSERT INTO project_user_shares (project_id, user_id, shared_by) VALUES (?, ?, ?)",
        [(project_id, user_id, session["user_id"]) for user_id in requested_user_ids],
    )
    conn.commit()
    conn.close()
    flash("Project sharing updated.", "success")
    return redirect(url_for("projects.detail", project_id=project_id))


@projects.route("/projects/<int:project_id>/delete", methods=["POST"])
def delete_project(project_id):
    conn = get_connection()
    cursor = conn.cursor()
    project, error_status = _accessible_project(cursor, project_id, write=True)
    if error_status:
        conn.close()
        if error_status == 404:
            return render_template("projects/not_found.html"), 404
        return "Forbidden", error_status
    cursor.execute(
        "DELETE FROM project_task_notes WHERE task_id IN (SELECT id FROM project_tasks WHERE project_id = ?)",
        (project_id,),
    )
    cursor.execute("DELETE FROM project_tasks WHERE project_id = ?", (project_id,))
    cursor.execute("DELETE FROM project_user_shares WHERE project_id = ?", (project_id,))
    cursor.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    flash("Project deleted.", "success")
    return redirect(url_for("projects.index"))


@projects.route("/projects/<int:project_id>/tasks", methods=["POST"])
def add_task(project_id):
    if not _can_edit_projects():
        return "Forbidden", 403
    title = request.form.get("title", "").strip()
    due_date = request.form.get("due_date", "").strip() or None
    stage = request.form.get("stage", "awaiting_response")
    if not title:
        flash("Task name is required.", "danger")
        return redirect(url_for("projects.detail", project_id=project_id))
    if not _valid_date(due_date):
        flash("Enter a valid task due date.", "danger")
        return redirect(url_for("projects.detail", project_id=project_id))
    if stage not in TASK_STAGES:
        flash("Choose a valid task stage.", "danger")
        return redirect(url_for("projects.detail", project_id=project_id))

    conn = get_connection()
    cursor = conn.cursor()
    project, error_status = _accessible_project(cursor, project_id, write=True)
    if error_status:
        conn.close()
        if error_status == 404:
            return render_template("projects/not_found.html"), 404
        return "Forbidden", error_status
    cursor.execute(
        """INSERT INTO project_tasks (project_id, title, details, due_date, stage, is_complete, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            project_id,
            title,
            request.form.get("details", "").strip(),
            due_date,
            stage,
            int(stage == "completed"),
            session["user_id"],
        ),
    )
    cursor.execute("UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    flash("Task added.", "success")
    return redirect(url_for("projects.detail", project_id=project_id))


@projects.route("/projects/<int:project_id>/tasks/<int:task_id>/toggle", methods=["POST"])
def toggle_task(project_id, task_id):
    conn = get_connection()
    cursor = conn.cursor()
    project, error_status = _accessible_project(cursor, project_id, write=True)
    if error_status:
        conn.close()
        if error_status == 404:
            return render_template("projects/not_found.html"), 404
        return "Forbidden", error_status
    cursor.execute(
        """UPDATE project_tasks
           SET is_complete = CASE is_complete WHEN 1 THEN 0 ELSE 1 END,
               stage = CASE stage WHEN 'completed' THEN 'awaiting_response' ELSE 'completed' END,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ? AND project_id = ?""",
        (task_id, project_id),
    )
    if cursor.rowcount == 0:
        conn.close()
        return render_template("projects/not_found.html"), 404
    cursor.execute("UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("projects.detail", project_id=project_id))


@projects.route("/projects/<int:project_id>/stage", methods=["POST"])
def update_project_stage(project_id):
    if not _can_edit_projects():
        return {"error": "Forbidden"}, 403
    payload = request.get_json(silent=True) or {}
    stage = request.form.get("stage") or payload.get("stage")
    if stage not in STAGES:
        return {"error": "Choose a valid project stage."}, 400
    conn = get_connection()
    cursor = conn.cursor()
    project, error_status = _accessible_project(cursor, project_id, write=True)
    if error_status:
        conn.close()
        if error_status == 404:
            return {"error": "Project not found."}, 404
        return {"error": "Forbidden"}, error_status
    cursor.execute(
        "UPDATE projects SET stage = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (stage, project_id),
    )
    if cursor.rowcount == 0:
        conn.close()
        return {"error": "Project not found."}, 404
    conn.commit()
    conn.close()
    if request.is_json:
        return {"saved": True, "stage": stage}
    flash("Project stage updated.", "success")
    return redirect(url_for("projects.index"))


@projects.route("/projects/<int:project_id>/tasks/<int:task_id>/stage", methods=["POST"])
def update_task_stage(project_id, task_id):
    if not _can_edit_projects():
        return {"error": "Forbidden"}, 403
    payload = request.get_json(silent=True) or {}
    stage = request.form.get("stage") or payload.get("stage")
    if stage not in TASK_STAGES:
        return {"error": "Choose a valid task stage."}, 400
    conn = get_connection()
    cursor = conn.cursor()
    project, error_status = _accessible_project(cursor, project_id, write=True)
    if error_status:
        conn.close()
        if error_status == 404:
            return {"error": "Project not found."}, 404
        return {"error": "Forbidden"}, error_status
    cursor.execute(
        """UPDATE project_tasks SET stage = ?, is_complete = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = ? AND project_id = ?""",
        (stage, int(stage == "completed"), task_id, project_id),
    )
    if cursor.rowcount == 0:
        conn.close()
        return {"error": "Task not found."}, 404
    cursor.execute("UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    if request.is_json:
        return {"saved": True, "stage": stage}
    return redirect(url_for("projects.detail", project_id=project_id))


@projects.route("/projects/<int:project_id>/items/<int:task_id>", methods=["GET", "POST"])
def task_detail(project_id, task_id):
    conn = get_connection()
    cursor = conn.cursor()
    project, error_status = _accessible_project(cursor, project_id)
    if error_status:
        conn.close()
        if error_status == 404:
            return render_template("projects/not_found.html"), 404
        return "Forbidden", error_status
    task = _get_task(cursor, project_id, task_id)
    if project is None or task is None:
        conn.close()
        return render_template("projects/not_found.html"), 404

    if request.method == "POST":
        if not _can_edit_projects():
            conn.close()
            return "Forbidden", 403
        title = request.form.get("title", "").strip()
        stage = request.form.get("stage", "")
        due_date = request.form.get("due_date", "").strip() or None
        details = request.form.get("details", "").strip()
        if not title:
            flash("Work item name is required.", "danger")
        elif stage not in TASK_STAGES:
            flash("Choose a valid work item stage.", "danger")
        elif not _valid_date(due_date):
            flash("Enter a valid due date.", "danger")
        else:
            cursor.execute(
                """UPDATE project_tasks
                   SET title = ?, details = ?, stage = ?, due_date = ?, is_complete = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND project_id = ?""",
                (title, details, stage, due_date, int(stage == "completed"), task_id, project_id),
            )
            cursor.execute("UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (project_id,))
            conn.commit()
            flash("Work item updated.", "success")
            conn.close()
            return redirect(url_for("projects.task_detail", project_id=project_id, task_id=task_id))

    cursor.execute(
        """SELECT project_task_notes.*, users.full_name, users.username
           FROM project_task_notes JOIN users ON users.id = project_task_notes.created_by
           WHERE task_id = ? ORDER BY created_at DESC, id DESC""",
        (task_id,),
    )
    notes = cursor.fetchall()
    conn.close()
    return render_template(
        "projects/task_detail.html",
        project=project,
        task=task,
        notes=notes,
        task_stages=TASK_STAGES,
        task_stage_labels=TASK_STAGE_LABELS,
        can_edit=_can_edit_projects(),
    )


@projects.route("/projects/<int:project_id>/items/<int:task_id>/delete", methods=["POST"])
def delete_task(project_id, task_id):
    conn = get_connection()
    cursor = conn.cursor()
    project, error_status = _accessible_project(cursor, project_id, write=True)
    if error_status:
        conn.close()
        if error_status == 404:
            return render_template("projects/not_found.html"), 404
        return "Forbidden", error_status
    if _get_task(cursor, project_id, task_id) is None:
        conn.close()
        return render_template("projects/not_found.html"), 404
    cursor.execute("DELETE FROM project_task_notes WHERE task_id = ?", (task_id,))
    cursor.execute("DELETE FROM project_tasks WHERE id = ? AND project_id = ?", (task_id, project_id))
    cursor.execute("UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    flash("Work item deleted.", "success")
    return redirect(url_for("projects.detail", project_id=project_id))


@projects.route("/projects/<int:project_id>/items/<int:task_id>/notes", methods=["POST"])
def add_task_note(project_id, task_id):
    if not _can_edit_projects():
        return "Forbidden", 403
    note = request.form.get("note", "").strip()
    if not note:
        flash("Write a note before adding it.", "danger")
        return redirect(url_for("projects.task_detail", project_id=project_id, task_id=task_id))
    conn = get_connection()
    cursor = conn.cursor()
    project, error_status = _accessible_project(cursor, project_id, write=True)
    if error_status:
        conn.close()
        if error_status == 404:
            return render_template("projects/not_found.html"), 404
        return "Forbidden", error_status
    if _get_task(cursor, project_id, task_id) is None:
        conn.close()
        return render_template("projects/not_found.html"), 404
    cursor.execute(
        "INSERT INTO project_task_notes (task_id, note, created_by) VALUES (?, ?, ?)",
        (task_id, note, session["user_id"]),
    )
    cursor.execute("UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    flash("Note added.", "success")
    return redirect(url_for("projects.task_detail", project_id=project_id, task_id=task_id))
