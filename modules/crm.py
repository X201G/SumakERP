from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from database import get_connection
from modules.auth import require_system

crm = Blueprint("crm", __name__)
crm.before_request(require_system("crm"))


@crm.app_template_filter("crm_date")
def format_crm_date(value):
    if not value:
        return ""
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return value


STAGES = ("qualified", "trial", "quoted", "negotiating", "won", "lost")
STAGE_LABELS = {
    "qualified": "Qualified",
    "trial": "Product trial",
    "quoted": "Quoted",
    "negotiating": "Negotiating",
    "won": "Won",
    "lost": "Lost",
}
STAGE_PROBABILITIES = {
    "qualified": 0.15,
    "trial": 0.35,
    "quoted": 0.5,
    "negotiating": 0.75,
    "won": 1,
    "lost": 0,
}
PO_STATUSES = ("not_received", "received", "processing", "fulfilled", "cancelled")
PO_STATUS_LABELS = {
    "not_received": "Awaiting PO",
    "received": "Received",
    "processing": "Processing",
    "fulfilled": "Fulfilled",
    "cancelled": "Cancelled",
}
ACTIVITY_TYPES = ("visit", "trial", "call", "email", "meeting", "note")
ACTIVITY_LABELS = {
    "visit": "Customer visit",
    "trial": "Product trial",
    "call": "Call",
    "email": "Email",
    "meeting": "Meeting",
    "note": "Note",
}


def _valid_date(value):
    if not value:
        return True
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _quote_stage(status):
    if status in ("accepted", "won"):
        return "won"
    if status in ("rejected", "lost"):
        return "lost"
    return "quoted"


def _load_form_options(cursor):
    cursor.execute("SELECT id, name, phone, email FROM customers ORDER BY name COLLATE NOCASE")
    customers = cursor.fetchall()
    cursor.execute("SELECT id, product_name, sale_price FROM products ORDER BY product_name COLLATE NOCASE")
    products = cursor.fetchall()
    cursor.execute(
        """SELECT sales_quotations.id, sales_quotations.quotation_no,
                  sales_quotations.customer_id, sales_quotations.total_amount,
                  sales_quotations.status, customers.name AS customer_name,
                  (SELECT sales_quotation_items.product_id FROM sales_quotation_items
                   WHERE sales_quotation_items.quotation_id = sales_quotations.id
                   ORDER BY sales_quotation_items.id LIMIT 1) AS first_product_id
           FROM sales_quotations
           JOIN customers ON customers.id = sales_quotations.customer_id
           ORDER BY sales_quotations.quotation_date DESC, sales_quotations.id DESC"""
    )
    quotations = cursor.fetchall()
    cursor.execute(
        "SELECT id, full_name, username FROM users WHERE system = 'crm' AND is_active = 1 ORDER BY full_name, username"
    )
    users = cursor.fetchall()
    return customers, products, quotations, users


