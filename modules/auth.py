from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_connection

auth = Blueprint("auth", __name__)

ROLES = ["admin", "sales", "purchase", "store", "accounts"]

# ============================================
# ACCESS CONTROL HELPERS
# ============================================

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("auth.login", next=request.path))
            if session.get("role") != "admin" and session.get("role") not in roles:
                flash("You don't have permission to access that.", "danger")
                return redirect(url_for("index"))
            return view(*args, **kwargs)
        return wrapped
    return decorator

def require_roles(*roles):
    """Blueprint-level guard: attach with `blueprint.before_request(require_roles(...))`."""
    def check():
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.path))
        if session.get("role") != "admin" and session.get("role") not in roles:
            flash("You don't have permission to access that section.", "danger")
            return redirect(url_for("index"))
    return check


def require_login():
    """Blueprint-level guard: just checks you're logged in — no role restriction yet."""
    if not session.get("user_id"):
        return redirect(url_for("auth.login", next=request.path))


# ============================================
# LOGIN / LOGOUT
# ============================================

def require_system(system_name):
    """Blueprint-level guard: must be logged in AND logged into this specific system."""
    def check():
        if not session.get("user_id"):
            return redirect(url_for("auth.login", system=system_name, next=request.path))
        if session.get("system") != system_name:
            flash("That section belongs to a different login.", "danger")
            return redirect(url_for("auth.login", system=system_name))
    return check


def system_required(system_name):
    """Route decorator version of require_system, for routes defined directly in app.py."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("auth.login", system=system_name, next=request.path))
            if session.get("system") != system_name:
                flash("That section belongs to a different login.", "danger")
                return redirect(url_for("auth.login", system=system_name))
            return view(*args, **kwargs)
        return wrapped
    return decorator


@auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        system = request.form["system"]
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid username or password.", "danger")
            return redirect(url_for("auth.login", system=system))
        if not user["is_active"]:
            flash("This account has been deactivated.", "danger")
            return redirect(url_for("auth.login", system=system))
        if user["system"] != system:
            flash("This login isn't set up for that system.", "danger")
            return redirect(url_for("auth.login", system=system))
        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["full_name"] = user["full_name"] or user["username"]
        session["role"] = user["role"]
        session["system"] = user["system"]
        flash(f"Welcome back, {session['full_name']}!", "success")
        next_url = request.args.get("next")
        if next_url:
            return redirect(next_url)
        if system == "manufacturing":
            return redirect(url_for("manufacturing.index"))
        return redirect(url_for("index"))

    system = request.args.get("system")
    if not system:
        return render_template("auth/login_chooser.html")
    return render_template("auth/login.html", system=system)

@auth.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))

# ============================================
# USER MANAGEMENT (admin only)
# ============================================

@auth.route("/users")
@roles_required("admin")
def users_list():
    system = session.get("system", "distribution")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE system = ? ORDER BY username", (system,))
    users = cursor.fetchall()
    conn.close()
    return render_template("auth/users.html", users=users, roles=ROLES, system=system)

@auth.route("/users/add", methods=["GET", "POST"])
@roles_required("admin")
def add_user():
    if request.method == "POST":
        username = request.form["username"].strip()
        full_name = request.form.get("full_name")
        role = request.form.get("role") or "staff"
        password = request.form["password"]
        system = session.get("system", "distribution")
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name, role, system) VALUES (?,?,?,?,?)",
                (username, generate_password_hash(password), full_name, role, system)
            )
            conn.commit()
            flash("User added successfully!", "success")
        except Exception:
            flash("That username is already taken.", "danger")
        conn.close()
        return redirect(url_for("auth.users_list"))
    return render_template("auth/users.html", users=[], roles=ROLES, system=session.get("system", "distribution"))

@auth.route("/users/edit/<int:user_id>", methods=["GET", "POST"])
@roles_required("admin")
def edit_user(user_id):
    system = session.get("system", "distribution")
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        full_name = request.form.get("full_name")
        role = request.form.get("role") or "staff"
        is_active = 1 if request.form.get("is_active") else 0
        password = request.form.get("password")
        if password:
            cursor.execute(
                "UPDATE users SET full_name=?, role=?, is_active=?, password_hash=? WHERE id=? AND system=?",
                (full_name, role, is_active, generate_password_hash(password), user_id, system)
            )
        else:
            cursor.execute(
                "UPDATE users SET full_name=?, role=?, is_active=? WHERE id=? AND system=?",
                (full_name, role, is_active, user_id, system)
            )
        conn.commit()
        conn.close()
        flash("User updated successfully!", "success")
        return redirect(url_for("auth.users_list"))
    cursor.execute("SELECT * FROM users WHERE id = ? AND system = ?", (user_id, system))
    user = cursor.fetchone()
    conn.close()
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("auth.users_list"))
    return render_template("auth/users.html", users=[], user=user, roles=ROLES, editing=True, system=system)

@auth.route("/users/delete/<int:user_id>")
@roles_required("admin")
def delete_user(user_id):
    if user_id == session.get("user_id"):
        flash("You can't delete your own account while logged in.", "danger")
        return redirect(url_for("auth.users_list"))
    system = session.get("system", "distribution")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id = ? AND system = ?", (user_id, system))
    conn.commit()
    conn.close()
    flash("User deleted successfully!", "danger")
    return redirect(url_for("auth.users_list"))
