"""
SUMAK ERP - Manufacturing: Payments Received, Payments Made, Outstanding Report,
Party Statement, Day Book. Mirrors modules/accounting.py (Distribution's version)
structurally - same shape, same "compute balance-due on the fly, never store a
status field" philosophy - against Manufacturing's own tables
(mfg_invoices / mfg_purchase_invoices / mfg_sales_payments / mfg_purchase_payments).
Routes are prefixed /manufacturing/... to avoid colliding with Distribution's
identically-named routes in the same Flask app.

Receivable-side views (Payments Received, Outstanding, Party Statement, Day Book) are
the ONE unified accounting surface across both job-work manufacturing (mfg_invoices /
mfg_sales_payments) and Resharpening (mfg_resharpen_invoices / mfg_resharpen_sales_payments)
by request - the two processes stay in separate tables (their ids aren't unique across
both), so every merged row carries a "source" ("new" vs "resharpen") that says which table
it actually lives in; sales-payment add/edit/delete routes use that tag to write to the
right one. Payable side (Purchase Payments) is untouched - resharpening has no purchase
invoices of its own.
"""
from flask import Blueprint
from flask import render_template, request, redirect, url_for, flash
from database import get_connection as _get_connection
from modules.auth import require_system
from modules.permissions import enforce_permissions
from modules.document_database import (
    install_document_connection_cleanup,
    managed_document_connection,
)
from datetime import date

get_connection = managed_document_connection(_get_connection)
manufacturing_accounting = Blueprint("manufacturing_accounting", __name__)
manufacturing_accounting.before_request(require_system("manufacturing"))
manufacturing_accounting.before_request(enforce_permissions)
install_document_connection_cleanup(manufacturing_accounting)

def invoice_status(total_amount, paid):
    total = total_amount or 0
    paid = paid or 0
    due = round(total - paid, 2)
    if due <= 0.01:
        status = "paid"
    elif paid > 0:
        status = "partial"
    else:
        status = "unpaid"
    return due, status

# ============================================
# SALES PAYMENTS (money received from customers)
# ============================================

def _sales_payments_where(prefix, customer_id, from_date, to_date):
    parts = []
    params = []
    if customer_id:
        parts.append(f"{prefix}.customer_id = ?")
        params.append(customer_id)
    if from_date:
        parts.append(f"{prefix}.payment_date >= ?")
        params.append(from_date)
    if to_date:
        parts.append(f"{prefix}.payment_date <= ?")
        params.append(to_date)
    return ("WHERE " + " AND ".join(parts)) if parts else "", params


