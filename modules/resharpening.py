"""
SUMAK ERP - Manufacturing: Resharpening.

Kept as a fully separate PROCESS from job-work manufacturing (its own
mfg_resharpen_* tables/routes/templates) since a resharpen batch starts from
a tool the CUSTOMER sends in - the mirror image of a job-work batch, which
starts from SUMAK's own rod stock. Only the Customer/Tool/Supplier masters
are shared with the rest of Manufacturing.

Invoices/payments for resharpening still live in their own tables (mfg_resharpen_invoices /
mfg_resharpen_sales_payments) to avoid batch_id/invoice_id ambiguity with the job-work
tables, but the ACCOUNTING surface (Outstanding Report, Day Book, Party Statement, Payments
Received) is unified in modules/manufacturing_accounting.py - by request, that part is meant
to be one shared view across both processes, not two separate ones to check.
"""
from flask import Blueprint
from flask import render_template, request, redirect, url_for, flash
from database import get_connection
from modules.auth import require_system
from modules.permissions import enforce_permissions
from datetime import date, timedelta

resharpening = Blueprint("resharpening", __name__)
resharpening.before_request(require_system("manufacturing"))
resharpening.before_request(enforce_permissions)

RESHARPEN_STAGES = [
    {"key": "resharpening", "label": "Resharpening"},
    {"key": "coating", "label": "Recoating"},
]

def _final_price(price, discount_percent):
    price = float(price or 0)
    discount_percent = float(discount_percent or 0)
    return round(price * (1 - discount_percent / 100), 2)


def stage_label_for(keys):
    labels = [s["label"] for s in RESHARPEN_STAGES if s["key"] in keys]
    if len(labels) == len(RESHARPEN_STAGES):
        return "Full Service (Resharpening + Recoating)"
    return " + ".join(labels)


def generate_batch_no(batch_date, cursor):
    date_part = batch_date.replace("-", "")
    cursor.execute("SELECT batch_no FROM mfg_resharpen_batches WHERE batch_no LIKE ?", (f"RS-{date_part}-%",))
    existing_numbers = []
    for row in cursor.fetchall():
        try:
            existing_numbers.append(int(row["batch_no"].split("-")[-1]))
        except ValueError:
            pass
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"RS-{date_part}-{str(next_number).zfill(4)}"


CHALLAN_PREFIX = {"customer_inward": "RSIC", "vendor_jobwork": "RSJC", "customer_delivery": "RSDC"}


def generate_challan_no(challan_type, challan_date, cursor):
    prefix = CHALLAN_PREFIX.get(challan_type, "RSC")
    date_part = challan_date.replace("-", "")
    cursor.execute("SELECT challan_no FROM mfg_resharpen_challans WHERE challan_no LIKE ?", (f"{prefix}-{date_part}-%",))
    existing_numbers = []
    for row in cursor.fetchall():
        try:
            existing_numbers.append(int(row["challan_no"].split("-")[-1]))
        except ValueError:
            pass
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"{prefix}-{date_part}-{str(next_number).zfill(4)}"


def generate_invoice_no(invoice_date, cursor):
    date_part = invoice_date.replace("-", "")
    cursor.execute("SELECT invoice_no FROM mfg_resharpen_invoices WHERE invoice_no LIKE ?", (f"RSINV-{date_part}-%",))
    existing_numbers = []
    for row in cursor.fetchall():
        try:
            existing_numbers.append(int(row["invoice_no"].split("-")[-1]))
        except ValueError:
            pass
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"RSINV-{date_part}-{str(next_number).zfill(4)}"


def generate_purchase_invoice_no(invoice_date, cursor):
    """Same numbering as manufacturing.py's copy, since resharpen vendor invoices go into
    the same shared mfg_purchase_invoices table (see create_vendor_invoice below)."""
    date_part = invoice_date.replace("-", "")
    cursor.execute("SELECT invoice_no FROM mfg_purchase_invoices WHERE invoice_no LIKE ?", (f"MPI-{date_part}-%",))
    existing_numbers = []
    for row in cursor.fetchall():
        try:
            existing_numbers.append(int(row["invoice_no"].split("-")[-1]))
        except ValueError:
            pass
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"MPI-{date_part}-{str(next_number).zfill(4)}"


def get_sold_qty(cursor, customer_id, tool_id):
    """How many of this tool SUMAK has ever sold this customer (job-work invoices) -
    a resharpen batch can't hold more of a tool than the customer actually owns. If this
    is 0, we've never sold them this tool - that's a pure resharpening-only relationship
    (their own tool from elsewhere), so no cap applies at all."""
    cursor.execute("""
        SELECT COALESCE(SUM(mfg_invoice_items.quantity), 0) as total
        FROM mfg_invoice_items
        JOIN mfg_invoices ON mfg_invoice_items.invoice_id = mfg_invoices.id
        JOIN mfg_job_batches ON mfg_invoice_items.batch_id = mfg_job_batches.id
        WHERE mfg_invoices.customer_id = ? AND mfg_job_batches.tool_id = ?
    """, (customer_id, tool_id))
    return cursor.fetchone()["total"]


def get_resharpen_outstanding_qty(cursor, customer_id, tool_id):
    """Pieces of this tool currently physically with us for this customer across all their
    resharpen batches - received but not yet delivered back (delivered pieces free up the
    quota again, since they're the same physical tools coming back another time)."""
    cursor.execute("SELECT id, quantity FROM mfg_resharpen_batches WHERE customer_id = ? AND tool_id = ?", (customer_id, tool_id))
    batches = cursor.fetchall()
    total_outstanding = 0
    for b in batches:
        cursor.execute("""
            SELECT COALESCE(SUM(mfg_resharpen_challan_items.quantity), 0) as total
            FROM mfg_resharpen_challan_items
            JOIN mfg_resharpen_challans ON mfg_resharpen_challan_items.challan_id = mfg_resharpen_challans.id
            WHERE mfg_resharpen_challan_items.batch_id = ? AND mfg_resharpen_challans.challan_type = 'customer_delivery'
        """, (b["id"],))
        delivered = cursor.fetchone()["total"]
        total_outstanding += max(0, b["quantity"] - delivered)
    return total_outstanding


def compute_resharpen_stock(cursor, tool_id):
    cursor.execute("SELECT * FROM mfg_resharpen_batches WHERE tool_id = ?", (tool_id,))
    batches = cursor.fetchall()

    not_started_qty = 0
    stage_qty = {"resharpening": 0, "coating": 0}
    ready_qty = {"resharpening": 0, "coating": 0}
    completed_qty = 0

    for b in batches:
        cursor.execute("SELECT * FROM mfg_resharpen_stages WHERE batch_id = ?", (b["id"],))
        ops = cursor.fetchall()
        if not ops:
            not_started_qty += b["quantity"]
            continue
        covered = set()
        any_pending = False
        for op in ops:
            keys = op["stages"].split(",")
            covered.update(keys)
            if not op["receive_date"]:
                any_pending = True
                for k in keys:
                    if k in stage_qty:
                        stage_qty[k] += op["quantity_sent"] or 0
        if any_pending:
            continue
        missing = [s["key"] for s in RESHARPEN_STAGES if s["key"] not in covered]
        if not missing:
            # smallest quantity_received across this batch's stages, not the originally
            # received-from-customer quantity - so rejects during resharpening don't
            # silently inflate the completed-stock count (mirrors compute_tool_stock)
            received_qtys = [op["quantity_received"] for op in ops if op["quantity_received"]]
            completed_qty += min(received_qtys) if received_qtys else b["quantity"]
        else:
            ready_qty[missing[0]] += b["quantity"]

    cursor.execute("""
        SELECT COALESCE(SUM(mfg_resharpen_challan_items.quantity), 0) as total
        FROM mfg_resharpen_challan_items
        JOIN mfg_resharpen_challans ON mfg_resharpen_challan_items.challan_id = mfg_resharpen_challans.id
        JOIN mfg_resharpen_batches ON mfg_resharpen_challan_items.batch_id = mfg_resharpen_batches.id
        WHERE mfg_resharpen_batches.tool_id = ? AND mfg_resharpen_challans.challan_type = 'customer_delivery'
    """, (tool_id,))
    delivered_qty = cursor.fetchone()["total"]

    return {
        "not_started": not_started_qty, "stage_qty": stage_qty, "ready_qty": ready_qty,
        "completed_in_hand": round(completed_qty - delivered_qty, 2), "delivered": delivered_qty
    }


