import logging

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import database
from database import get_connection as _get_connection
from modules.document_database import (
    install_document_connection_cleanup,
    managed_document_connection,
)
from modules.auth import require_system

hiring = Blueprint("hiring", __name__)
get_connection = managed_document_connection(_get_connection)
install_document_connection_cleanup(hiring)
logger = logging.getLogger(__name__)
PUBLIC_ENDPOINTS = {"hiring.careers", "hiring.apply", "hiring.application_received"}
JOB_STATUSES = ("draft", "open", "closed")
EMPLOYMENT_TYPES = ("Full-time", "Part-time", "Contract", "Temporary", "Internship")


@hiring.before_request
def require_hiring_login():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    return require_system("hiring")()

STATUSES = ("new", "screening", "interview", "offer", "hired", "rejected")
STATUS_LABELS = {
    "new": "New",
    "screening": "Screening",
    "interview": "Interview",
    "offer": "Offer",
    "hired": "Hired",
    "rejected": "Rejected",
}
EDIT_ROLES = ("admin", "recruiter", "hiring_manager")


def _can_edit():
    return session.get("role") in EDIT_ROLES


def _database_failure(error, conn):
    if not database.is_database_error(error):
        return None

    if conn is not None:
        try:
            conn.rollback()
        except Exception as rollback_error:
            logger.error(
                "Hiring database rollback failed (%s).",
                type(rollback_error).__name__,
            )
    logger.warning("Hiring database operation failed (%s).", type(error).__name__)
    return "Hiring service is temporarily unavailable. Please try again later.", 503


@hiring.route("/careers")
def careers():
    conn = None
    try:
        conn = get_connection()
        jobs = conn.execute(
            "SELECT * FROM hiring_jobs WHERE status = 'open' ORDER BY created_at DESC, id DESC"
        ).fetchall()
    except Exception as error:
        response = _database_failure(error, conn)
        if response is not None:
            return response
        raise
    finally:
        if conn is not None:
            conn.close()
    return render_template("hiring/careers.html", jobs=jobs)


@hiring.route("/careers/<int:job_id>/apply", methods=["GET", "POST"])
def apply(job_id):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholder = "%s" if database.is_postgres_configured() else "?"
        cursor.execute(
            f"SELECT * FROM hiring_jobs WHERE id = {placeholder} AND status = 'open'",
            (job_id,),
        )
        job = cursor.fetchone()
        if not job:
            return "Job opening not found", 404

        source = "LinkedIn" if request.args.get("source", "").lower() == "linkedin" else "Careers page"
        if request.method == "POST":
            if request.form.get("website", "").strip():
                return redirect(url_for("hiring.application_received"))
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()
            resume_url = request.form.get("resume_url", "").strip()
            cover_note = request.form.get("cover_note", "").strip()
            if not full_name or len(full_name) > 160:
                flash("Enter your name (up to 160 characters).", "danger")
            elif not email or len(email) > 254 or "@" not in email:
                flash("Enter a valid email address.", "danger")
            elif len(phone) > 40 or len(resume_url) > 500 or len(cover_note) > 10000:
                flash("One or more fields exceed the allowed length.", "danger")
            elif not request.form.get("privacy_consent"):
                flash("Please confirm the application data notice to continue.", "danger")
            else:
                values = ", ".join([placeholder] * 5 + ["'new'"] + [placeholder] * 3)
                cursor.execute(
                    """INSERT INTO hiring_candidates
                       (full_name, email, phone, position, source, status, resume_url, notes, job_id)
                       VALUES ({})""".format(values),
                    (
                        full_name,
                        email,
                        phone or None,
                        job["title"],
                        source,
                        resume_url or None,
                        cover_note or None,
                        job_id,
                    ),
                )
                conn.commit()
                return redirect(url_for("hiring.application_received"))

    except Exception as error:
        response = _database_failure(error, conn)
        if response is not None:
            return response
        raise
    finally:
        if conn is not None:
            conn.close()

    return render_template("hiring/apply.html", job=job)


@hiring.route("/careers/application-received")
def application_received():
    return render_template("hiring/application_received.html")