def sync_quotation_opportunity(cursor, quotation_id):
    cursor.execute(
        """SELECT sales_quotations.*, customers.name AS customer_name,
                  (SELECT sales_quotation_items.product_id FROM sales_quotation_items
                   WHERE sales_quotation_items.quotation_id = sales_quotations.id
                   ORDER BY sales_quotation_items.id LIMIT 1) AS first_product_id
           FROM sales_quotations JOIN customers ON customers.id = sales_quotations.customer_id
           WHERE sales_quotations.id = ?""",
        (quotation_id,),
    )
    quotation = cursor.fetchone()
    if not quotation:
        return
    cursor.execute(
        """SELECT id FROM users WHERE system = 'crm' AND is_active = 1
           ORDER BY CASE WHEN role = 'admin' THEN 0 ELSE 1 END, id LIMIT 1"""
    )
    crm_owner = cursor.fetchone()
    owner_id = crm_owner["id"] if crm_owner else None
    if owner_id is None:
        return
    cursor.execute(
        """INSERT OR IGNORE INTO crm_opportunities
           (title, customer_id, product_id, quotation_id, stage, potential_value, created_by, owner_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            f"{quotation['quotation_no']} - {quotation['customer_name']}",
            quotation["customer_id"],
            quotation["first_product_id"],
            quotation_id,
            _quote_stage(quotation["status"]),
            quotation["total_amount"] or 0,
            owner_id,
            owner_id,
        ),
    )


@crm.route("/crm")
def index():
    search = request.args.get("search", "").strip()
    stage_filter = request.args.get("stage", "")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT stage, COUNT(*) AS deal_count, COALESCE(SUM(potential_value), 0) AS amount
           FROM crm_opportunities GROUP BY stage"""
    )
    totals = {row["stage"]: row for row in cursor.fetchall()}
    pipeline_value = sum(
        row["amount"] * STAGE_PROBABILITIES.get(stage, 0)
        for stage, row in totals.items()
        if stage not in ("won", "lost")
    )
    open_count = sum(row["deal_count"] for stage, row in totals.items() if stage not in ("won", "lost"))
    won_value = totals.get("won", {"amount": 0})["amount"]

    filters = []
    params = []
    if stage_filter in STAGES:
        filters.append("crm_opportunities.stage = ?")
        params.append(stage_filter)
    if search:
        term = f"%{search}%"
        filters.append(
            "(crm_opportunities.title LIKE ? OR customers.name LIKE ? OR products.product_name LIKE ? OR sales_quotations.quotation_no LIKE ?)"
        )
        params.extend([term, term, term, term])
    where = " WHERE " + " AND ".join(filters) if filters else ""
    cursor.execute(
        """SELECT crm_opportunities.*, customers.name AS customer_name,
                  customers.phone AS customer_phone, customers.email AS customer_email,
                  products.product_name, sales_quotations.quotation_no,
                  users.full_name AS owner_name
           FROM crm_opportunities
           JOIN customers ON customers.id = crm_opportunities.customer_id
           LEFT JOIN products ON products.id = crm_opportunities.product_id
           LEFT JOIN sales_quotations ON sales_quotations.id = crm_opportunities.quotation_id
           LEFT JOIN users ON users.id = crm_opportunities.owner_id"""
        + where
        + " ORDER BY CASE WHEN crm_opportunities.stage IN ('won', 'lost') THEN 1 ELSE 0 END, crm_opportunities.next_follow_up IS NULL, crm_opportunities.next_follow_up, crm_opportunities.updated_at DESC",
        params,
    )
    opportunities = cursor.fetchall()
    cursor.execute(
        """SELECT crm_opportunities.id, crm_opportunities.title, crm_opportunities.next_follow_up,
                  crm_opportunities.stage, customers.name AS customer_name
           FROM crm_opportunities JOIN customers ON customers.id = crm_opportunities.customer_id
           WHERE crm_opportunities.stage NOT IN ('won', 'lost')
             AND crm_opportunities.next_follow_up IS NOT NULL
             AND crm_opportunities.next_follow_up <= ?
           ORDER BY crm_opportunities.next_follow_up, crm_opportunities.id LIMIT 8""",
        (date.today().isoformat(),),
    )
    due_follow_ups = cursor.fetchall()
    conn.close()
    return render_template(
        "crm/index.html",
        opportunities=opportunities,
        due_follow_ups=due_follow_ups,
        totals=totals,
        pipeline_value=pipeline_value,
        open_count=open_count,
        won_value=won_value,
        stages=STAGES,
        stage_labels=STAGE_LABELS,
        stage_filter=stage_filter,
        search=search,
        today=date.today().isoformat(),
    )