# ============================================
# BATCHES
# ============================================

@resharpening.route("/manufacturing/resharpen/batches")
def batches_list():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    offset = (page - 1) * per_page
    q = request.args.get("q", "")
    q_search = q.strip()
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")

    conn = get_connection()
    cursor = conn.cursor()

    where_parts = []
    params = []
    if q_search:
        where_parts.append("(mfg_resharpen_batches.batch_no LIKE ? OR mfg_tools.tool_name LIKE ? OR customers.name LIKE ?)")
        params += [f"%{q_search}%", f"%{q_search}%", f"%{q_search}%"]
    if from_date:
        where_parts.append("mfg_resharpen_batches.batch_date >= ?")
        params.append(from_date)
    if to_date:
        where_parts.append("mfg_resharpen_batches.batch_date <= ?")
        params.append(to_date)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    cursor.execute(f"""
        SELECT mfg_resharpen_batches.*, mfg_tools.tool_name, mfg_tools.tool_type, customers.name as customer_name
        FROM mfg_resharpen_batches
        JOIN mfg_tools ON mfg_resharpen_batches.tool_id = mfg_tools.id
        JOIN customers ON mfg_resharpen_batches.customer_id = customers.id
        {where}
        ORDER BY mfg_resharpen_batches.batch_date DESC, mfg_resharpen_batches.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    batches = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_resharpen_batches
        JOIN mfg_tools ON mfg_resharpen_batches.tool_id = mfg_tools.id
        JOIN customers ON mfg_resharpen_batches.customer_id = customers.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    batch_list = []
    for b in batches:
        cursor.execute("SELECT * FROM mfg_resharpen_stages WHERE batch_id = ? ORDER BY id", (b["id"],))
        ops = cursor.fetchall()
        if not ops:
            status, status_class = "Not Started", "neutral"
        else:
            covered = set()
            for op in ops:
                covered.update(op["stages"].split(","))
            any_pending = any(not op["receive_date"] for op in ops)
            if any_pending:
                latest = ops[-1]
                status = stage_label_for(latest["stages"].split(",")) + " - At Vendor"
                status_class = "warning"
            elif len(covered) == len(RESHARPEN_STAGES):
                status, status_class = "Complete", "success"
            else:
                status, status_class = "In Progress", "info"
        batch_list.append({"batch": b, "status": status, "status_class": status_class})

    conn.close()
    return render_template("manufacturing/resharpen_batches.html", batch_list=batch_list, page=page, total_pages=total_pages, total=total, q=q, from_date=from_date, to_date=to_date)


@resharpening.route("/manufacturing/resharpen/batches/add", methods=["GET", "POST"])
def add_batch():
    conn = get_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        batch_date = request.form["batch_date"]
        tool_id = request.form["tool_id"]
        customer_id = request.form.get("customer_id") or None
        quantity = int(request.form["quantity"])
        transporter_id = request.form.get("transporter_id") or None
        remarks = request.form.get("remarks")

        if not customer_id:
            flash("Please select a customer.", "danger")
            conn.close()
            return redirect(url_for("resharpening.add_batch"))

        sold_qty = get_sold_qty(cursor, customer_id, tool_id)
        if sold_qty > 0:
            outstanding = get_resharpen_outstanding_qty(cursor, customer_id, tool_id)
            if outstanding + quantity > sold_qty:
                remaining = max(0, sold_qty - outstanding)
                flash(f"Cannot receive {quantity} for resharpening - this customer has bought only {sold_qty} of this tool from us, and {outstanding} {'is' if outstanding == 1 else 'are'} already with us. At most {remaining} more can be received right now.", "danger")
                conn.close()
                return redirect(url_for("resharpening.add_batch"))

        cursor.execute("BEGIN IMMEDIATE")
        batch_no = generate_batch_no(batch_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_resharpen_batches (batch_no, batch_date, tool_id, customer_id, quantity, remarks)
            VALUES (?,?,?,?,?,?)
        """, (batch_no, batch_date, tool_id, customer_id, quantity, remarks))
        batch_id = cursor.lastrowid

        # the batch's creation IS the "received from customer" event - auto-generate an
        # inward challan as the paper acknowledgement, same pattern as how a vendor
        # job-work challan is auto-created when send_operation() has a vendor
        challan_no = generate_challan_no("customer_inward", batch_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_resharpen_challans (challan_no, challan_date, challan_type, party_type, party_id, transporter_id, remarks)
            VALUES (?,?,?,?,?,?,?)
        """, (challan_no, batch_date, "customer_inward", "customer", customer_id, transporter_id, remarks))
        challan_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO mfg_resharpen_challan_items (challan_id, batch_id, stage_id, quantity)
            VALUES (?,?,?,?)
        """, (challan_id, batch_id, None, quantity))

        conn.commit()
        conn.close()
        flash(f"Batch {batch_no} created - inward receipt {challan_no} generated!", "success")
        return redirect(url_for("resharpening.print_challan", challan_id=challan_id))

    cursor.execute("SELECT id, item_code, tool_name, tool_type, customer_id FROM mfg_tools ORDER BY tool_name")
    tools = cursor.fetchall()
    cursor.execute("SELECT id, name FROM customers ORDER BY name")
    customers = cursor.fetchall()
    cursor.execute("SELECT id, name FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/resharpen_add_batch.html", tools=tools, customers=customers, transporters=transporters)


@resharpening.route("/manufacturing/resharpen/api/tool-quota/<int:customer_id>/<int:tool_id>")
def api_tool_quota(customer_id, tool_id):
    conn = get_connection()
    cursor = conn.cursor()
    sold_qty = get_sold_qty(cursor, customer_id, tool_id)
    outstanding = get_resharpen_outstanding_qty(cursor, customer_id, tool_id) if sold_qty > 0 else 0
    conn.close()
    return {"sold_qty": sold_qty, "outstanding": outstanding, "unlimited": sold_qty == 0,
            "remaining": max(0, sold_qty - outstanding)}