@hiring.route("/hiring/jobs")
def jobs():
    if not _can_edit():
        return "Forbidden", 403
    conn = get_connection()
    rows = conn.execute(
        """SELECT hiring_jobs.*, COUNT(hiring_candidates.id) AS applicant_count
           FROM hiring_jobs LEFT JOIN hiring_candidates ON hiring_candidates.job_id = hiring_jobs.id
           GROUP BY hiring_jobs.id ORDER BY hiring_jobs.updated_at DESC, hiring_jobs.id DESC"""
    ).fetchall()
    conn.close()
    job_rows = [
        {
            "job": row,
            "apply_url": url_for("hiring.apply", job_id=row["id"], source="linkedin", _external=True),
        }
        for row in rows
    ]
    return render_template("hiring/jobs.html", job_rows=job_rows)


@hiring.route("/hiring/jobs/new", methods=["GET", "POST"])
def create_job():
    if not _can_edit():
        return "Forbidden", 403
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        status = request.form.get("status", "draft")
        employment_type = request.form.get("employment_type", "Full-time")
        if not title or len(title) > 160:
            flash("Enter a job title (up to 160 characters).", "danger")
        elif status not in JOB_STATUSES or employment_type not in EMPLOYMENT_TYPES:
            flash("Choose a valid job status and employment type.", "danger")
        else:
            conn = get_connection()
            conn.execute(
                """INSERT INTO hiring_jobs
                   (title, location, employment_type, description, status, created_by)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    title,
                    request.form.get("location", "").strip() or None,
                    employment_type,
                    request.form.get("description", "").strip() or None,
                    status,
                    session["user_id"],
                ),
            )
            conn.commit()
            conn.close()
            flash("Job opening created.", "success")
            return redirect(url_for("hiring.jobs"))
    return render_template(
        "hiring/job_form.html",
        job=None,
        statuses=JOB_STATUSES,
        employment_types=EMPLOYMENT_TYPES,
        page_heading="Create job opening",
    )


@hiring.route("/hiring/jobs/<int:job_id>/edit", methods=["GET", "POST"])
def edit_job(job_id):
    if not _can_edit():
        return "Forbidden", 403
    conn = get_connection()
    cursor = conn.cursor()
    job = cursor.execute("SELECT * FROM hiring_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        conn.close()
        return "Job opening not found", 404
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        status = request.form.get("status", "")
        employment_type = request.form.get("employment_type", "")
        if not title or len(title) > 160:
            flash("Enter a job title (up to 160 characters).", "danger")
        elif status not in JOB_STATUSES or employment_type not in EMPLOYMENT_TYPES:
            flash("Choose a valid job status and employment type.", "danger")
        else:
            cursor.execute(
                """UPDATE hiring_jobs SET title=?, location=?, employment_type=?, description=?,
                   status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (
                    title,
                    request.form.get("location", "").strip() or None,
                    employment_type,
                    request.form.get("description", "").strip() or None,
                    status,
                    job_id,
                ),
            )
            conn.commit()
            conn.close()
            flash("Job opening updated.", "success")
            return redirect(url_for("hiring.jobs"))
        job = cursor.execute("SELECT * FROM hiring_jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return render_template(
        "hiring/job_form.html",
        job=job,
        statuses=JOB_STATUSES,
        employment_types=EMPLOYMENT_TYPES,
        page_heading="Edit job opening",
    )


@hiring.route("/hiring")
def index():
    query = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    position = request.args.get("position", "").strip()
    conn = get_connection()
    cursor = conn.cursor()
    filters = []
    params = []
    if query:
        filters.append("(full_name LIKE ? OR email LIKE ? OR phone LIKE ? OR position LIKE ?)")
        params.extend([f"%{query}%"] * 4)
    if status in STATUSES:
        filters.append("status = ?")
        params.append(status)
    if position:
        filters.append("position = ?")
        params.append(position)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    cursor.execute(
        f"SELECT * FROM hiring_candidates {where} ORDER BY updated_at DESC, id DESC",
        params,
    )
    candidates = cursor.fetchall()
    cursor.execute("SELECT status, COUNT(*) AS count FROM hiring_candidates GROUP BY status")
    counts = {row["status"]: row["count"] for row in cursor.fetchall()}
    cursor.execute(
        "SELECT DISTINCT position FROM hiring_candidates ORDER BY LOWER(position), position"
    )
    positions = [row["position"] for row in cursor.fetchall()]
    conn.close()
    return render_template(
        "hiring/candidates.html",
        candidates=candidates,
        counts=counts,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        positions=positions,
        selected_status=status,
        selected_position=position,
        query=query,
        can_edit=_can_edit(),
    )