@crm.route("/crm/opportunities/new", methods=["GET", "POST"])
def create_opportunity():
    conn = get_connection()
    cursor = conn.cursor()
    customers, products, quotations, users = _load_form_options(cursor)

    if request.method == "POST":
        quotation_id = request.form.get("quotation_id", type=int)
        quotation = None
        product_id = request.form.get("product_id", type=int) or None
        customer_id = request.form.get("customer_id", type=int)
        potential_value_raw = request.form.get("potential_value", "0").strip() or "0"
        title = request.form.get("title", "").strip()
        stage = request.form.get("stage", "qualified")
        owner_id = request.form.get("owner_id", type=int) or session["user_id"]

        if quotation_id:
            cursor.execute(
                """SELECT sales_quotations.*, customers.name AS customer_name
                   FROM sales_quotations JOIN customers ON customers.id = sales_quotations.customer_id
                   WHERE sales_quotations.id = ?""",
                (quotation_id,),
            )
            quotation = cursor.fetchone()
            if quotation:
                cursor.execute("SELECT id FROM crm_opportunities WHERE quotation_id = ?", (quotation_id,))
                existing = cursor.fetchone()
                if existing:
                    conn.close()
                    return redirect(url_for("crm.detail", opportunity_id=existing["id"]))
                customer_id = quotation["customer_id"]
                title = title or f"{quotation['quotation_no']} - {quotation['customer_name']}"
                potential_value_raw = str(quotation["total_amount"] or 0)
                stage = _quote_stage(quotation["status"])
                cursor.execute(
                    "SELECT product_id FROM sales_quotation_items WHERE quotation_id = ? ORDER BY id LIMIT 1",
                    (quotation_id,),
                )
                first_product = cursor.fetchone()
                product_id = first_product["product_id"] if first_product else None

        try:
            potential_value = float(potential_value_raw)
        except ValueError:
            potential_value = -1
        expected_close_date = request.form.get("expected_close_date", "").strip() or None
        next_follow_up = request.form.get("next_follow_up", "").strip() or None
        customer_exists = any(customer["id"] == customer_id for customer in customers)
        owner_exists = any(user["id"] == owner_id for user in users)

        if quotation_id and quotation is None:
            flash("Select a valid quotation.", "danger")
        elif not customer_exists:
            flash("Select a customer.", "danger")
        elif not title:
            flash("Add an opportunity name.", "danger")
        elif potential_value < 0:
            flash("Enter a valid potential amount.", "danger")
        elif stage not in STAGES:
            flash("Select a valid stage.", "danger")
        elif not _valid_date(expected_close_date) or not _valid_date(next_follow_up):
            flash("Enter valid opportunity dates.", "danger")
        elif not owner_exists:
            flash("Select an active distribution user.", "danger")
        else:
            cursor.execute(
                """INSERT INTO crm_opportunities
                   (title, customer_id, product_id, quotation_id, stage, potential_value,
                    expected_close_date, next_follow_up, notes, owner_id, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    title,
                    customer_id,
                    product_id,
                    quotation_id,
                    stage,
                    round(potential_value, 2),
                    expected_close_date,
                    next_follow_up,
                    request.form.get("notes", "").strip(),
                    owner_id,
                    session["user_id"],
                ),
            )
            opportunity_id = cursor.lastrowid
            conn.commit()
            conn.close()
            flash("Opportunity created.", "success")
            return redirect(url_for("crm.detail", opportunity_id=opportunity_id))

    selected_quotation_id = request.args.get("quotation_id", type=int)
    selected_quotation = next((quote for quote in quotations if quote["id"] == selected_quotation_id), None)
    conn.close()
    return render_template(
        "crm/opportunity_form.html",
        customers=customers,
        products=products,
        quotations=quotations,
        users=users,
        opportunity=None,
        stages=STAGES,
        stage_labels=STAGE_LABELS,
        selected_quotation=selected_quotation,
        session_user_id=session["user_id"],
        today=date.today().isoformat(),
        form=request.form if request.method == "POST" else {},
    )


@crm.route("/crm/opportunities/<int:opportunity_id>")
def detail(opportunity_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT crm_opportunities.*, customers.name AS customer_name,
                  customers.phone AS customer_phone, customers.email AS customer_email,
                  customers.city AS customer_city, products.product_name,
                  sales_quotations.quotation_no, sales_quotations.status AS quotation_status,
                  users.full_name AS owner_name
           FROM crm_opportunities
           JOIN customers ON customers.id = crm_opportunities.customer_id
           LEFT JOIN products ON products.id = crm_opportunities.product_id
           LEFT JOIN sales_quotations ON sales_quotations.id = crm_opportunities.quotation_id
           LEFT JOIN users ON users.id = crm_opportunities.owner_id
           WHERE crm_opportunities.id = ?""",
        (opportunity_id,),
    )
    opportunity = cursor.fetchone()
    if not opportunity:
        conn.close()
        return "Opportunity not found", 404
    cursor.execute(
        """SELECT crm_activities.*, products.product_name, users.full_name AS author_name
           FROM crm_activities
           LEFT JOIN products ON products.id = crm_activities.product_id
           LEFT JOIN users ON users.id = crm_activities.created_by
           WHERE crm_activities.opportunity_id = ?
           ORDER BY crm_activities.activity_date DESC, crm_activities.id DESC""",
        (opportunity_id,),
    )
    activities = cursor.fetchall()
    cursor.execute(
        """SELECT id, quotation_no, quotation_date, valid_till, status, total_amount
           FROM sales_quotations WHERE customer_id = ?
           ORDER BY quotation_date DESC, id DESC LIMIT 8""",
        (opportunity["customer_id"],),
    )
    customer_quotations = cursor.fetchall()
    cursor.execute(
        """SELECT id, order_no, order_date, po_no, status, total_amount
           FROM sales_orders WHERE customer_id = ?
           ORDER BY order_date DESC, id DESC LIMIT 8""",
        (opportunity["customer_id"],),
    )
    customer_orders = cursor.fetchall()
    cursor.execute("SELECT id, product_name FROM products ORDER BY product_name COLLATE NOCASE")
    products = cursor.fetchall()
    cursor.execute(
        "SELECT id, full_name, username FROM users WHERE system = 'crm' AND is_active = 1 ORDER BY full_name, username"
    )
    users = cursor.fetchall()
    conn.close()
    return render_template(
        "crm/detail.html",
        opportunity=opportunity,
        activities=activities,
        customer_quotations=customer_quotations,
        customer_orders=customer_orders,
        products=products,
        users=users,
        stages=STAGES,
        stage_labels=STAGE_LABELS,
        po_statuses=PO_STATUSES,
        po_status_labels=PO_STATUS_LABELS,
        activity_types=ACTIVITY_TYPES,
        activity_labels=ACTIVITY_LABELS,
        today=date.today().isoformat(),
    )