@resharpening.route("/manufacturing/resharpen/batches/<int:batch_id>")
def batch_detail(batch_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_resharpen_batches.*, mfg_tools.tool_name, mfg_tools.tool_type, customers.name as customer_name
        FROM mfg_resharpen_batches
        JOIN mfg_tools ON mfg_resharpen_batches.tool_id = mfg_tools.id
        JOIN customers ON mfg_resharpen_batches.customer_id = customers.id
        WHERE mfg_resharpen_batches.id = ?
    """, (batch_id,))
    batch = cursor.fetchone()

    cursor.execute("""
        SELECT mfg_resharpen_stages.*, suppliers.name as vendor_name, mfg_purchase_invoices.invoice_no as vendor_invoice_no
        FROM mfg_resharpen_stages
        LEFT JOIN suppliers ON mfg_resharpen_stages.vendor_id = suppliers.id
        LEFT JOIN mfg_purchase_invoices ON mfg_resharpen_stages.purchase_invoice_id = mfg_purchase_invoices.id
        WHERE batch_id = ?
        ORDER BY id
    """, (batch_id,))
    operations = cursor.fetchall()

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    cursor.execute("SELECT id, name FROM transporters ORDER BY name")
    transporters = cursor.fetchall()

    covered_keys = set()
    for op in operations:
        covered_keys.update(op["stages"].split(","))
    not_started = [s for s in RESHARPEN_STAGES if s["key"] not in covered_keys]

    total_cost = round(sum(op["rate"] for op in operations if op["rate"]), 2)

    cursor.execute("""
        SELECT mfg_resharpen_challan_items.stage_id, mfg_resharpen_challan_items.challan_id, mfg_resharpen_challans.challan_no
        FROM mfg_resharpen_challan_items
        JOIN mfg_resharpen_challans ON mfg_resharpen_challan_items.challan_id = mfg_resharpen_challans.id
        WHERE mfg_resharpen_challan_items.batch_id = ? AND mfg_resharpen_challan_items.stage_id IS NOT NULL
    """, (batch_id,))
    op_challans = {r["stage_id"]: {"id": r["challan_id"], "no": r["challan_no"]} for r in cursor.fetchall()}

    cursor.execute("""
        SELECT mfg_resharpen_challans.*
        FROM mfg_resharpen_challans
        JOIN mfg_resharpen_challan_items ON mfg_resharpen_challan_items.challan_id = mfg_resharpen_challans.id
        WHERE mfg_resharpen_challan_items.batch_id = ? AND mfg_resharpen_challans.challan_type = 'customer_inward'
        ORDER BY mfg_resharpen_challans.id
    """, (batch_id,))
    inward_challans = cursor.fetchall()

    cursor.execute("""
        SELECT mfg_resharpen_challans.*
        FROM mfg_resharpen_challans
        JOIN mfg_resharpen_challan_items ON mfg_resharpen_challan_items.challan_id = mfg_resharpen_challans.id
        WHERE mfg_resharpen_challan_items.batch_id = ? AND mfg_resharpen_challans.challan_type = 'customer_delivery'
        ORDER BY mfg_resharpen_challans.id
    """, (batch_id,))
    customer_challans = cursor.fetchall()

    cursor.execute("""
        SELECT mfg_resharpen_invoices.*
        FROM mfg_resharpen_invoices
        JOIN mfg_resharpen_invoice_items ON mfg_resharpen_invoice_items.invoice_id = mfg_resharpen_invoices.id
        WHERE mfg_resharpen_invoice_items.batch_id = ?
        ORDER BY mfg_resharpen_invoices.id
    """, (batch_id,))
    invoices = cursor.fetchall()
    conn.close()

    def op_view(op):
        stage_keys = op["stages"].split(",")
        return {
            "id": op["id"], "stage_keys": stage_keys, "label": stage_label_for(stage_keys),
            "vendor_name": op["vendor_name"], "vendor_id": op["vendor_id"], "dispatch_date": op["dispatch_date"],
            "quantity_sent": op["quantity_sent"], "receive_date": op["receive_date"],
            "quantity_received": op["quantity_received"], "rate": op["rate"],
            "challan": op_challans.get(op["id"]),
            "purchase_invoice_id": op["purchase_invoice_id"], "vendor_invoice_no": op["vendor_invoice_no"],
        }

    pending_ops = [op_view(op) for op in operations if not op["receive_date"]]
    done_ops = [op_view(op) for op in operations if op["receive_date"]]
    batch_untouched = not operations and not customer_challans and not invoices

    return render_template("manufacturing/resharpen_batch_detail.html",
        batch=batch, suppliers=suppliers, transporters=transporters, not_started=not_started,
        pending_ops=pending_ops, done_ops=done_ops, total_cost=total_cost,
        inward_challans=inward_challans, customer_challans=customer_challans, invoices=invoices,
        batch_untouched=batch_untouched)


def _resharpen_batch_is_untouched(cursor, batch_id):
    cursor.execute("SELECT COUNT(*) as c FROM mfg_resharpen_stages WHERE batch_id=?", (batch_id,))
    if cursor.fetchone()["c"] > 0:
        return False
    cursor.execute("""
        SELECT COUNT(*) as c FROM mfg_resharpen_challan_items
        JOIN mfg_resharpen_challans ON mfg_resharpen_challan_items.challan_id = mfg_resharpen_challans.id
        WHERE mfg_resharpen_challan_items.batch_id=? AND mfg_resharpen_challans.challan_type='customer_delivery'
    """, (batch_id,))
    if cursor.fetchone()["c"] > 0:
        return False
    cursor.execute("SELECT COUNT(*) as c FROM mfg_resharpen_invoice_items WHERE batch_id=?", (batch_id,))
    if cursor.fetchone()["c"] > 0:
        return False
    return True

@resharpening.route("/manufacturing/resharpen/batches/<int:batch_id>/edit", methods=["GET", "POST"])
def edit_batch(batch_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_resharpen_batches WHERE id = ?", (batch_id,))
    batch = cursor.fetchone()
    if not batch:
        conn.close()
        flash("Batch not found.", "danger")
        return redirect(url_for("resharpening.batches_list"))
    untouched = _resharpen_batch_is_untouched(cursor, batch_id)

    if request.method == "POST":
        remarks = request.form.get("remarks")

        if untouched and request.form.get("tool_id"):
            tool_id = request.form["tool_id"]
            customer_id = request.form.get("customer_id") or batch["customer_id"]
            quantity = int(request.form["quantity"])

            sold_qty = get_sold_qty(cursor, customer_id, tool_id)
            if sold_qty > 0:
                cursor.execute("SELECT id, quantity FROM mfg_resharpen_batches WHERE customer_id = ? AND tool_id = ? AND id != ?",
                               (customer_id, tool_id, batch_id))
                other_batches = cursor.fetchall()
                outstanding = 0
                for b in other_batches:
                    cursor.execute("""
                        SELECT COALESCE(SUM(mfg_resharpen_challan_items.quantity), 0) as total
                        FROM mfg_resharpen_challan_items
                        JOIN mfg_resharpen_challans ON mfg_resharpen_challan_items.challan_id = mfg_resharpen_challans.id
                        WHERE mfg_resharpen_challan_items.batch_id = ? AND mfg_resharpen_challans.challan_type = 'customer_delivery'
                    """, (b["id"],))
                    delivered = cursor.fetchone()["total"]
                    outstanding += max(0, b["quantity"] - delivered)
                if outstanding + quantity > sold_qty:
                    remaining = max(0, sold_qty - outstanding)
                    flash(f"Cannot set this to {quantity} - this customer has bought only {sold_qty} of this tool from us, and {outstanding} {'is' if outstanding == 1 else 'are'} already with us elsewhere. At most {remaining} can be set here.", "danger")
                    conn.close()
                    return redirect(url_for("resharpening.edit_batch", batch_id=batch_id))

            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("UPDATE mfg_resharpen_batches SET tool_id=?, customer_id=?, quantity=?, remarks=? WHERE id=?",
                           (tool_id, customer_id, quantity, remarks, batch_id))
            cursor.execute("""
                UPDATE mfg_resharpen_challan_items SET quantity=?
                WHERE batch_id=? AND challan_id IN (SELECT id FROM mfg_resharpen_challans WHERE challan_type='customer_inward')
            """, (quantity, batch_id))
            cursor.execute("""
                UPDATE mfg_resharpen_challans SET party_id=?
                WHERE challan_type='customer_inward' AND id IN (SELECT challan_id FROM mfg_resharpen_challan_items WHERE batch_id=?)
            """, (customer_id, batch_id))
            conn.commit()
            conn.close()
            flash("Batch updated successfully!", "success")
            return redirect(url_for("resharpening.batch_detail", batch_id=batch_id))

        cursor.execute("UPDATE mfg_resharpen_batches SET remarks=? WHERE id=?", (remarks, batch_id))
        conn.commit()
        conn.close()
        flash("Batch updated successfully!", "success")
        return redirect(url_for("resharpening.batch_detail", batch_id=batch_id))

    tools, customers = [], []
    if untouched:
        cursor.execute("SELECT id, item_code, tool_name, tool_type, customer_id FROM mfg_tools ORDER BY tool_name")
        tools = cursor.fetchall()
        cursor.execute("SELECT id, name FROM customers ORDER BY name")
        customers = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/resharpen_edit_batch.html", batch=batch, tools=tools, customers=customers, untouched=untouched)

@resharpening.route("/manufacturing/resharpen/batches/delete/<int:batch_id>")
def delete_batch(batch_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_resharpen_batches WHERE id=?", (batch_id,))
    batch = cursor.fetchone()
    if not batch:
        conn.close()
        flash("Batch not found.", "danger")
        return redirect(url_for("resharpening.batches_list"))
    if not _resharpen_batch_is_untouched(cursor, batch_id):
        conn.close()
        flash("Cannot delete this batch - work has already started (a stage, delivery challan, or invoice references it).", "danger")
        return redirect(url_for("resharpening.batch_detail", batch_id=batch_id))

    cursor.execute("SELECT id FROM mfg_resharpen_challans WHERE id IN (SELECT challan_id FROM mfg_resharpen_challan_items WHERE batch_id=?)", (batch_id,))
    challans = cursor.fetchall()

    cursor.execute("BEGIN IMMEDIATE")
    for c in challans:
        cursor.execute("DELETE FROM mfg_resharpen_challan_items WHERE challan_id=?", (c["id"],))
        cursor.execute("DELETE FROM mfg_resharpen_challans WHERE id=?", (c["id"],))
    cursor.execute("DELETE FROM mfg_resharpen_batches WHERE id=?", (batch_id,))
    conn.commit()
    conn.close()
    flash("Batch deleted successfully!", "danger")
    return redirect(url_for("resharpening.batches_list"))


@resharpening.route("/manufacturing/resharpen/batches/<int:batch_id>/stage/<int:stage_id>/invoice-vendor", methods=["GET", "POST"])
def create_vendor_invoice(batch_id, stage_id):
    """Turns a vendor resharpening/recoating stage's cost into a real Purchase Invoice
    (payable) - manual, pre-filled from the stage's own rate/qty, same as job-work's
    equivalent in manufacturing.py. Goes into the same shared mfg_purchase_invoices table
    (Resharpening has no purchase side of its own), so Purchase Payments/Outstanding/
    Purchase Report pick it up automatically."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_resharpen_stages.*, mfg_resharpen_batches.batch_no, suppliers.name as vendor_name
        FROM mfg_resharpen_stages
        JOIN mfg_resharpen_batches ON mfg_resharpen_stages.batch_id = mfg_resharpen_batches.id
        LEFT JOIN suppliers ON mfg_resharpen_stages.vendor_id = suppliers.id
        WHERE mfg_resharpen_stages.id = ? AND mfg_resharpen_stages.batch_id = ?
    """, (stage_id, batch_id))
    stage = cursor.fetchone()

    if not stage or not stage["vendor_id"]:
        flash("Cannot create a purchase invoice - no vendor recorded for this stage.", "danger")
        conn.close()
        return redirect(url_for("resharpening.batch_detail", batch_id=batch_id))
    if stage["purchase_invoice_id"]:
        flash("This stage is already linked to a purchase invoice.", "danger")
        conn.close()
        return redirect(url_for("resharpening.batch_detail", batch_id=batch_id))

    if request.method == "POST":
        invoice_date = request.form["invoice_date"]
        sup_invoice_no = request.form.get("sup_invoice_no")
        sup_invoice_date = request.form.get("sup_invoice_date") or None
        cr_days = request.form.get("cr_days") or 0
        due_date = request.form.get("due_date") or None
        description = request.form["description"]
        quantity = float(request.form["quantity"])
        rate = float(request.form["rate"])
        tax_rate = float(request.form.get("tax_rate") or 18)
        remarks = request.form.get("remarks")

        bef_tax_amt = round(quantity * rate, 2)
        tax_amount = round(bef_tax_amt * tax_rate / 100, 2)
        amount = round(bef_tax_amt + tax_amount, 2)

        cursor.execute("BEGIN IMMEDIATE")
        invoice_no = generate_purchase_invoice_no(invoice_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_purchase_invoices (invoice_no, invoice_date, supplier_id, sup_invoice_no, sup_invoice_date, cr_days, due_date, remarks, total_amount)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (invoice_no, invoice_date, stage["vendor_id"], sup_invoice_no, sup_invoice_date, cr_days, due_date, remarks, amount))
        invoice_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO mfg_purchase_invoice_items (invoice_id, item_type, description, quantity, rate, tax_rate, bef_tax_amt, tax_amount, amount)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (invoice_id, "service", description, quantity, rate, tax_rate, bef_tax_amt, tax_amount, amount))
        cursor.execute("UPDATE mfg_resharpen_stages SET purchase_invoice_id = ? WHERE id = ?", (invoice_id, stage_id))
        conn.commit()
        conn.close()
        flash(f"Purchase Invoice {invoice_no} created for {stage['vendor_name']}!", "success")
        return redirect(url_for("manufacturing.view_purchase_invoice", invoice_id=invoice_id))

    conn.close()
    default_description = f"Resharpening: {stage_label_for(stage['stages'].split(','))} - Batch {stage['batch_no']}"
    return render_template("manufacturing/add_vendor_service_invoice.html",
        vendor_name=stage["vendor_name"], default_description=default_description,
        default_quantity=stage["quantity_received"] or stage["quantity_sent"], default_rate=stage["rate"] or 0,
        form_action=f"/manufacturing/resharpen/batches/{batch_id}/stage/{stage_id}/invoice-vendor",
        back_url=f"/manufacturing/resharpen/batches/{batch_id}")


@resharpening.route("/manufacturing/resharpen/batches/<int:batch_id>/send", methods=["POST"])
def send_operation(batch_id):
    stages = request.form.getlist("stages")
    vendor_id = request.form.get("vendor_id") or None
    transporter_id = request.form.get("transporter_id") or None
    dispatch_date = request.form["dispatch_date"]
    quantity_sent = int(request.form["quantity_sent"])
    remarks = request.form.get("remarks")

    if not stages:
        flash("Please select at least one stage to send.", "danger")
        return redirect(url_for("resharpening.batch_detail", batch_id=batch_id))

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT quantity FROM mfg_resharpen_batches WHERE id = ?", (batch_id,))
    batch_row = cursor.fetchone()
    if batch_row and quantity_sent > batch_row["quantity"]:
        conn.close()
        flash(f"Cannot send {quantity_sent} - the batch only has {batch_row['quantity']} pieces.", "danger")
        return redirect(url_for("resharpening.batch_detail", batch_id=batch_id))

    cursor.execute("SELECT id FROM mfg_resharpen_stages WHERE batch_id = ? AND receive_date IS NULL", (batch_id,))
    if cursor.fetchone():
        conn.close()
        flash("This batch already has a shipment in progress - receive it before sending another.", "danger")
        return redirect(url_for("resharpening.batch_detail", batch_id=batch_id))

    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute("""
        INSERT INTO mfg_resharpen_stages (batch_id, stages, vendor_id, dispatch_date, quantity_sent, remarks)
        VALUES (?,?,?,?,?,?)
    """, (batch_id, ",".join(stages), vendor_id, dispatch_date, quantity_sent, remarks))
    stage_id = cursor.lastrowid

    if vendor_id:
        challan_no = generate_challan_no("vendor_jobwork", dispatch_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_resharpen_challans (challan_no, challan_date, challan_type, party_type, party_id, transporter_id, remarks)
            VALUES (?,?,?,?,?,?,?)
        """, (challan_no, dispatch_date, "vendor_jobwork", "supplier", vendor_id, transporter_id, remarks))
        challan_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO mfg_resharpen_challan_items (challan_id, batch_id, stage_id, quantity)
            VALUES (?,?,?,?)
        """, (challan_id, batch_id, stage_id, quantity_sent))
        conn.commit()
        conn.close()
        return redirect(url_for("resharpening.print_challan", challan_id=challan_id))

    conn.commit()
    conn.close()
    flash("Sent for " + stage_label_for(stages) + " (in-house)!", "success")
    return redirect(url_for("resharpening.batch_detail", batch_id=batch_id))


@resharpening.route("/manufacturing/resharpen/batches/<int:batch_id>/receive/<int:stage_id>", methods=["POST"])
def receive_operation(batch_id, stage_id):
    receive_date = request.form["receive_date"]
    quantity_received = int(request.form["quantity_received"])
    rate = request.form.get("rate") or None

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT quantity_sent FROM mfg_resharpen_stages WHERE id = ?", (stage_id,))
    stage_row = cursor.fetchone()
    if stage_row and stage_row["quantity_sent"] and quantity_received > stage_row["quantity_sent"]:
        conn.close()
        flash(f"Cannot receive {quantity_received} - only {stage_row['quantity_sent']} were sent.", "danger")
        return redirect(url_for("resharpening.batch_detail", batch_id=batch_id))

    cursor.execute("""
        UPDATE mfg_resharpen_stages SET receive_date=?, quantity_received=?, rate=?
        WHERE id=?
    """, (receive_date, quantity_received, rate, stage_id))

    conn.commit()
    conn.close()
    flash("Marked as received!", "success")
    return redirect(url_for("resharpening.batch_detail", batch_id=batch_id))


@resharpening.route("/manufacturing/resharpen/batches/<int:batch_id>/generate-challan/<int:stage_id>", methods=["POST"])
def generate_challan(batch_id, stage_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_resharpen_stages WHERE id = ?", (stage_id,))
    op = cursor.fetchone()

    if not op or not op["vendor_id"]:
        flash("Cannot generate challan - no vendor recorded for this shipment.", "danger")
        conn.close()
        return redirect(url_for("resharpening.batch_detail", batch_id=batch_id))

    cursor.execute("BEGIN IMMEDIATE")
    challan_no = generate_challan_no("vendor_jobwork", op["dispatch_date"], cursor)
    cursor.execute("""
        INSERT INTO mfg_resharpen_challans (challan_no, challan_date, challan_type, party_type, party_id, remarks)
        VALUES (?,?,?,?,?,?)
    """, (challan_no, op["dispatch_date"], "vendor_jobwork", "supplier", op["vendor_id"], op["remarks"]))
    challan_id = cursor.lastrowid
    cursor.execute("""
        INSERT INTO mfg_resharpen_challan_items (challan_id, batch_id, stage_id, quantity)
        VALUES (?,?,?,?)
    """, (challan_id, batch_id, stage_id, op["quantity_sent"]))

    conn.commit()
    conn.close()
    flash(f"Challan {challan_no} generated!", "success")
    return redirect(url_for("resharpening.batch_detail", batch_id=batch_id))


@resharpening.route("/manufacturing/resharpen/batches/<int:batch_id>/challan/customer/new", methods=["GET", "POST"])
def add_customer_challan(batch_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_resharpen_batches.*, mfg_tools.tool_name, customers.name as customer_name
        FROM mfg_resharpen_batches
        JOIN mfg_tools ON mfg_resharpen_batches.tool_id = mfg_tools.id
        JOIN customers ON mfg_resharpen_batches.customer_id = customers.id
        WHERE mfg_resharpen_batches.id = ?
    """, (batch_id,))
    batch = cursor.fetchone()

    if request.method == "POST":
        challan_date = request.form["challan_date"]
        quantity = int(request.form["quantity"])
        transporter_id = request.form.get("transporter_id") or None
        remarks = request.form.get("remarks")

        available = compute_resharpen_stock(cursor, batch["tool_id"])["completed_in_hand"]
        if quantity > available:
            flash(f"Cannot deliver {quantity} - only {available} completed and in hand for this tool right now.", "danger")
            conn.close()
            return redirect(url_for("resharpening.add_customer_challan", batch_id=batch_id))

        cursor.execute("BEGIN IMMEDIATE")
        challan_no = generate_challan_no("customer_delivery", challan_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_resharpen_challans (challan_no, challan_date, challan_type, party_type, party_id, transporter_id, remarks)
            VALUES (?,?,?,?,?,?,?)
        """, (challan_no, challan_date, "customer_delivery", "customer", batch["customer_id"], transporter_id, remarks))
        challan_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO mfg_resharpen_challan_items (challan_id, batch_id, stage_id, quantity)
            VALUES (?,?,?,?)
        """, (challan_id, batch_id, None, quantity))
        conn.commit()
        conn.close()
        flash(f"Delivery Challan {challan_no} created!", "success")
        return redirect(url_for("resharpening.view_challan", challan_id=challan_id))

    cursor.execute("SELECT id, name FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/resharpen_add_customer_challan.html", batch=batch, transporters=transporters)