@manufacturing_accounting.route("/manufacturing/sales-payments")
def sales_payments_list():
    page = request.args.get("page", 1, type=int)
    customer_id = request.args.get("customer_id", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")
    per_page = 20

    conn = get_connection()
    cursor = conn.cursor()

    where_sql, where_params = _sales_payments_where("mfg_sales_payments", customer_id, from_date, to_date)
    cursor.execute(f"""
        SELECT mfg_sales_payments.*, mfg_invoices.invoice_no, customers.name as customer_name
        FROM mfg_sales_payments
        LEFT JOIN mfg_invoices ON mfg_sales_payments.invoice_id = mfg_invoices.id
        JOIN customers ON mfg_sales_payments.customer_id = customers.id
        {where_sql}
    """, where_params)
    new_rows = cursor.fetchall()

    where_sql2, where_params2 = _sales_payments_where("mfg_resharpen_sales_payments", customer_id, from_date, to_date)
    cursor.execute(f"""
        SELECT mfg_resharpen_sales_payments.*, mfg_resharpen_invoices.invoice_no, customers.name as customer_name
        FROM mfg_resharpen_sales_payments
        LEFT JOIN mfg_resharpen_invoices ON mfg_resharpen_sales_payments.invoice_id = mfg_resharpen_invoices.id
        JOIN customers ON mfg_resharpen_sales_payments.customer_id = customers.id
        {where_sql2}
    """, where_params2)
    resharpen_rows = cursor.fetchall()

    cursor.execute("SELECT id, name FROM customers ORDER BY name")
    customers = cursor.fetchall()
    conn.close()

    # merged in Python (like Outstanding/Day Book already do) since the two payment
    # tables have independent, overlapping id sequences and can't be UNIONed by id
    combined = [dict(r, source="new") for r in new_rows] + [dict(r, source="resharpen") for r in resharpen_rows]
    combined.sort(key=lambda p: (p["payment_date"] or "", p["id"]), reverse=True)

    total = len(combined)
    total_amount = round(sum(p["amount"] or 0 for p in combined), 2)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    payments = combined[(page - 1) * per_page: (page - 1) * per_page + per_page]

    return render_template("manufacturing/sales_payments.html",
        payments=payments, page=page, total_pages=total_pages,
        customers=customers, customer_id=customer_id, from_date=from_date, to_date=to_date,
        total_amount=total_amount)

@manufacturing_accounting.route("/manufacturing/sales-payments/add", methods=["GET", "POST"])
def add_sales_payment():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        invoice_id = request.form.get("invoice_id") or None
        customer_id = request.form["customer_id"]
        payment_date = request.form["payment_date"]
        amount = request.form["amount"]
        payment_mode = request.form.get("payment_mode") or "cash"
        reference_no = request.form.get("reference_no")
        remarks = request.form.get("remarks")
        source = request.form.get("source") or "new"
        table = "mfg_resharpen_sales_payments" if source == "resharpen" else "mfg_sales_payments"
        cursor.execute(f"""
            INSERT INTO {table} (invoice_id, customer_id, payment_date, amount, payment_mode, reference_no, remarks)
            VALUES (?,?,?,?,?,?,?)
        """, (invoice_id, customer_id, payment_date, amount, payment_mode, reference_no, remarks))
        conn.commit()
        conn.close()
        flash("Payment recorded successfully!", "success")
        return redirect(url_for("manufacturing_accounting.sales_payments_list"))

    cursor.execute("SELECT id, name FROM customers ORDER BY name")
    customers = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/add_sales_payment.html", customers=customers)

@manufacturing_accounting.route("/manufacturing/sales-payments/edit/<source>/<int:payment_id>", methods=["GET", "POST"])
def edit_sales_payment(source, payment_id):
    table = "mfg_resharpen_sales_payments" if source == "resharpen" else "mfg_sales_payments"
    invoice_table = "mfg_resharpen_invoices" if source == "resharpen" else "mfg_invoices"
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        payment_date = request.form["payment_date"]
        amount = request.form["amount"]
        payment_mode = request.form.get("payment_mode") or "cash"
        reference_no = request.form.get("reference_no")
        remarks = request.form.get("remarks")
        cursor.execute(f"""
            UPDATE {table} SET payment_date=?, amount=?, payment_mode=?, reference_no=?, remarks=?
            WHERE id=?
        """, (payment_date, amount, payment_mode, reference_no, remarks, payment_id))
        conn.commit()
        conn.close()
        flash("Payment updated successfully!", "success")
        return redirect(url_for("manufacturing_accounting.sales_payments_list"))

    cursor.execute(f"""
        SELECT {table}.*, {invoice_table}.invoice_no, customers.name as customer_name
        FROM {table}
        LEFT JOIN {invoice_table} ON {table}.invoice_id = {invoice_table}.id
        JOIN customers ON {table}.customer_id = customers.id
        WHERE {table}.id = ?
    """, (payment_id,))
    payment = cursor.fetchone()
    conn.close()
    return render_template("manufacturing/edit_sales_payment.html", payment=payment, source=source)

@manufacturing_accounting.route("/manufacturing/sales-payments/delete/<source>/<int:payment_id>")
def delete_sales_payment(source, payment_id):
    table = "mfg_resharpen_sales_payments" if source == "resharpen" else "mfg_sales_payments"
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM {table} WHERE id=?", (payment_id,))
    conn.commit()
    conn.close()
    flash("Payment deleted successfully!", "danger")
    return redirect(url_for("manufacturing_accounting.sales_payments_list"))

@manufacturing_accounting.route("/manufacturing/api/customer-invoices/<int:customer_id>")
def api_customer_invoices(customer_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_invoices.id, mfg_invoices.invoice_no, mfg_invoices.invoice_date, mfg_invoices.due_date,
               mfg_invoices.total_amount,
               COALESCE((SELECT SUM(amount) FROM mfg_sales_payments WHERE mfg_sales_payments.invoice_id = mfg_invoices.id), 0) as paid
        FROM mfg_invoices
        WHERE mfg_invoices.customer_id = ?
        ORDER BY mfg_invoices.invoice_date DESC
    """, (customer_id,))
    new_rows = cursor.fetchall()
    cursor.execute("""
        SELECT mfg_resharpen_invoices.id, mfg_resharpen_invoices.invoice_no, mfg_resharpen_invoices.invoice_date, mfg_resharpen_invoices.due_date,
               mfg_resharpen_invoices.total_amount,
               COALESCE((SELECT SUM(amount) FROM mfg_resharpen_sales_payments WHERE mfg_resharpen_sales_payments.invoice_id = mfg_resharpen_invoices.id), 0) as paid
        FROM mfg_resharpen_invoices
        WHERE mfg_resharpen_invoices.customer_id = ?
        ORDER BY mfg_resharpen_invoices.invoice_date DESC
    """, (customer_id,))
    resharpen_rows = cursor.fetchall()
    conn.close()
    result = []
    for r in new_rows:
        due, status = invoice_status(r["total_amount"], r["paid"])
        result.append({
            "id": r["id"], "source": "new", "invoice_no": r["invoice_no"], "invoice_date": r["invoice_date"],
            "due_date": r["due_date"], "total_amount": r["total_amount"] or 0,
            "paid": r["paid"] or 0, "due": due, "status": status
        })
    for r in resharpen_rows:
        due, status = invoice_status(r["total_amount"], r["paid"])
        result.append({
            "id": r["id"], "source": "resharpen", "invoice_no": r["invoice_no"], "invoice_date": r["invoice_date"],
            "due_date": r["due_date"], "total_amount": r["total_amount"] or 0,
            "paid": r["paid"] or 0, "due": due, "status": status
        })
    result.sort(key=lambda x: x["invoice_date"] or "", reverse=True)
    return {"invoices": result}

# ============================================
# PURCHASE PAYMENTS (money paid to suppliers)
# ============================================

@manufacturing_accounting.route("/manufacturing/purchase-payments")
def purchase_payments_list():
    page = request.args.get("page", 1, type=int)
    supplier_id = request.args.get("supplier_id", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")
    per_page = 20
    offset = (page - 1) * per_page

    conn = get_connection()
    cursor = conn.cursor()

    where_clauses = []
    params = []
    if supplier_id:
        where_clauses.append("mfg_purchase_payments.supplier_id = ?")
        params.append(supplier_id)
    if from_date:
        where_clauses.append("mfg_purchase_payments.payment_date >= ?")
        params.append(from_date)
    if to_date:
        where_clauses.append("mfg_purchase_payments.payment_date <= ?")
        params.append(to_date)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    cursor.execute("""
        SELECT mfg_purchase_payments.*, mfg_purchase_invoices.invoice_no, suppliers.name as supplier_name
        FROM mfg_purchase_payments
        LEFT JOIN mfg_purchase_invoices ON mfg_purchase_payments.invoice_id = mfg_purchase_invoices.id
        JOIN suppliers ON mfg_purchase_payments.supplier_id = suppliers.id
        """ + where_sql + """
        ORDER BY mfg_purchase_payments.payment_date DESC, mfg_purchase_payments.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    payments = cursor.fetchall()

    cursor.execute("""
        SELECT COUNT(*) as cnt, COALESCE(SUM(mfg_purchase_payments.amount), 0) as total_amt
        FROM mfg_purchase_payments
        """ + where_sql, params)
    totals_row = cursor.fetchone()
    total = totals_row["cnt"]
    total_amount = totals_row["total_amt"]
    total_pages = max(1, (total + per_page - 1) // per_page)

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()

    conn.close()
    return render_template("manufacturing/purchase_payments.html",
        payments=payments, page=page, total_pages=total_pages,
        suppliers=suppliers, supplier_id=supplier_id, from_date=from_date, to_date=to_date,
        total_amount=total_amount)

@manufacturing_accounting.route("/manufacturing/purchase-payments/add", methods=["GET", "POST"])
def add_purchase_payment():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        cursor.execute("BEGIN IMMEDIATE")
        invoice_id = request.form.get("invoice_id") or None
        supplier_id = request.form["supplier_id"]
        payment_date = request.form["payment_date"]
        amount = request.form["amount"]
        payment_mode = request.form.get("payment_mode") or "cash"
        reference_no = request.form.get("reference_no")
        remarks = request.form.get("remarks")
        cursor.execute("""
            INSERT INTO mfg_purchase_payments (invoice_id, supplier_id, payment_date, amount, payment_mode, reference_no, remarks)
            VALUES (?,?,?,?,?,?,?)
        """, (invoice_id, supplier_id, payment_date, amount, payment_mode, reference_no, remarks))

        # keep mfg_purchase_invoices.status roughly in sync so the existing Purchase
        # Invoices/Purchase Report list pages (which display that column) stay accurate
        # without needing to be rewritten to compute paid/due live
        if invoice_id:
            cursor.execute("SELECT total_amount FROM mfg_purchase_invoices WHERE id = ?", (invoice_id,))
            inv = cursor.fetchone()
            cursor.execute("SELECT COALESCE(SUM(amount), 0) as paid FROM mfg_purchase_payments WHERE invoice_id = ?", (invoice_id,))
            paid = cursor.fetchone()["paid"]
            _, new_status = invoice_status(inv["total_amount"] if inv else 0, paid)
            cursor.execute("UPDATE mfg_purchase_invoices SET status = ? WHERE id = ?", (new_status, invoice_id))

        conn.commit()
        conn.close()
        flash("Payment recorded successfully!", "success")
        return redirect(url_for("manufacturing_accounting.purchase_payments_list"))

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/add_purchase_payment.html", suppliers=suppliers)

@manufacturing_accounting.route("/manufacturing/purchase-payments/edit/<int:payment_id>", methods=["GET", "POST"])
def edit_purchase_payment(payment_id):
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        cursor.execute("BEGIN IMMEDIATE")
        payment_date = request.form["payment_date"]
        amount = request.form["amount"]
        payment_mode = request.form.get("payment_mode") or "cash"
        reference_no = request.form.get("reference_no")
        remarks = request.form.get("remarks")
        cursor.execute("SELECT invoice_id FROM mfg_purchase_payments WHERE id = ?", (payment_id,))
        row = cursor.fetchone()
        invoice_id = row["invoice_id"] if row else None
        cursor.execute("""
            UPDATE mfg_purchase_payments SET payment_date=?, amount=?, payment_mode=?, reference_no=?, remarks=?
            WHERE id=?
        """, (payment_date, amount, payment_mode, reference_no, remarks, payment_id))
        if invoice_id:
            cursor.execute("SELECT total_amount FROM mfg_purchase_invoices WHERE id = ?", (invoice_id,))
            inv = cursor.fetchone()
            cursor.execute("SELECT COALESCE(SUM(amount), 0) as paid FROM mfg_purchase_payments WHERE invoice_id = ?", (invoice_id,))
            paid = cursor.fetchone()["paid"]
            _, new_status = invoice_status(inv["total_amount"] if inv else 0, paid)
            cursor.execute("UPDATE mfg_purchase_invoices SET status = ? WHERE id = ?", (new_status, invoice_id))
        conn.commit()
        conn.close()
        flash("Payment updated successfully!", "success")
        return redirect(url_for("manufacturing_accounting.purchase_payments_list"))

    cursor.execute("""
        SELECT mfg_purchase_payments.*, mfg_purchase_invoices.invoice_no, suppliers.name as supplier_name
        FROM mfg_purchase_payments
        LEFT JOIN mfg_purchase_invoices ON mfg_purchase_payments.invoice_id = mfg_purchase_invoices.id
        JOIN suppliers ON mfg_purchase_payments.supplier_id = suppliers.id
        WHERE mfg_purchase_payments.id = ?
    """, (payment_id,))
    payment = cursor.fetchone()
    conn.close()
    return render_template("manufacturing/edit_purchase_payment.html", payment=payment)

@manufacturing_accounting.route("/manufacturing/purchase-payments/delete/<int:payment_id>")
def delete_purchase_payment(payment_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute("SELECT invoice_id FROM mfg_purchase_payments WHERE id = ?", (payment_id,))
    row = cursor.fetchone()
    invoice_id = row["invoice_id"] if row else None
    cursor.execute("DELETE FROM mfg_purchase_payments WHERE id=?", (payment_id,))
    if invoice_id:
        cursor.execute("SELECT total_amount FROM mfg_purchase_invoices WHERE id = ?", (invoice_id,))
        inv = cursor.fetchone()
        cursor.execute("SELECT COALESCE(SUM(amount), 0) as paid FROM mfg_purchase_payments WHERE invoice_id = ?", (invoice_id,))
        paid = cursor.fetchone()["paid"]
        _, new_status = invoice_status(inv["total_amount"] if inv else 0, paid)
        cursor.execute("UPDATE mfg_purchase_invoices SET status = ? WHERE id = ?", (new_status, invoice_id))
    conn.commit()
    conn.close()
    flash("Payment deleted successfully!", "danger")
    return redirect(url_for("manufacturing_accounting.purchase_payments_list"))

@manufacturing_accounting.route("/manufacturing/api/supplier-invoices/<int:supplier_id>")
def api_supplier_invoices(supplier_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_purchase_invoices.id, mfg_purchase_invoices.invoice_no, mfg_purchase_invoices.invoice_date, mfg_purchase_invoices.due_date,
               mfg_purchase_invoices.total_amount,
               COALESCE((SELECT SUM(amount) FROM mfg_purchase_payments WHERE mfg_purchase_payments.invoice_id = mfg_purchase_invoices.id), 0) as paid
        FROM mfg_purchase_invoices
        WHERE mfg_purchase_invoices.supplier_id = ?
        ORDER BY mfg_purchase_invoices.invoice_date DESC
    """, (supplier_id,))
    rows = cursor.fetchall()
    conn.close()
    result = []
    for r in rows:
        due, status = invoice_status(r["total_amount"], r["paid"])
        result.append({
            "id": r["id"], "invoice_no": r["invoice_no"], "invoice_date": r["invoice_date"],
            "due_date": r["due_date"], "total_amount": r["total_amount"] or 0,
            "paid": r["paid"] or 0, "due": due, "status": status
        })
    return {"invoices": result}

# ============================================
# OUTSTANDING REPORT (everything still owed, both directions)
# ============================================

BUCKET_ORDER = ["Today", "0-30", "30-60", "60-90", "90-120", "120+"]

def ageing_bucket(due_date_str, today_str):
    if not due_date_str or due_date_str >= today_str:
        return "Today"
    days_overdue = (date.fromisoformat(today_str) - date.fromisoformat(due_date_str)).days
    if days_overdue <= 30:
        return "0-30"
    elif days_overdue <= 60:
        return "30-60"
    elif days_overdue <= 90:
        return "60-90"
    elif days_overdue <= 120:
        return "90-120"
    else:
        return "120+"

def get_due_invoices(party_type):
    conn = get_connection()
    cursor = conn.cursor()
    if party_type == "payable":
        cursor.execute("""
            SELECT mfg_purchase_invoices.id, mfg_purchase_invoices.invoice_no, mfg_purchase_invoices.invoice_date, mfg_purchase_invoices.due_date,
                   mfg_purchase_invoices.total_amount, suppliers.id as party_id, suppliers.name as party_name,
                   COALESCE((SELECT SUM(amount) FROM mfg_purchase_payments WHERE mfg_purchase_payments.invoice_id = mfg_purchase_invoices.id), 0) as paid
            FROM mfg_purchase_invoices
            JOIN suppliers ON mfg_purchase_invoices.supplier_id = suppliers.id
        """)
        rows = cursor.fetchall()
        conn.close()
        result = []
        for r in rows:
            due = round((r["total_amount"] or 0) - (r["paid"] or 0), 2)
            if due <= 0.01:
                continue
            result.append({
                "invoice_no": r["invoice_no"], "party_id": r["party_id"], "party_name": r["party_name"],
                "invoice_date": r["invoice_date"], "due_date": r["due_date"],
                "total_amount": r["total_amount"] or 0, "paid": r["paid"] or 0, "due": due
            })
        return result

    # receivable - unified across job-work (mfg_invoices) and Resharpening (mfg_resharpen_invoices)
    cursor.execute("""
        SELECT mfg_invoices.id, mfg_invoices.invoice_no, mfg_invoices.invoice_date, mfg_invoices.due_date,
               mfg_invoices.total_amount, customers.id as party_id, customers.name as party_name,
               COALESCE((SELECT SUM(amount) FROM mfg_sales_payments WHERE mfg_sales_payments.invoice_id = mfg_invoices.id), 0) as paid
        FROM mfg_invoices
        JOIN customers ON mfg_invoices.customer_id = customers.id
    """)
    new_rows = cursor.fetchall()
    cursor.execute("""
        SELECT mfg_resharpen_invoices.id, mfg_resharpen_invoices.invoice_no, mfg_resharpen_invoices.invoice_date, mfg_resharpen_invoices.due_date,
               mfg_resharpen_invoices.total_amount, customers.id as party_id, customers.name as party_name,
               COALESCE((SELECT SUM(amount) FROM mfg_resharpen_sales_payments WHERE mfg_resharpen_sales_payments.invoice_id = mfg_resharpen_invoices.id), 0) as paid
        FROM mfg_resharpen_invoices
        JOIN customers ON mfg_resharpen_invoices.customer_id = customers.id
    """)
    resharpen_rows = cursor.fetchall()
    conn.close()

    result = []
    for r in new_rows:
        due = round((r["total_amount"] or 0) - (r["paid"] or 0), 2)
        if due <= 0.01:
            continue
        result.append({
            "invoice_no": r["invoice_no"], "party_id": r["party_id"], "party_name": r["party_name"],
            "invoice_date": r["invoice_date"], "due_date": r["due_date"],
            "total_amount": r["total_amount"] or 0, "paid": r["paid"] or 0, "due": due, "source": "new"
        })
    for r in resharpen_rows:
        due = round((r["total_amount"] or 0) - (r["paid"] or 0), 2)
        if due <= 0.01:
            continue
        result.append({
            "invoice_no": r["invoice_no"], "party_id": r["party_id"], "party_name": r["party_name"],
            "invoice_date": r["invoice_date"], "due_date": r["due_date"],
            "total_amount": r["total_amount"] or 0, "paid": r["paid"] or 0, "due": due, "source": "resharpen"
        })
    return result

def get_party_totals(party_type):
    conn = get_connection()
    cursor = conn.cursor()
    if party_type == "payable":
        cursor.execute("""
            SELECT suppliers.id, suppliers.name,
                COALESCE((SELECT SUM(total_amount) FROM mfg_purchase_invoices WHERE mfg_purchase_invoices.supplier_id = suppliers.id), 0) as total_invoiced,
                COALESCE((SELECT SUM(amount) FROM mfg_purchase_payments WHERE mfg_purchase_payments.supplier_id = suppliers.id), 0) as total_paid
            FROM suppliers
        """)
        rows = cursor.fetchall()
        conn.close()
        result = []
        for r in rows:
            due = round((r["total_invoiced"] or 0) - (r["total_paid"] or 0), 2)
            if due > 0.01:
                result.append({
                    "party_id": r["id"], "name": r["name"],
                    "total_invoiced": round(r["total_invoiced"] or 0, 2),
                    "total_paid": round(r["total_paid"] or 0, 2), "due": due
                })
        return result

    # receivable - one combined total per customer across job-work + Resharpening
    cursor.execute("""
        SELECT customers.id, customers.name,
            COALESCE((SELECT SUM(total_amount) FROM mfg_invoices WHERE mfg_invoices.customer_id = customers.id), 0) as new_invoiced,
            COALESCE((SELECT SUM(amount) FROM mfg_sales_payments WHERE mfg_sales_payments.customer_id = customers.id), 0) as new_paid,
            COALESCE((SELECT SUM(total_amount) FROM mfg_resharpen_invoices WHERE mfg_resharpen_invoices.customer_id = customers.id), 0) as resharpen_invoiced,
            COALESCE((SELECT SUM(amount) FROM mfg_resharpen_sales_payments WHERE mfg_resharpen_sales_payments.customer_id = customers.id), 0) as resharpen_paid
        FROM customers
    """)
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        total_invoiced = (r["new_invoiced"] or 0) + (r["resharpen_invoiced"] or 0)
        total_paid = (r["new_paid"] or 0) + (r["resharpen_paid"] or 0)
        due = round(total_invoiced - total_paid, 2)
        if due > 0.01:
            result.append({
                "party_id": r["id"], "name": r["name"],
                "total_invoiced": round(total_invoiced, 2),
                "total_paid": round(total_paid, 2), "due": due
            })
    return result

def build_ageing_matrix(due_invoices, today_str):
    parties = {}
    for inv in due_invoices:
        pid = inv["party_id"]
        if pid not in parties:
            parties[pid] = {"party_id": pid, "name": inv["party_name"]}
            for b in BUCKET_ORDER:
                parties[pid][b] = 0
        bucket = ageing_bucket(inv["due_date"], today_str)
        parties[pid][bucket] += inv["due"]

    result = []
    for p in parties.values():
        for b in BUCKET_ORDER:
            p[b] = round(p[b], 2)
        p["total"] = round(sum(p[b] for b in BUCKET_ORDER), 2)
        result.append(p)
    return result

def filter_rows(rows, query, fields):
    if not query:
        return rows
    q = query.lower()
    return [r for r in rows if any(q in str(r.get(f, "")).lower() for f in fields)]

def sort_and_paginate(rows, sort_key, direction, page, per_page=20):
    if sort_key:
        rows = sorted(rows, key=lambda r: (r.get(sort_key) is None, r.get(sort_key)), reverse=(direction == "desc"))
    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    page_rows = rows[start:start + per_page]
    return page_rows, page, total_pages

@manufacturing_accounting.route("/manufacturing/outstanding")
def outstanding_report():
    view = request.args.get("view", "invoice")
    party_type = request.args.get("type", "receivable")
    sort = request.args.get("sort", "")
    direction = request.args.get("dir", "asc")
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    today = date.today().isoformat()

    due_invoices = get_due_invoices(party_type)

    if view == "party":
        default_sort, default_dir = "due", "desc"
        rows = get_party_totals(party_type)
        rows = filter_rows(rows, search, ["name"])
    elif view == "ageing":
        default_sort, default_dir = "total", "desc"
        rows = build_ageing_matrix(due_invoices, today)
        rows = filter_rows(rows, search, ["name"])
    else:
        view = "invoice"
        default_sort, default_dir = "due_date", "asc"
        rows = filter_rows(due_invoices, search, ["invoice_no", "party_name"])

    if not sort:
        sort = default_sort
        direction = default_dir

    page_rows, page, total_pages = sort_and_paginate(rows, sort, direction, page)

    return render_template("manufacturing/outstanding.html",
        view=view, party_type=party_type, sort=sort, direction=direction,
        page=page, total_pages=total_pages, rows=page_rows, today=today, search=search)

@manufacturing_accounting.route("/manufacturing/statement/<party_type>/<int:party_id>")
def party_statement(party_type, party_id):
    conn = get_connection()
    cursor = conn.cursor()

    resharpen_invoice_rows = []
    resharpen_payment_rows = []
    if party_type == "payable":
        cursor.execute("SELECT name FROM suppliers WHERE id = ?", (party_id,))
        row = cursor.fetchone()
        party_name = row["name"] if row else "Unknown"

        cursor.execute("SELECT invoice_date as txn_date, invoice_no as reference, total_amount as amount FROM mfg_purchase_invoices WHERE supplier_id = ?", (party_id,))
        invoice_rows = cursor.fetchall()

        cursor.execute("SELECT payment_date as txn_date, reference_no as reference, amount FROM mfg_purchase_payments WHERE supplier_id = ?", (party_id,))
        payment_rows = cursor.fetchall()
    else:
        party_type = "receivable"
        cursor.execute("SELECT name FROM customers WHERE id = ?", (party_id,))
        row = cursor.fetchone()
        party_name = row["name"] if row else "Unknown"

        cursor.execute("SELECT invoice_date as txn_date, invoice_no as reference, total_amount as amount FROM mfg_invoices WHERE customer_id = ?", (party_id,))
        invoice_rows = cursor.fetchall()

        cursor.execute("SELECT payment_date as txn_date, reference_no as reference, amount FROM mfg_sales_payments WHERE customer_id = ?", (party_id,))
        payment_rows = cursor.fetchall()

        # unified statement - also pull this customer's Resharpening invoices/payments in,
        # tagged so they're distinguishable even though they live in separate tables
        cursor.execute("SELECT invoice_date as txn_date, invoice_no as reference, total_amount as amount FROM mfg_resharpen_invoices WHERE customer_id = ?", (party_id,))
        resharpen_invoice_rows = cursor.fetchall()

        cursor.execute("SELECT payment_date as txn_date, reference_no as reference, amount FROM mfg_resharpen_sales_payments WHERE customer_id = ?", (party_id,))
        resharpen_payment_rows = cursor.fetchall()

    conn.close()

    entries = []
    for r in invoice_rows:
        entries.append({"date": r["txn_date"], "type": "Invoice", "reference": r["reference"], "debit": r["amount"] or 0, "credit": 0})
    for r in resharpen_invoice_rows:
        entries.append({"date": r["txn_date"], "type": "Invoice", "reference": (r["reference"] or "") + " (Resharpen)", "debit": r["amount"] or 0, "credit": 0})
    for r in payment_rows:
        entries.append({"date": r["txn_date"], "type": "Payment", "reference": r["reference"] or "On Account", "debit": 0, "credit": r["amount"] or 0})
    for r in resharpen_payment_rows:
        entries.append({"date": r["txn_date"], "type": "Payment", "reference": (r["reference"] or "On Account") + " (Resharpen)", "debit": 0, "credit": r["amount"] or 0})

    entries.sort(key=lambda e: (e["date"] or "", e["type"]))

    balance = 0
    for e in entries:
        balance = round(balance + e["debit"] - e["credit"], 2)
        e["balance"] = balance

    total_debit = round(sum(e["debit"] for e in entries), 2)
    total_credit = round(sum(e["credit"] for e in entries), 2)

    page = request.args.get("page", 1, type=int)
    per_page = 20
    total_pages = max(1, (len(entries) + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    page_entries = entries[(page - 1) * per_page: (page - 1) * per_page + per_page]

    return render_template("manufacturing/party_statement.html",
        party_type=party_type, party_id=party_id, party_name=party_name,
        entries=page_entries, total_debit=total_debit, total_credit=total_credit,
        closing_balance=balance, page=page, total_pages=total_pages)

@manufacturing_accounting.route("/manufacturing/outstanding/party/<party_type>/<int:party_id>")
def outstanding_party_detail(party_type, party_id):
    sort = request.args.get("sort", "due_date")
    direction = request.args.get("dir", "asc")
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()

    due_invoices = get_due_invoices(party_type)
    party_invoices = [inv for inv in due_invoices if inv["party_id"] == party_id]
    party_name = party_invoices[0]["party_name"] if party_invoices else None

    if not party_name:
        conn = get_connection()
        cursor = conn.cursor()
        table = "suppliers" if party_type == "payable" else "customers"
        cursor.execute("SELECT name FROM " + table + " WHERE id = ?", (party_id,))
        row = cursor.fetchone()
        conn.close()
        party_name = row["name"] if row else "Unknown"

    party_invoices = filter_rows(party_invoices, search, ["invoice_no"])
    page_rows, page, total_pages = sort_and_paginate(party_invoices, sort, direction, page)

    return render_template("manufacturing/outstanding_party_detail.html",
        party_type=party_type, party_id=party_id, party_name=party_name,
        sort=sort, direction=direction, page=page, total_pages=total_pages, rows=page_rows, search=search)

# ============================================
# DAY BOOK (all money in and out for a date range, split by mode)
# ============================================

@manufacturing_accounting.route("/manufacturing/daybook")
def day_book():
    from_date = request.args.get("from_date", "") or date.today().isoformat()
    to_date = request.args.get("to_date", "") or date.today().isoformat()
    page = request.args.get("page", 1, type=int)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT mfg_sales_payments.payment_date as txn_date, mfg_sales_payments.amount, mfg_sales_payments.payment_mode,
               mfg_sales_payments.reference_no, customers.name as party_name
        FROM mfg_sales_payments
        JOIN customers ON mfg_sales_payments.customer_id = customers.id
        WHERE mfg_sales_payments.payment_date BETWEEN ? AND ?
    """, (from_date, to_date))
    received_rows = cursor.fetchall()

    cursor.execute("""
        SELECT mfg_purchase_payments.payment_date as txn_date, mfg_purchase_payments.amount, mfg_purchase_payments.payment_mode,
               mfg_purchase_payments.reference_no, suppliers.name as party_name
        FROM mfg_purchase_payments
        JOIN suppliers ON mfg_purchase_payments.supplier_id = suppliers.id
        WHERE mfg_purchase_payments.payment_date BETWEEN ? AND ?
    """, (from_date, to_date))
    paid_rows = cursor.fetchall()

    # unified day book - Resharpening's payments received count as cash in too
    cursor.execute("""
        SELECT mfg_resharpen_sales_payments.payment_date as txn_date, mfg_resharpen_sales_payments.amount, mfg_resharpen_sales_payments.payment_mode,
               mfg_resharpen_sales_payments.reference_no, customers.name as party_name
        FROM mfg_resharpen_sales_payments
        JOIN customers ON mfg_resharpen_sales_payments.customer_id = customers.id
        WHERE mfg_resharpen_sales_payments.payment_date BETWEEN ? AND ?
    """, (from_date, to_date))
    resharpen_received_rows = cursor.fetchall()

    conn.close()

    entries = []
    for r in received_rows:
        entries.append({"date": r["txn_date"], "type": "Received", "party_name": r["party_name"], "mode": r["payment_mode"], "reference_no": r["reference_no"], "amount": r["amount"] or 0})
    for r in resharpen_received_rows:
        entries.append({"date": r["txn_date"], "type": "Received", "party_name": r["party_name"], "mode": r["payment_mode"], "reference_no": r["reference_no"], "amount": r["amount"] or 0})
    for r in paid_rows:
        entries.append({"date": r["txn_date"], "type": "Paid", "party_name": r["party_name"], "mode": r["payment_mode"], "reference_no": r["reference_no"], "amount": r["amount"] or 0})

    entries.sort(key=lambda e: (e["date"] or "", e["type"]))

    total_received = round(sum(e["amount"] for e in entries if e["type"] == "Received"), 2)
    total_paid = round(sum(e["amount"] for e in entries if e["type"] == "Paid"), 2)
    net = round(total_received - total_paid, 2)

    modes = ["cash", "bank", "upi", "cheque"]
    mode_summary = []
    for m in modes:
        in_amt = round(sum(e["amount"] for e in entries if e["type"] == "Received" and e["mode"] == m), 2)
        out_amt = round(sum(e["amount"] for e in entries if e["type"] == "Paid" and e["mode"] == m), 2)
        mode_summary.append({"mode": m, "in": in_amt, "out": out_amt, "net": round(in_amt - out_amt, 2)})

    total = len(entries)
    per_page = 20
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    page_entries = entries[(page - 1) * per_page: (page - 1) * per_page + per_page]

    return render_template("manufacturing/day_book.html",
        entries=page_entries, from_date=from_date, to_date=to_date,
        total_received=total_received, total_paid=total_paid, net=net,
        mode_summary=mode_summary, page=page, total_pages=total_pages)

# ============================================
# SALES DAY BOOK (every sales-side document for a date range, chronologically -
# Customer Orders, Customer Delivery Challans, Invoices - not money, documents)
# ============================================

@manufacturing_accounting.route("/manufacturing/sales-daybook")
def sales_day_book():
    from_date = request.args.get("from_date", "") or date.today().isoformat()
    to_date = request.args.get("to_date", "") or date.today().isoformat()
    page = request.args.get("page", 1, type=int)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT mfg_customer_orders.id as doc_id, mfg_customer_orders.order_no as doc_no,
               mfg_customer_orders.order_date as txn_date, customers.name as party_name,
               COALESCE((SELECT SUM(quantity) FROM mfg_customer_order_items WHERE order_id = mfg_customer_orders.id), 0) as quantity,
               COALESCE((SELECT SUM(quantity * COALESCE(rate, 0)) FROM mfg_customer_order_items WHERE order_id = mfg_customer_orders.id), 0) as amount
        FROM mfg_customer_orders
        JOIN customers ON mfg_customer_orders.customer_id = customers.id
        WHERE mfg_customer_orders.order_date BETWEEN ? AND ?
    """, (from_date, to_date))
    order_rows = cursor.fetchall()

    cursor.execute("""
        SELECT mfg_challans.id as doc_id, mfg_challans.challan_no as doc_no,
               mfg_challans.challan_date as txn_date, customers.name as party_name,
               COALESCE((SELECT SUM(mfg_challan_items.quantity) FROM mfg_challan_items WHERE challan_id = mfg_challans.id), 0) as quantity
        FROM mfg_challans
        JOIN customers ON mfg_challans.party_id = customers.id
        WHERE mfg_challans.challan_type = 'customer_delivery' AND mfg_challans.challan_date BETWEEN ? AND ?
    """, (from_date, to_date))
    challan_rows = cursor.fetchall()

    cursor.execute("""
        SELECT mfg_invoices.id as doc_id, mfg_invoices.invoice_no as doc_no,
               mfg_invoices.invoice_date as txn_date, customers.name as party_name,
               mfg_invoices.total_amount as amount,
               COALESCE((SELECT SUM(quantity) FROM mfg_invoice_items WHERE invoice_id = mfg_invoices.id), 0) as quantity
        FROM mfg_invoices
        JOIN customers ON mfg_invoices.customer_id = customers.id
        WHERE mfg_invoices.invoice_date BETWEEN ? AND ?
    """, (from_date, to_date))
    invoice_rows = cursor.fetchall()

    conn.close()

    entries = []
    for r in order_rows:
        entries.append({"date": r["txn_date"], "type": "Order", "doc_no": r["doc_no"], "doc_id": r["doc_id"],
                         "party_name": r["party_name"], "quantity": r["quantity"], "amount": round(r["amount"] or 0, 2)})
    for r in challan_rows:
        entries.append({"date": r["txn_date"], "type": "Challan", "doc_no": r["doc_no"], "doc_id": r["doc_id"],
                         "party_name": r["party_name"], "quantity": r["quantity"], "amount": None})
    for r in invoice_rows:
        entries.append({"date": r["txn_date"], "type": "Invoice", "doc_no": r["doc_no"], "doc_id": r["doc_id"],
                         "party_name": r["party_name"], "quantity": r["quantity"], "amount": round(r["amount"] or 0, 2)})

    entries.sort(key=lambda e: (e["date"] or "", e["type"], e["doc_no"] or ""))

    total_order_value = round(sum(e["amount"] or 0 for e in entries if e["type"] == "Order"), 2)
    total_delivered_qty = round(sum(e["quantity"] or 0 for e in entries if e["type"] == "Challan"), 2)
    total_invoiced = round(sum(e["amount"] or 0 for e in entries if e["type"] == "Invoice"), 2)

    total = len(entries)
    per_page = 20
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    page_entries = entries[(page - 1) * per_page: (page - 1) * per_page + per_page]

    return render_template("manufacturing/sales_day_book.html",
        entries=page_entries, from_date=from_date, to_date=to_date,
        total_order_value=total_order_value, total_delivered_qty=total_delivered_qty, total_invoiced=total_invoiced,
        page=page, total_pages=total_pages)

# ============================================
# PURCHASE DAY BOOK (every purchase-side document for a date range, chronologically -
# Purchase Orders, GRNs, Purchase Invoices, Purchase Returns)
# ============================================

@manufacturing_accounting.route("/manufacturing/purchase-daybook")
def purchase_day_book():
    from_date = request.args.get("from_date", "") or date.today().isoformat()
    to_date = request.args.get("to_date", "") or date.today().isoformat()
    page = request.args.get("page", 1, type=int)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT mfg_purchase_orders.id as doc_id, mfg_purchase_orders.order_no as doc_no,
               mfg_purchase_orders.order_date as txn_date, suppliers.name as party_name,
               mfg_purchase_orders.total_amount as amount, mfg_purchase_orders.status as status
        FROM mfg_purchase_orders
        LEFT JOIN suppliers ON mfg_purchase_orders.supplier_id = suppliers.id
        WHERE mfg_purchase_orders.order_date BETWEEN ? AND ?
    """, (from_date, to_date))
    po_rows = cursor.fetchall()

    cursor.execute("""
        SELECT mfg_purchase_grn.id as doc_id, mfg_purchase_grn.grn_no as doc_no,
               mfg_purchase_grn.grn_date as txn_date, suppliers.name as party_name,
               mfg_purchase_grn.total_amount as amount
        FROM mfg_purchase_grn
        LEFT JOIN suppliers ON mfg_purchase_grn.supplier_id = suppliers.id
        WHERE mfg_purchase_grn.grn_date BETWEEN ? AND ?
    """, (from_date, to_date))
    grn_rows = cursor.fetchall()

    cursor.execute("""
        SELECT mfg_purchase_invoices.id as doc_id, mfg_purchase_invoices.invoice_no as doc_no,
               mfg_purchase_invoices.invoice_date as txn_date, suppliers.name as party_name,
               mfg_purchase_invoices.total_amount as amount, mfg_purchase_invoices.status as status
        FROM mfg_purchase_invoices
        LEFT JOIN suppliers ON mfg_purchase_invoices.supplier_id = suppliers.id
        WHERE mfg_purchase_invoices.invoice_date BETWEEN ? AND ?
    """, (from_date, to_date))
    invoice_rows = cursor.fetchall()

    cursor.execute("""
        SELECT mfg_purchase_returns.id as doc_id, mfg_purchase_returns.return_no as doc_no,
               mfg_purchase_returns.return_date as txn_date, suppliers.name as party_name,
               mfg_purchase_returns.total_amount as amount
        FROM mfg_purchase_returns
        LEFT JOIN suppliers ON mfg_purchase_returns.supplier_id = suppliers.id
        WHERE mfg_purchase_returns.return_date BETWEEN ? AND ?
    """, (from_date, to_date))
    return_rows = cursor.fetchall()

    conn.close()

    entries = []
    for r in po_rows:
        entries.append({"date": r["txn_date"], "type": "PO", "doc_no": r["doc_no"], "doc_id": r["doc_id"],
                         "party_name": r["party_name"], "amount": round(r["amount"] or 0, 2), "status": r["status"]})
    for r in grn_rows:
        entries.append({"date": r["txn_date"], "type": "GRN", "doc_no": r["doc_no"], "doc_id": r["doc_id"],
                         "party_name": r["party_name"], "amount": round(r["amount"] or 0, 2), "status": None})
    for r in invoice_rows:
        entries.append({"date": r["txn_date"], "type": "Invoice", "doc_no": r["doc_no"], "doc_id": r["doc_id"],
                         "party_name": r["party_name"], "amount": round(r["amount"] or 0, 2), "status": r["status"]})
    for r in return_rows:
        entries.append({"date": r["txn_date"], "type": "Return", "doc_no": r["doc_no"], "doc_id": r["doc_id"],
                         "party_name": r["party_name"], "amount": round(r["amount"] or 0, 2), "status": None})

    entries.sort(key=lambda e: (e["date"] or "", e["type"], e["doc_no"] or ""))

    total_po_value = round(sum(e["amount"] for e in entries if e["type"] == "PO"), 2)
    total_grn_value = round(sum(e["amount"] for e in entries if e["type"] == "GRN"), 2)
    total_invoiced = round(sum(e["amount"] for e in entries if e["type"] == "Invoice"), 2)
    total_returned = round(sum(e["amount"] for e in entries if e["type"] == "Return"), 2)

    total = len(entries)
    per_page = 20
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    page_entries = entries[(page - 1) * per_page: (page - 1) * per_page + per_page]

    return render_template("manufacturing/purchase_day_book.html",
        entries=page_entries, from_date=from_date, to_date=to_date,
        total_po_value=total_po_value, total_grn_value=total_grn_value,
        total_invoiced=total_invoiced, total_returned=total_returned,
        page=page, total_pages=total_pages)