@crm.route("/crm/opportunities/<int:opportunity_id>/update", methods=["POST"])
def update_opportunity(opportunity_id):
    stage = request.form.get("stage", "")
    po_status = request.form.get("po_status", "")
    expected_close_date = request.form.get("expected_close_date", "").strip() or None
    next_follow_up = request.form.get("next_follow_up", "").strip() or None
    customer_po_no = request.form.get("customer_po_no", "").strip() or None
    owner_id = request.form.get("owner_id", type=int)
    try:
        potential_value = float(request.form.get("potential_value", "0").strip() or "0")
    except ValueError:
        potential_value = -1

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, quotation_id FROM crm_opportunities WHERE id = ?", (opportunity_id,))
    opportunity = cursor.fetchone()
    if not opportunity:
        conn.close()
        return "Opportunity not found", 404
    cursor.execute(
        "SELECT id FROM users WHERE id = ? AND system = 'crm' AND is_active = 1",
        (owner_id,),
    )
    owner_exists = cursor.fetchone() is not None
    if (
        stage not in STAGES
        or po_status not in PO_STATUSES
        or potential_value < 0
        or not _valid_date(expected_close_date)
        or not _valid_date(next_follow_up)
        or not owner_exists
    ):
        conn.close()
        flash("Check the opportunity details and try again.", "danger")
        return redirect(url_for("crm.detail", opportunity_id=opportunity_id))

    if customer_po_no and po_status == "not_received":
        po_status = "received"
    cursor.execute(
        """UPDATE crm_opportunities SET title = ?, product_id = ?, stage = ?, potential_value = ?,
                  expected_close_date = ?, next_follow_up = ?, customer_po_no = ?, po_status = ?,
                  notes = ?, owner_id = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (
            request.form.get("title", "").strip() or "Opportunity",
            request.form.get("product_id", type=int) or None,
            stage,
            round(potential_value, 2),
            expected_close_date,
            next_follow_up,
            customer_po_no,
            po_status,
            request.form.get("notes", "").strip(),
            owner_id,
            opportunity_id,
        ),
    )
    if opportunity["quotation_id"] and stage in ("won", "lost"):
        quote_status = "accepted" if stage == "won" else "rejected"
        cursor.execute(
            "UPDATE sales_quotations SET status = ? WHERE id = ?",
            (quote_status, opportunity["quotation_id"]),
        )
    conn.commit()
    conn.close()
    flash("Opportunity updated.", "success")
    return redirect(url_for("crm.detail", opportunity_id=opportunity_id))


@crm.route("/crm/opportunities/<int:opportunity_id>/activities", methods=["POST"])
def add_activity(opportunity_id):
    activity_type = request.form.get("activity_type", "")
    activity_date = request.form.get("activity_date", "").strip() or date.today().isoformat()
    next_follow_up = request.form.get("next_follow_up", "").strip() or None
    product_id = request.form.get("product_id", type=int) or None
    notes = request.form.get("notes", "").strip()
    if (
        activity_type not in ACTIVITY_TYPES
        or not _valid_date(activity_date)
        or not _valid_date(next_follow_up)
        or not notes
    ):
        flash("Choose an activity, enter a note, and check the dates.", "danger")
        return redirect(url_for("crm.detail", opportunity_id=opportunity_id))

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT product_id FROM crm_opportunities WHERE id = ?", (opportunity_id,))
    opportunity = cursor.fetchone()
    if not opportunity:
        conn.close()
        return "Opportunity not found", 404
    if activity_type == "trial" and not product_id:
        product_id = opportunity["product_id"]
    cursor.execute(
        """INSERT INTO crm_activities
           (opportunity_id, activity_type, activity_date, product_id, outcome, notes, next_follow_up, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            opportunity_id,
            activity_type,
            activity_date,
            product_id,
            request.form.get("outcome", "").strip(),
            notes,
            next_follow_up,
            session["user_id"],
        ),
    )
    if next_follow_up or activity_type == "trial":
        cursor.execute(
            """UPDATE crm_opportunities
               SET next_follow_up = COALESCE(?, next_follow_up),
                   product_id = CASE WHEN ? = 'trial' AND ? IS NOT NULL THEN ? ELSE product_id END,
                   stage = CASE WHEN ? = 'trial' AND stage NOT IN ('won', 'lost') THEN 'trial' ELSE stage END,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (next_follow_up, activity_type, product_id, product_id, activity_type, opportunity_id),
        )
    conn.commit()
    conn.close()
    flash("Activity logged.", "success")
    return redirect(url_for("crm.detail", opportunity_id=opportunity_id))