# ============================================
# CHALLANS
# ============================================

@resharpening.route("/manufacturing/resharpen/challans/pick-batch-delivery")
def pick_batch_for_delivery():
    """Entry point for '+ New Delivery Challan' from the Resharpen Challans list - same
    reasoning as the job-work version: delivery draws from a per-TOOL completed pool
    (compute_resharpen_stock's completed_in_hand), which add_customer_challan() already
    enforces, so this page just surfaces batches worth picking."""
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    q = request.args.get("q", "")
    q_search = q.strip()

    conn = get_connection()
    cursor = conn.cursor()

    where_parts = []
    params = []
    if q_search:
        where_parts.append("(mfg_resharpen_batches.batch_no LIKE ? OR mfg_tools.tool_name LIKE ? OR customers.name LIKE ?)")
        params += [f"%{q_search}%", f"%{q_search}%", f"%{q_search}%"]
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    cursor.execute(f"""
        SELECT mfg_resharpen_batches.id, mfg_resharpen_batches.batch_no, mfg_tools.id as tool_id, mfg_tools.tool_name, mfg_tools.tool_type,
               customers.name as customer_name
        FROM mfg_resharpen_batches
        JOIN mfg_tools ON mfg_resharpen_batches.tool_id = mfg_tools.id
        JOIN customers ON mfg_resharpen_batches.customer_id = customers.id
        {where}
        ORDER BY mfg_resharpen_batches.batch_no DESC
    """, params)
    candidates = cursor.fetchall()

    rows_all = []
    stock_cache = {}
    for b in candidates:
        if b["tool_id"] not in stock_cache:
            stock_cache[b["tool_id"]] = compute_resharpen_stock(cursor, b["tool_id"])["completed_in_hand"]
        completed = stock_cache[b["tool_id"]]
        if completed > 0:
            rows_all.append({"id": b["id"], "batch_no": b["batch_no"], "tool_name": b["tool_name"],
                              "tool_type": b["tool_type"], "customer_name": b["customer_name"], "completed_in_hand": completed})

    total = len(rows_all)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    rows = rows_all[offset:offset + per_page]

    conn.close()
    return render_template("manufacturing/resharpen_pick_batch_for_delivery.html", rows=rows, page=page, total_pages=total_pages, total=total, q=q)