@hiring.route("/hiring/candidates/new", methods=["GET", "POST"])
def create_candidate():
    if not _can_edit():
        return "Forbidden", 403
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        candidate_position = request.form.get("position", "").strip()
        rating = request.form.get("rating", "").strip()
        status = request.form.get("status", "new")
        if not full_name or not candidate_position:
            flash("Candidate name and position are required.", "danger")
        elif status not in STATUSES:
            flash("Choose a valid candidate stage.", "danger")
        elif rating and (not rating.isdigit() or not 1 <= int(rating) <= 5):
            flash("Rating must be between 1 and 5.", "danger")
        else:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO hiring_candidates
                   (full_name, email, phone, position, source, status, rating, resume_url, notes, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    full_name,
                    request.form.get("email", "").strip() or None,
                    request.form.get("phone", "").strip() or None,
                    candidate_position,
                    request.form.get("source", "").strip() or None,
                    status,
                    int(rating) if rating else None,
                    request.form.get("resume_url", "").strip() or None,
                    request.form.get("notes", "").strip() or None,
                    session["user_id"],
                ),
            )
            conn.commit()
            conn.close()
            flash("Candidate added to the pipeline.", "success")
            return redirect(url_for("hiring.index"))
    return render_template(
        "hiring/candidate_form.html",
        candidate=None,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        page_heading="Add candidate",
    )


@hiring.route("/hiring/candidates/<int:candidate_id>/status", methods=["POST"])
def update_status(candidate_id):
    if not _can_edit():
        return "Forbidden", 403
    status = request.form.get("status", "")
    if status not in STATUSES:
        flash("Choose a valid candidate stage.", "danger")
    else:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE hiring_candidates SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, candidate_id),
        )
        conn.commit()
        changed = cursor.rowcount
        conn.close()
        if changed:
            flash("Candidate stage updated.", "success")
        else:
            flash("Candidate not found.", "danger")
    return redirect(url_for("hiring.index"))


@hiring.route("/hiring/candidates/<int:candidate_id>", methods=["GET", "POST"])
def edit_candidate(candidate_id):
    if not _can_edit():
        return "Forbidden", 403
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM hiring_candidates WHERE id = ?", (candidate_id,))
    candidate = cursor.fetchone()
    if not candidate:
        conn.close()
        return "Candidate not found", 404
    if request.method == "POST":
        if not _can_edit():
            conn.close()
            return "Forbidden", 403
        full_name = request.form.get("full_name", "").strip()
        candidate_position = request.form.get("position", "").strip()
        rating = request.form.get("rating", "").strip()
        status = request.form.get("status", "")
        if not full_name or not candidate_position:
            flash("Candidate name and position are required.", "danger")
        elif status not in STATUSES:
            flash("Choose a valid candidate stage.", "danger")
        elif rating and (not rating.isdigit() or not 1 <= int(rating) <= 5):
            flash("Rating must be between 1 and 5.", "danger")
        else:
            cursor.execute(
                """UPDATE hiring_candidates SET full_name=?, email=?, phone=?, position=?, source=?,
                   status=?, rating=?, resume_url=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (
                    full_name,
                    request.form.get("email", "").strip() or None,
                    request.form.get("phone", "").strip() or None,
                    candidate_position,
                    request.form.get("source", "").strip() or None,
                    status,
                    int(rating) if rating else None,
                    request.form.get("resume_url", "").strip() or None,
                    request.form.get("notes", "").strip() or None,
                    candidate_id,
                ),
            )
            conn.commit()
            conn.close()
            flash("Candidate updated.", "success")
            return redirect(url_for("hiring.index"))
        cursor.execute("SELECT * FROM hiring_candidates WHERE id = ?", (candidate_id,))
        candidate = cursor.fetchone()
    conn.close()
    return render_template(
        "hiring/candidate_form.html",
        candidate=candidate,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        page_heading="Edit candidate",
    )