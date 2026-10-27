from flask import Blueprint
from flask import render_template, request, redirect, url_for, flash
from database import get_connection
from modules.auth import require_system
from datetime import date

accounting = Blueprint("accounting", __name__)
accounting.before_request(require_system("distribution"))
from modules.permissions import enforce_permissions
accounting.before_request(enforce_permissions)

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

@accounting.route("/sales-payments")
def sales_payments_list():
    page = request.args.get("page", 1, type=int)
    customer_id = request.args.get("customer_id", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")
    per_page = 20
    offset = (page - 1) * per_page

    conn = get_connection()
    cursor = conn.cursor()

    where_clauses = []
    params = []
    if customer_id:
        where_clauses.append("sales_payments.customer_id = ?")
        params.append(customer_id)
    if from_date:
        where_clauses.append("sales_payments.payment_date >= ?")
        params.append(from_date)
    if to_date:
        where_clauses.append("sales_payments.payment_date <= ?")
        params.append(to_date)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    cursor.execute("""
        SELECT sales_payments.*, sales_invoices.invoice_no, customers.name as customer_name
        FROM sales_payments
        LEFT JOIN sales_invoices ON sales_payments.invoice_id = sales_invoices.id
        JOIN customers ON sales_payments.customer_id = customers.id
        """ + where_sql + """
        ORDER BY sales_payments.payment_date DESC, sales_payments.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    payments = cursor.fetchall()

    cursor.execute("""
        SELECT COUNT(*) as cnt, COALESCE(SUM(sales_payments.amount), 0) as total_amt
        FROM sales_payments
        """ + where_sql + """
    """, params)
    totals_row = cursor.fetchone()
    total = totals_row["cnt"]
    total_amount = totals_row["total_amt"]
    total_pages = (total + per_page - 1) // per_page

    cursor.execute("SELECT id, name FROM customers ORDER BY name")
    customers = cursor.fetchall()

    conn.close()
    return render_template("accounting/sales_payments.html",
        payments=payments, page=page, total_pages=total_pages,
        customers=customers, customer_id=customer_id, from_date=from_date, to_date=to_date,
        total_amount=total_amount)

@accounting.route("/sales-payments/add", methods=["GET", "POST"])
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
        cursor.execute("""
            INSERT INTO sales_payments (invoice_id, customer_id, payment_date, amount, payment_mode, reference_no, remarks)
            VALUES (?,?,?,?,?,?,?)
        """, (invoice_id, customer_id, payment_date, amount, payment_mode, reference_no, remarks))
        conn.commit()
        conn.close()
        flash("Payment recorded successfully!", "success")
        return redirect(url_for("accounting.sales_payments_list"))

    cursor.execute("SELECT id, name FROM customers ORDER BY name")
    customers = cursor.fetchall()
    conn.close()
    return render_template("accounting/add_sales_payment.html", customers=customers)

@accounting.route("/sales-payments/edit/<int:payment_id>", methods=["GET", "POST"])
def edit_sales_payment(payment_id):
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        payment_date = request.form["payment_date"]
        amount = request.form["amount"]
        payment_mode = request.form.get("payment_mode") or "cash"
        reference_no = request.form.get("reference_no")
        remarks = request.form.get("remarks")
        cursor.execute("""
            UPDATE sales_payments SET payment_date=?, amount=?, payment_mode=?, reference_no=?, remarks=?
            WHERE id=?
        """, (payment_date, amount, payment_mode, reference_no, remarks, payment_id))
        conn.commit()
        conn.close()
        flash("Payment updated successfully!", "success")
        return redirect(url_for("accounting.sales_payments_list"))

    cursor.execute("""
        SELECT sales_payments.*, sales_invoices.invoice_no, customers.name as customer_name
        FROM sales_payments
        LEFT JOIN sales_invoices ON sales_payments.invoice_id = sales_invoices.id
        JOIN customers ON sales_payments.customer_id = customers.id
        WHERE sales_payments.id = ?
    """, (payment_id,))
    payment = cursor.fetchone()
    conn.close()
    return render_template("accounting/edit_sales_payment.html", payment=payment)


@accounting.route("/sales-payments/delete/<int:payment_id>")
def delete_sales_payment(payment_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sales_payments WHERE id=?", (payment_id,))
    conn.commit()
    conn.close()
    flash("Payment deleted successfully!", "danger")
    return redirect(url_for("accounting.sales_payments_list"))

@accounting.route("/api/customer-invoices/<int:customer_id>")
def api_customer_invoices(customer_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sales_invoices.id, sales_invoices.invoice_no, sales_invoices.invoice_date, sales_invoices.due_date,
               sales_invoices.total_amount,
               COALESCE((SELECT SUM(amount) FROM sales_payments WHERE sales_payments.invoice_id = sales_invoices.id), 0) as paid
        FROM sales_invoices
        WHERE sales_invoices.customer_id = ?
        ORDER BY sales_invoices.invoice_date DESC
    """, (customer_id,))
    rows = cursor.fetchall()
    conn.close()
    result = []
    for r in rows:
        due, status = invoice_status(r["total_amount"], r["paid"])
        result.append({
            "id": r["id"],
            "invoice_no": r["invoice_no"],
            "invoice_date": r["invoice_date"],
            "due_date": r["due_date"],
            "total_amount": r["total_amount"] or 0,
            "paid": r["paid"] or 0,
            "due": due,
            "status": status
        })
    return {"invoices": result}

# ============================================
# PURCHASE PAYMENTS (money paid to suppliers)
# ============================================

@accounting.route("/purchase-payments")
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
        where_clauses.append("purchase_payments.supplier_id = ?")
        params.append(supplier_id)
    if from_date:
        where_clauses.append("purchase_payments.payment_date >= ?")
        params.append(from_date)
    if to_date:
        where_clauses.append("purchase_payments.payment_date <= ?")
        params.append(to_date)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    cursor.execute("""
        SELECT purchase_payments.*, purchase_invoices.invoice_no, suppliers.name as supplier_name
        FROM purchase_payments
        LEFT JOIN purchase_invoices ON purchase_payments.invoice_id = purchase_invoices.id
        JOIN suppliers ON purchase_payments.supplier_id = suppliers.id
        """ + where_sql + """
        ORDER BY purchase_payments.payment_date DESC, purchase_payments.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    payments = cursor.fetchall()

    cursor.execute("""
        SELECT COUNT(*) as cnt, COALESCE(SUM(purchase_payments.amount), 0) as total_amt
        FROM purchase_payments
        """ + where_sql + """
    """, params)
    totals_row = cursor.fetchone()
    total = totals_row["cnt"]
    total_amount = totals_row["total_amt"]
    total_pages = (total + per_page - 1) // per_page

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()

    conn.close()
    return render_template("accounting/purchase_payments.html",
        payments=payments, page=page, total_pages=total_pages,
        suppliers=suppliers, supplier_id=supplier_id, from_date=from_date, to_date=to_date,
        total_amount=total_amount)

@accounting.route("/purchase-payments/add", methods=["GET", "POST"])
def add_purchase_payment():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        invoice_id = request.form.get("invoice_id") or None
        supplier_id = request.form["supplier_id"]
        payment_date = request.form["payment_date"]
        amount = request.form["amount"]
        payment_mode = request.form.get("payment_mode") or "cash"
        reference_no = request.form.get("reference_no")
        remarks = request.form.get("remarks")
        cursor.execute("""
            INSERT INTO purchase_payments (invoice_id, supplier_id, payment_date, amount, payment_mode, reference_no, remarks)
            VALUES (?,?,?,?,?,?,?)
        """, (invoice_id, supplier_id, payment_date, amount, payment_mode, reference_no, remarks))
        conn.commit()
        conn.close()
        flash("Payment recorded successfully!", "success")
        return redirect(url_for("accounting.purchase_payments_list"))

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    conn.close()
    return render_template("accounting/add_purchase_payment.html", suppliers=suppliers)

@accounting.route("/purchase-payments/edit/<int:payment_id>", methods=["GET", "POST"])
def edit_purchase_payment(payment_id):
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        payment_date = request.form["payment_date"]
        amount = request.form["amount"]
        payment_mode = request.form.get("payment_mode") or "cash"
        reference_no = request.form.get("reference_no")
        remarks = request.form.get("remarks")
        cursor.execute("""
            UPDATE purchase_payments SET payment_date=?, amount=?, payment_mode=?, reference_no=?, remarks=?
            WHERE id=?
        """, (payment_date, amount, payment_mode, reference_no, remarks, payment_id))
        conn.commit()
        conn.close()
        flash("Payment updated successfully!", "success")
        return redirect(url_for("accounting.purchase_payments_list"))

    cursor.execute("""
        SELECT purchase_payments.*, purchase_invoices.invoice_no, suppliers.name as supplier_name
        FROM purchase_payments
        LEFT JOIN purchase_invoices ON purchase_payments.invoice_id = purchase_invoices.id
        JOIN suppliers ON purchase_payments.supplier_id = suppliers.id
        WHERE purchase_payments.id = ?
    """, (payment_id,))
    payment = cursor.fetchone()
    conn.close()
    return render_template("accounting/edit_purchase_payment.html", payment=payment)


@accounting.route("/purchase-payments/delete/<int:payment_id>")
def delete_purchase_payment(payment_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM purchase_payments WHERE id=?", (payment_id,))
    conn.commit()
    conn.close()
    flash("Payment deleted successfully!", "danger")
    return redirect(url_for("accounting.purchase_payments_list"))

@accounting.route("/api/supplier-invoices/<int:supplier_id>")
def api_supplier_invoices(supplier_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT purchase_invoices.id, purchase_invoices.invoice_no, purchase_invoices.invoice_date, purchase_invoices.due_date,
               purchase_invoices.total_amount,
               COALESCE((SELECT SUM(amount) FROM purchase_payments WHERE purchase_payments.invoice_id = purchase_invoices.id), 0) as paid
        FROM purchase_invoices
        WHERE purchase_invoices.supplier_id = ?
        ORDER BY purchase_invoices.invoice_date DESC
    """, (supplier_id,))
    rows = cursor.fetchall()
    conn.close()
    result = []
    for r in rows:
        due, status = invoice_status(r["total_amount"], r["paid"])
        result.append({
            "id": r["id"],
            "invoice_no": r["invoice_no"],
            "invoice_date": r["invoice_date"],
            "due_date": r["due_date"],
            "total_amount": r["total_amount"] or 0,
            "paid": r["paid"] or 0,
            "due": due,
            "status": status
        })
    return {"invoices": result}

# ============================================
# OUTSTANDING REPORT (everything still owed, both directions)
# Views: invoice-wise, party-wise, ageing (day-wise matrix)
# Each view: pick Receivable or Payable separately, sort by any column, 20 rows/page
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
    """Every invoice (sales or purchase) that still has money owed on it, as plain dicts."""
    conn = get_connection()
    cursor = conn.cursor()
    if party_type == "payable":
        cursor.execute("""
            SELECT purchase_invoices.id, purchase_invoices.invoice_no, purchase_invoices.invoice_date, purchase_invoices.due_date,
                   purchase_invoices.total_amount, suppliers.id as party_id, suppliers.name as party_name,
                   COALESCE((SELECT SUM(amount) FROM purchase_payments WHERE purchase_payments.invoice_id = purchase_invoices.id), 0) as paid
            FROM purchase_invoices
            JOIN suppliers ON purchase_invoices.supplier_id = suppliers.id
        """)
    else:
        cursor.execute("""
            SELECT sales_invoices.id, sales_invoices.invoice_no, sales_invoices.invoice_date, sales_invoices.due_date,
                   sales_invoices.total_amount, customers.id as party_id, customers.name as party_name,
                   COALESCE((SELECT SUM(amount) FROM sales_payments WHERE sales_payments.invoice_id = sales_invoices.id), 0) as paid
            FROM sales_invoices
            JOIN customers ON sales_invoices.customer_id = customers.id
        """)
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        due = round((r["total_amount"] or 0) - (r["paid"] or 0), 2)
        if due <= 0.01:
            continue
        result.append({
            "invoice_no": r["invoice_no"],
            "party_id": r["party_id"],
            "party_name": r["party_name"],
            "invoice_date": r["invoice_date"],
            "due_date": r["due_date"],
            "total_amount": r["total_amount"] or 0,
            "paid": r["paid"] or 0,
            "due": due
        })
    return result

def get_party_totals(party_type):
    """One row per customer/supplier with their true totals (includes on-account payments)."""
    conn = get_connection()
    cursor = conn.cursor()
    if party_type == "payable":
        cursor.execute("""
            SELECT suppliers.id, suppliers.name,
                COALESCE((SELECT SUM(total_amount) FROM purchase_invoices WHERE purchase_invoices.supplier_id = suppliers.id), 0) as total_invoiced,
                COALESCE((SELECT SUM(amount) FROM purchase_payments WHERE purchase_payments.supplier_id = suppliers.id), 0) as total_paid
            FROM suppliers
        """)
    else:
        cursor.execute("""
            SELECT customers.id, customers.name,
                COALESCE((SELECT SUM(total_amount) FROM sales_invoices WHERE sales_invoices.customer_id = customers.id), 0) as total_invoiced,
                COALESCE((SELECT SUM(amount) FROM sales_payments WHERE sales_payments.customer_id = customers.id), 0) as total_paid
            FROM customers
        """)
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        due = round((r["total_invoiced"] or 0) - (r["total_paid"] or 0), 2)
        if due > 0.01:
            result.append({
                "party_id": r["id"],
                "name": r["name"],
                "total_invoiced": round(r["total_invoiced"] or 0, 2),
                "total_paid": round(r["total_paid"] or 0, 2),
                "due": due
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
    """Keep only rows where the search text appears in one of the given fields."""
    if not query:
        return rows
    q = query.lower()
    return [r for r in rows if any(q in str(r.get(f, "")).lower() for f in fields)]

def sort_and_paginate(rows, sort_key, direction, page, per_page=20):
    """Shared by every Outstanding Report table: sort by any column, then cut to one page of 20."""
    if sort_key:
        rows = sorted(rows, key=lambda r: (r.get(sort_key) is None, r.get(sort_key)), reverse=(direction == "desc"))
    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    page_rows = rows[start:start + per_page]
    return page_rows, page, total_pages

@accounting.route("/outstanding")
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

    return render_template("accounting/outstanding.html",
        view=view, party_type=party_type, sort=sort, direction=direction,
        page=page, total_pages=total_pages, rows=page_rows, today=today, search=search)

@accounting.route("/statement/<party_type>/<int:party_id>")
def party_statement(party_type, party_id):
    conn = get_connection()
    cursor = conn.cursor()

    if party_type == "payable":
        cursor.execute("SELECT name FROM suppliers WHERE id = ?", (party_id,))
        row = cursor.fetchone()
        party_name = row["name"] if row else "Unknown"

        cursor.execute("SELECT invoice_date as txn_date, invoice_no as reference, total_amount as amount FROM purchase_invoices WHERE supplier_id = ?", (party_id,))
        invoice_rows = cursor.fetchall()

        cursor.execute("SELECT payment_date as txn_date, reference_no as reference, amount FROM purchase_payments WHERE supplier_id = ?", (party_id,))
        payment_rows = cursor.fetchall()
    else:
        party_type = "receivable"
        cursor.execute("SELECT name FROM customers WHERE id = ?", (party_id,))
        row = cursor.fetchone()
        party_name = row["name"] if row else "Unknown"

        cursor.execute("SELECT invoice_date as txn_date, invoice_no as reference, total_amount as amount FROM sales_invoices WHERE customer_id = ?", (party_id,))
        invoice_rows = cursor.fetchall()

        cursor.execute("SELECT payment_date as txn_date, reference_no as reference, amount FROM sales_payments WHERE customer_id = ?", (party_id,))
        payment_rows = cursor.fetchall()

    conn.close()

    entries = []
    for r in invoice_rows:
        entries.append({"date": r["txn_date"], "type": "Invoice", "reference": r["reference"], "debit": r["amount"] or 0, "credit": 0})
    for r in payment_rows:
        entries.append({"date": r["txn_date"], "type": "Payment", "reference": r["reference"] or "On Account", "debit": 0, "credit": r["amount"] or 0})

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

    return render_template("accounting/party_statement.html",
        party_type=party_type, party_id=party_id, party_name=party_name,
        entries=page_entries, total_debit=total_debit, total_credit=total_credit,
        closing_balance=balance, page=page, total_pages=total_pages)


@accounting.route("/outstanding/party/<party_type>/<int:party_id>")
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

    return render_template("accounting/outstanding_party_detail.html",
        party_type=party_type, party_id=party_id, party_name=party_name,
        sort=sort, direction=direction, page=page, total_pages=total_pages, rows=page_rows, search=search)


# ============================================
# DAY BOOK (all money in and out for a date range, split by mode)
# ============================================

@accounting.route("/daybook")
def day_book():
    from_date = request.args.get("from_date", "") or date.today().isoformat()
    to_date = request.args.get("to_date", "") or date.today().isoformat()
    page = request.args.get("page", 1, type=int)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT sales_payments.payment_date as txn_date, sales_payments.amount, sales_payments.payment_mode,
               sales_payments.reference_no, customers.name as party_name
        FROM sales_payments
        JOIN customers ON sales_payments.customer_id = customers.id
        WHERE sales_payments.payment_date BETWEEN ? AND ?
    """, (from_date, to_date))
    received_rows = cursor.fetchall()

    cursor.execute("""
        SELECT purchase_payments.payment_date as txn_date, purchase_payments.amount, purchase_payments.payment_mode,
               purchase_payments.reference_no, suppliers.name as party_name
        FROM purchase_payments
        JOIN suppliers ON purchase_payments.supplier_id = suppliers.id
        WHERE purchase_payments.payment_date BETWEEN ? AND ?
    """, (from_date, to_date))
    paid_rows = cursor.fetchall()

    conn.close()

    entries = []
    for r in received_rows:
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

    return render_template("accounting/day_book.html",
        entries=page_entries, from_date=from_date, to_date=to_date,
        total_received=total_received, total_paid=total_paid, net=net,
        mode_summary=mode_summary, page=page, total_pages=total_pages)