@resharpening.route("/manufacturing/resharpen/challans")
def challans_list():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    offset = (page - 1) * per_page
    q = request.args.get("q", "")
    q_search = q.strip()
    challan_type = request.args.get("challan_type", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")

    conn = get_connection()
    cursor = conn.cursor()

    where_parts = []
    params = []
    if q_search:
        where_parts.append("(mfg_resharpen_challans.challan_no LIKE ? OR suppliers.name LIKE ? OR customers.name LIKE ?)")
        params += [f"%{q_search}%", f"%{q_search}%", f"%{q_search}%"]
    if challan_type:
        where_parts.append("mfg_resharpen_challans.challan_type = ?")
        params.append(challan_type)
    if from_date:
        where_parts.append("mfg_resharpen_challans.challan_date >= ?")
        params.append(from_date)
    if to_date:
        where_parts.append("mfg_resharpen_challans.challan_date <= ?")
        params.append(to_date)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    cursor.execute(f"""
        SELECT mfg_resharpen_challans.*,
               CASE WHEN party_type = 'supplier' THEN suppliers.name ELSE customers.name END as party_name
        FROM mfg_resharpen_challans
        LEFT JOIN suppliers ON mfg_resharpen_challans.party_type = 'supplier' AND mfg_resharpen_challans.party_id = suppliers.id
        LEFT JOIN customers ON mfg_resharpen_challans.party_type = 'customer' AND mfg_resharpen_challans.party_id = customers.id
        {where}
        ORDER BY mfg_resharpen_challans.challan_date DESC, mfg_resharpen_challans.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    challans = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_resharpen_challans
        LEFT JOIN suppliers ON mfg_resharpen_challans.party_type = 'supplier' AND mfg_resharpen_challans.party_id = suppliers.id
        LEFT JOIN customers ON mfg_resharpen_challans.party_type = 'customer' AND mfg_resharpen_challans.party_id = customers.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    conn.close()
    return render_template("manufacturing/resharpen_challans.html", challans=challans, page=page, total_pages=total_pages, total=total, q=q, challan_type=challan_type, from_date=from_date, to_date=to_date)


@resharpening.route("/manufacturing/resharpen/challans/<int:challan_id>")
def view_challan(challan_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_resharpen_challans WHERE id = ?", (challan_id,))
    challan = cursor.fetchone()

    if challan["party_type"] == "supplier":
        cursor.execute("SELECT * FROM suppliers WHERE id = ?", (challan["party_id"],))
    else:
        cursor.execute("SELECT * FROM customers WHERE id = ?", (challan["party_id"],))
    party = cursor.fetchone()

    transporter = None
    if challan["transporter_id"]:
        cursor.execute("SELECT * FROM transporters WHERE id = ?", (challan["transporter_id"],))
        transporter = cursor.fetchone()

    cursor.execute("""
        SELECT mfg_resharpen_challan_items.*, mfg_resharpen_batches.batch_no, mfg_tools.tool_name, mfg_tools.tool_type,
               mfg_resharpen_stages.stages as op_stages
        FROM mfg_resharpen_challan_items
        JOIN mfg_resharpen_batches ON mfg_resharpen_challan_items.batch_id = mfg_resharpen_batches.id
        JOIN mfg_tools ON mfg_resharpen_batches.tool_id = mfg_tools.id
        LEFT JOIN mfg_resharpen_stages ON mfg_resharpen_challan_items.stage_id = mfg_resharpen_stages.id
        WHERE challan_id = ?
    """, (challan_id,))
    item_rows = cursor.fetchall()
    conn.close()

    items = []
    for r in item_rows:
        label = stage_label_for(r["op_stages"].split(",")) if r["op_stages"] else None
        items.append({"batch_id": r["batch_id"], "batch_no": r["batch_no"], "tool_name": r["tool_name"],
                       "tool_type": r["tool_type"], "quantity": r["quantity"], "stage_label": label})

    conn2 = get_connection()
    delete_blocked_reason = _resharpen_challan_delete_guard(conn2.cursor(), challan)
    conn2.close()

    return render_template("manufacturing/resharpen_view_challan.html", challan=challan, party=party, items=items,
        transporter=transporter, delete_blocked_reason=delete_blocked_reason)


@resharpening.route("/manufacturing/resharpen/challans/<int:challan_id>/edit", methods=["GET", "POST"])
def edit_challan(challan_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_resharpen_challans WHERE id = ?", (challan_id,))
    challan = cursor.fetchone()
    if not challan:
        conn.close()
        flash("Challan not found.", "danger")
        return redirect(url_for("resharpening.challans_list"))
    if challan["challan_type"] == "customer_inward":
        conn.close()
        flash("This is the batch's inward receipt - edit the batch instead.", "danger")
        return redirect(url_for("resharpening.challans_list"))

    if request.method == "POST":
        challan_date = request.form["challan_date"]
        transporter_id = request.form.get("transporter_id") or None
        remarks = request.form.get("remarks")
        cursor.execute("UPDATE mfg_resharpen_challans SET challan_date=?, transporter_id=?, remarks=? WHERE id=?",
                       (challan_date, transporter_id, remarks, challan_id))
        conn.commit()
        conn.close()
        flash("Challan updated successfully!", "success")
        return redirect(url_for("resharpening.view_challan", challan_id=challan_id))

    cursor.execute("SELECT id, name FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/resharpen_edit_challan.html", challan=challan, transporters=transporters)

def _resharpen_challan_delete_guard(cursor, challan):
    if challan["challan_type"] == "customer_inward":
        return "This is the batch's inward receipt - delete the batch instead."
    cursor.execute("SELECT * FROM mfg_resharpen_challan_items WHERE challan_id = ?", (challan["id"],))
    items = cursor.fetchall()
    if challan["challan_type"] == "customer_delivery":
        for item in items:
            cursor.execute("SELECT COALESCE(SUM(quantity), 0) as total FROM mfg_resharpen_invoice_items WHERE batch_id = ?", (item["batch_id"],))
            if cursor.fetchone()["total"] > 0:
                return "Cannot delete - this batch has already been invoiced, so we can't tell how much of that invoice depended on this delivery."
    elif challan["challan_type"] == "vendor_jobwork":
        for item in items:
            if not item["stage_id"]:
                continue
            cursor.execute("SELECT receive_date, purchase_invoice_id FROM mfg_resharpen_stages WHERE id = ?", (item["stage_id"],))
            stage = cursor.fetchone()
            if stage and stage["receive_date"]:
                return "Cannot delete - this shipment has already been received back."
            if stage and stage["purchase_invoice_id"]:
                return "Cannot delete - a vendor purchase invoice already references this shipment."
    return None

@resharpening.route("/manufacturing/resharpen/challans/delete/<int:challan_id>")
def delete_challan(challan_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_resharpen_challans WHERE id = ?", (challan_id,))
    challan = cursor.fetchone()
    if not challan:
        conn.close()
        flash("Challan not found.", "danger")
        return redirect(url_for("resharpening.challans_list"))

    error = _resharpen_challan_delete_guard(cursor, challan)
    if error:
        conn.close()
        flash(error, "danger")
        return redirect(url_for("resharpening.view_challan", challan_id=challan_id))

    cursor.execute("SELECT * FROM mfg_resharpen_challan_items WHERE challan_id = ?", (challan_id,))
    items = cursor.fetchall()

    cursor.execute("BEGIN IMMEDIATE")
    if challan["challan_type"] == "vendor_jobwork":
        for item in items:
            if item["stage_id"]:
                cursor.execute("DELETE FROM mfg_resharpen_stages WHERE id = ?", (item["stage_id"],))
    cursor.execute("DELETE FROM mfg_resharpen_challan_items WHERE challan_id = ?", (challan_id,))
    cursor.execute("DELETE FROM mfg_resharpen_challans WHERE id = ?", (challan_id,))
    conn.commit()
    conn.close()
    flash("Challan deleted successfully!", "danger")
    return redirect(url_for("resharpening.challans_list"))

@resharpening.route("/manufacturing/resharpen/challans/<int:challan_id>/print")
def print_challan(challan_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_resharpen_challans WHERE id = ?", (challan_id,))
    challan = cursor.fetchone()

    if challan["party_type"] == "supplier":
        cursor.execute("SELECT * FROM suppliers WHERE id = ?", (challan["party_id"],))
    else:
        cursor.execute("SELECT * FROM customers WHERE id = ?", (challan["party_id"],))
    party = cursor.fetchone()

    transporter = None
    if challan["transporter_id"]:
        cursor.execute("SELECT * FROM transporters WHERE id = ?", (challan["transporter_id"],))
        transporter = cursor.fetchone()

    cursor.execute("""
        SELECT mfg_resharpen_challan_items.*, mfg_resharpen_batches.batch_no, mfg_tools.tool_name, mfg_tools.tool_type,
               mfg_resharpen_stages.stages as op_stages
        FROM mfg_resharpen_challan_items
        JOIN mfg_resharpen_batches ON mfg_resharpen_challan_items.batch_id = mfg_resharpen_batches.id
        JOIN mfg_tools ON mfg_resharpen_batches.tool_id = mfg_tools.id
        LEFT JOIN mfg_resharpen_stages ON mfg_resharpen_challan_items.stage_id = mfg_resharpen_stages.id
        WHERE challan_id = ?
    """, (challan_id,))
    item_rows = cursor.fetchall()
    conn.close()

    items = []
    for r in item_rows:
        description = f"{r['tool_name']} ({r['tool_type']})"
        if r["op_stages"]:
            description += " - " + stage_label_for(r["op_stages"].split(","))
        items.append({"description": description, "quantity": r["quantity"], "batch_no": r["batch_no"]})

    doc_titles = {"customer_inward": "Resharpening Inward Receipt", "vendor_jobwork": "Job Work Challan", "customer_delivery": "Delivery Challan"}
    doc_title = doc_titles.get(challan["challan_type"], "Challan")
    batch_id = item_rows[0]["batch_id"] if item_rows else None

    return render_template("manufacturing/print_document.html",
        doc_title=doc_title, doc_no=challan["challan_no"], doc_date=challan["challan_date"],
        party=party, items=items, show_pricing=False, transporter=transporter,
        subtotal=None, tax_amount=None, total=None, remarks=challan["remarks"],
        back_url=(f"/manufacturing/resharpen/batches/{batch_id}" if batch_id else None))


# ============================================
# INVOICES
# ============================================

@resharpening.route("/manufacturing/resharpen/invoices/pick-batch")
def pick_batch_for_invoice():
    """Entry point for '+ New Invoice' from the Resharpen Invoices list - lets you find the
    batch to bill by tool/customer/batch no instead of drilling into it first. Just navigates
    to the exact same add_invoice(batch_id) route/form used from the batch page, so the
    delivered-vs-already-invoiced cap (add_invoice's own check) is the only place that
    quantity is ever validated - this page can't bypass or duplicate it."""
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    offset = (page - 1) * per_page
    q = request.args.get("q", "")
    q_search = q.strip()

    conn = get_connection()
    cursor = conn.cursor()

    where = ""
    params = []
    if q_search:
        where = "WHERE (batch_no LIKE ? OR tool_name LIKE ? OR customer_name LIKE ?)"
        params = [f"%{q_search}%", f"%{q_search}%", f"%{q_search}%"]

    cursor.execute(f"""
        SELECT * FROM (
            SELECT mfg_resharpen_batches.id, mfg_resharpen_batches.batch_no, mfg_tools.tool_name, mfg_tools.tool_type,
                   customers.name as customer_name,
                   COALESCE((SELECT SUM(mfg_resharpen_challan_items.quantity) FROM mfg_resharpen_challan_items
                             JOIN mfg_resharpen_challans ON mfg_resharpen_challan_items.challan_id = mfg_resharpen_challans.id
                             WHERE mfg_resharpen_challan_items.batch_id = mfg_resharpen_batches.id AND mfg_resharpen_challans.challan_type = 'customer_delivery'), 0) as delivered_qty,
                   COALESCE((SELECT SUM(quantity) FROM mfg_resharpen_invoice_items WHERE batch_id = mfg_resharpen_batches.id), 0) as invoiced_qty
            FROM mfg_resharpen_batches
            JOIN mfg_tools ON mfg_resharpen_batches.tool_id = mfg_tools.id
            JOIN customers ON mfg_resharpen_batches.customer_id = customers.id
        )
        WHERE (delivered_qty - invoiced_qty) > 0
        {where}
        ORDER BY batch_no DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    rows = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT mfg_resharpen_batches.id, mfg_resharpen_batches.batch_no, mfg_tools.tool_name, customers.name as customer_name,
                   COALESCE((SELECT SUM(mfg_resharpen_challan_items.quantity) FROM mfg_resharpen_challan_items
                             JOIN mfg_resharpen_challans ON mfg_resharpen_challan_items.challan_id = mfg_resharpen_challans.id
                             WHERE mfg_resharpen_challan_items.batch_id = mfg_resharpen_batches.id AND mfg_resharpen_challans.challan_type = 'customer_delivery'), 0) as delivered_qty,
                   COALESCE((SELECT SUM(quantity) FROM mfg_resharpen_invoice_items WHERE batch_id = mfg_resharpen_batches.id), 0) as invoiced_qty
            FROM mfg_resharpen_batches
            JOIN mfg_tools ON mfg_resharpen_batches.tool_id = mfg_tools.id
            JOIN customers ON mfg_resharpen_batches.customer_id = customers.id
        )
        WHERE (delivered_qty - invoiced_qty) > 0
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    conn.close()
    return render_template("manufacturing/resharpen_pick_batch_for_invoice.html", rows=rows, page=page, total_pages=total_pages, total=total, q=q)


@resharpening.route("/manufacturing/resharpen/invoices")
def invoices_list():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    offset = (page - 1) * per_page
    q = request.args.get("q", "")
    q_search = q.strip()
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")

    conn = get_connection()
    cursor = conn.cursor()

    where_parts = []
    params = []
    if q_search:
        where_parts.append("(mfg_resharpen_invoices.invoice_no LIKE ? OR customers.name LIKE ?)")
        params += [f"%{q_search}%", f"%{q_search}%"]
    if from_date:
        where_parts.append("mfg_resharpen_invoices.invoice_date >= ?")
        params.append(from_date)
    if to_date:
        where_parts.append("mfg_resharpen_invoices.invoice_date <= ?")
        params.append(to_date)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    cursor.execute(f"""
        SELECT mfg_resharpen_invoices.*, customers.name as customer_name
        FROM mfg_resharpen_invoices
        LEFT JOIN customers ON mfg_resharpen_invoices.customer_id = customers.id
        {where}
        ORDER BY mfg_resharpen_invoices.invoice_date DESC, mfg_resharpen_invoices.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    invoices = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_resharpen_invoices
        LEFT JOIN customers ON mfg_resharpen_invoices.customer_id = customers.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    conn.close()
    return render_template("manufacturing/resharpen_invoices.html", invoices=invoices, page=page, total_pages=total_pages, total=total, q=q, from_date=from_date, to_date=to_date)


@resharpening.route("/manufacturing/resharpen/invoices/<int:invoice_id>")
def view_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_resharpen_invoices WHERE id = ?", (invoice_id,))
    invoice = cursor.fetchone()
    cursor.execute("SELECT * FROM customers WHERE id = ?", (invoice["customer_id"],))
    customer = cursor.fetchone()
    cursor.execute("""
        SELECT mfg_resharpen_invoice_items.*, mfg_resharpen_batches.batch_no, mfg_tools.tool_name, mfg_tools.tool_type
        FROM mfg_resharpen_invoice_items
        JOIN mfg_resharpen_batches ON mfg_resharpen_invoice_items.batch_id = mfg_resharpen_batches.id
        JOIN mfg_tools ON mfg_resharpen_batches.tool_id = mfg_tools.id
        WHERE invoice_id = ?
    """, (invoice_id,))
    items = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) as c FROM mfg_resharpen_sales_payments WHERE invoice_id = ?", (invoice_id,))
    delete_blocked = cursor.fetchone()["c"] > 0
    conn.close()
    return render_template("manufacturing/resharpen_view_invoice.html", invoice=invoice, customer=customer, items=items, delete_blocked=delete_blocked)


@resharpening.route("/manufacturing/resharpen/invoices/<int:invoice_id>/edit", methods=["GET", "POST"])
def edit_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_resharpen_invoices WHERE id = ?", (invoice_id,))
    invoice = cursor.fetchone()
    if not invoice:
        conn.close()
        flash("Invoice not found.", "danger")
        return redirect(url_for("resharpening.invoices_list"))
    cursor.execute("SELECT * FROM mfg_resharpen_invoice_items WHERE invoice_id = ?", (invoice_id,))
    item = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) as c FROM mfg_resharpen_sales_payments WHERE invoice_id = ?", (invoice_id,))
    untouched = cursor.fetchone()["c"] == 0

    if request.method == "POST":
        remarks = request.form.get("remarks")
        cr_days = int(request.form.get("cr_days") or 0)
        due_date = None
        if cr_days:
            due_date = (date.fromisoformat(invoice["invoice_date"]) + timedelta(days=cr_days)).isoformat()

        if untouched and request.form.get("quantity"):
            quantity = float(request.form["quantity"])
            rate = float(request.form["rate"])
            tax_rate = float(request.form.get("tax_rate") or 18)

            cursor.execute("""
                SELECT COALESCE(SUM(mfg_resharpen_challan_items.quantity), 0) as total
                FROM mfg_resharpen_challan_items
                JOIN mfg_resharpen_challans ON mfg_resharpen_challan_items.challan_id = mfg_resharpen_challans.id
                WHERE mfg_resharpen_challan_items.batch_id = ? AND mfg_resharpen_challans.challan_type = 'customer_delivery'
            """, (item["batch_id"],))
            delivered_qty = cursor.fetchone()["total"]
            cursor.execute("SELECT COALESCE(SUM(quantity), 0) as total FROM mfg_resharpen_invoice_items WHERE batch_id = ? AND invoice_id != ?", (item["batch_id"], invoice_id))
            already_invoiced_qty = cursor.fetchone()["total"]
            available_to_invoice = delivered_qty - already_invoiced_qty
            if quantity > available_to_invoice:
                flash(f"Cannot invoice {quantity} - only {available_to_invoice} delivered and not yet invoiced for this batch.", "danger")
                conn.close()
                return redirect(url_for("resharpening.edit_invoice", invoice_id=invoice_id))

            bef_tax_amt = round(quantity * rate, 2)
            tax_amount = round(bef_tax_amt * tax_rate / 100, 2)
            amount = round(bef_tax_amt + tax_amount, 2)

            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("""
                UPDATE mfg_resharpen_invoice_items SET quantity=?, rate=?, tax_rate=?, bef_tax_amt=?, tax_amount=?, amount=?
                WHERE id=?
            """, (quantity, rate, tax_rate, bef_tax_amt, tax_amount, amount, item["id"]))
            cursor.execute("UPDATE mfg_resharpen_invoices SET cr_days=?, due_date=?, remarks=?, total_amount=? WHERE id=?",
                           (cr_days, due_date, remarks, amount, invoice_id))
            conn.commit()
            conn.close()
            flash("Invoice updated successfully!", "success")
            return redirect(url_for("resharpening.view_invoice", invoice_id=invoice_id))

        cursor.execute("UPDATE mfg_resharpen_invoices SET cr_days=?, due_date=?, remarks=? WHERE id=?", (cr_days, due_date, remarks, invoice_id))
        conn.commit()
        conn.close()
        flash("Invoice updated successfully!", "success")
        return redirect(url_for("resharpening.view_invoice", invoice_id=invoice_id))

    conn.close()
    return render_template("manufacturing/resharpen_edit_invoice.html", invoice=invoice, item=item, untouched=untouched)

@resharpening.route("/manufacturing/resharpen/invoices/delete/<int:invoice_id>")
def delete_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as c FROM mfg_resharpen_sales_payments WHERE invoice_id = ?", (invoice_id,))
    if cursor.fetchone()["c"] > 0:
        conn.close()
        flash("Cannot delete this invoice - a payment has already been recorded against it.", "danger")
        return redirect(url_for("resharpening.view_invoice", invoice_id=invoice_id))

    cursor.execute("DELETE FROM mfg_resharpen_invoice_items WHERE invoice_id = ?", (invoice_id,))
    cursor.execute("DELETE FROM mfg_resharpen_invoices WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()
    flash("Invoice deleted successfully!", "danger")
    return redirect(url_for("resharpening.invoices_list"))

@resharpening.route("/manufacturing/resharpen/invoices/<int:invoice_id>/print")
def print_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_resharpen_invoices WHERE id = ?", (invoice_id,))
    invoice = cursor.fetchone()
    cursor.execute("SELECT * FROM customers WHERE id = ?", (invoice["customer_id"],))
    party = cursor.fetchone()
    cursor.execute("""
        SELECT mfg_resharpen_invoice_items.*, mfg_resharpen_batches.batch_no, mfg_tools.tool_name, mfg_tools.tool_type
        FROM mfg_resharpen_invoice_items
        JOIN mfg_resharpen_batches ON mfg_resharpen_invoice_items.batch_id = mfg_resharpen_batches.id
        JOIN mfg_tools ON mfg_resharpen_batches.tool_id = mfg_tools.id
        WHERE invoice_id = ?
    """, (invoice_id,))
    item_rows = cursor.fetchall()
    conn.close()

    items = []
    for r in item_rows:
        items.append({
            "description": f"{r['tool_name']} ({r['tool_type']}) - Resharpening",
            "quantity": r["quantity"], "rate": r["rate"], "amount": r["bef_tax_amt"],
            "batch_no": r["batch_no"]
        })

    batch_id = item_rows[0]["batch_id"] if item_rows else None
    return render_template("manufacturing/print_document.html",
        doc_title="Tax Invoice", doc_no=invoice["invoice_no"], doc_date=invoice["invoice_date"],
        party=party, items=items, show_pricing=True,
        subtotal=round(invoice["total_amount"] - sum(i["tax_amount"] for i in item_rows), 2),
        tax_amount=round(sum(i["tax_amount"] for i in item_rows), 2),
        total=invoice["total_amount"], remarks=invoice["remarks"],
        back_url=(f"/manufacturing/resharpen/batches/{batch_id}" if batch_id else None))


@resharpening.route("/manufacturing/resharpen/batches/<int:batch_id>/invoice/new", methods=["GET", "POST"])
def add_invoice(batch_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_resharpen_batches.*, mfg_tools.tool_name, customers.name as customer_name
        FROM mfg_resharpen_batches
        JOIN mfg_tools ON mfg_resharpen_batches.tool_id = mfg_tools.id
        JOIN customers ON mfg_resharpen_batches.customer_id = customers.id
        WHERE mfg_resharpen_batches.id = ?
    """, (batch_id,))
    batch = cursor.fetchone()

    if request.method == "POST":
        invoice_date = request.form["invoice_date"]
        quantity = float(request.form["quantity"])
        rate = float(request.form["rate"])
        tax_rate = float(request.form.get("tax_rate") or 18)
        remarks = request.form.get("remarks")
        cr_days = int(request.form.get("cr_days") or 0)
        due_date = None
        if cr_days:
            due_date = (date.fromisoformat(invoice_date) + timedelta(days=cr_days)).isoformat()

        cursor.execute("""
            SELECT COALESCE(SUM(mfg_resharpen_challan_items.quantity), 0) as total
            FROM mfg_resharpen_challan_items
            JOIN mfg_resharpen_challans ON mfg_resharpen_challan_items.challan_id = mfg_resharpen_challans.id
            WHERE mfg_resharpen_challan_items.batch_id = ? AND mfg_resharpen_challans.challan_type = 'customer_delivery'
        """, (batch_id,))
        delivered_qty = cursor.fetchone()["total"]
        cursor.execute("SELECT COALESCE(SUM(quantity), 0) as total FROM mfg_resharpen_invoice_items WHERE batch_id = ?", (batch_id,))
        already_invoiced_qty = cursor.fetchone()["total"]
        available_to_invoice = delivered_qty - already_invoiced_qty
        if quantity > available_to_invoice:
            flash(f"Cannot invoice {quantity} - only {available_to_invoice} delivered and not yet invoiced for this batch. Create a delivery challan first.", "danger")
            conn.close()
            return redirect(url_for("resharpening.add_invoice", batch_id=batch_id))

        bef_tax_amt = round(quantity * rate, 2)
        tax_amount = round(bef_tax_amt * tax_rate / 100, 2)
        amount = round(bef_tax_amt + tax_amount, 2)

        cursor.execute("BEGIN IMMEDIATE")
        invoice_no = generate_invoice_no(invoice_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_resharpen_invoices (invoice_no, invoice_date, customer_id, cr_days, due_date, remarks, total_amount)
            VALUES (?,?,?,?,?,?,?)
        """, (invoice_no, invoice_date, batch["customer_id"], cr_days, due_date, remarks, amount))
        invoice_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO mfg_resharpen_invoice_items (invoice_id, batch_id, quantity, rate, tax_rate, bef_tax_amt, tax_amount, amount)
            VALUES (?,?,?,?,?,?,?,?)
        """, (invoice_id, batch_id, quantity, rate, tax_rate, bef_tax_amt, tax_amount, amount))

        conn.commit()
        conn.close()
        flash(f"Invoice {invoice_no} created!", "success")
        return redirect(url_for("resharpening.view_invoice", invoice_id=invoice_id))

    cursor.execute("""
        SELECT mfg_resharpen_invoice_items.rate
        FROM mfg_resharpen_invoice_items
        JOIN mfg_resharpen_invoices ON mfg_resharpen_invoice_items.invoice_id = mfg_resharpen_invoices.id
        JOIN mfg_resharpen_batches ON mfg_resharpen_invoice_items.batch_id = mfg_resharpen_batches.id
        WHERE mfg_resharpen_invoices.customer_id = ? AND mfg_resharpen_batches.tool_id = ?
        ORDER BY mfg_resharpen_invoice_items.id DESC LIMIT 1
    """, (batch["customer_id"], batch["tool_id"]))
    last_price_row = cursor.fetchone()
    last_price = last_price_row["rate"] if last_price_row else None
    if last_price is None:
        cursor.execute("SELECT sales_price, sales_discount_percent FROM mfg_tools WHERE id = ?", (batch["tool_id"],))
        tool_price_row = cursor.fetchone()
        if tool_price_row and tool_price_row["sales_price"] is not None:
            last_price = _final_price(tool_price_row["sales_price"], tool_price_row["sales_discount_percent"])

    conn.close()
    return render_template("manufacturing/resharpen_add_invoice.html", batch=batch, last_price=last_price)


# ============================================
# STOCK
# ============================================

@resharpening.route("/manufacturing/resharpen/stock")
def stock_view():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    offset = (page - 1) * per_page
    q = request.args.get("q", "")
    q_search = q.strip()

    conn = get_connection()
    cursor = conn.cursor()

    where = ""
    params = []
    if q_search:
        where = "WHERE (tool_name LIKE ? OR tool_type LIKE ?)"
        params = [f"%{q_search}%", f"%{q_search}%"]

    cursor.execute(f"""
        SELECT DISTINCT mfg_tools.id, mfg_tools.tool_name, mfg_tools.tool_type
        FROM mfg_tools
        JOIN mfg_resharpen_batches ON mfg_resharpen_batches.tool_id = mfg_tools.id
        {where}
        ORDER BY mfg_tools.tool_name LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    tools = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(DISTINCT mfg_tools.id) FROM mfg_tools
        JOIN mfg_resharpen_batches ON mfg_resharpen_batches.tool_id = mfg_tools.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    tool_stock = []
    for tool in tools:
        s = compute_resharpen_stock(cursor, tool["id"])
        tool_stock.append({"tool": tool, **s})

    conn.close()
    return render_template("manufacturing/resharpen_stock.html", tool_stock=tool_stock, page=page, total_pages=total_pages, total=total, q=q)
