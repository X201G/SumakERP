from flask import Blueprint
from flask import render_template, request, redirect, url_for, flash
from database import get_connection
from modules.stock import add_stock_movement
from modules.crm import sync_quotation_opportunity

sales = Blueprint("sales", __name__)
from modules.auth import require_system
sales.before_request(require_system("distribution"))

# ============================================
# SALES INVOICE
# ============================================
@sales.route("/sales-invoices")
def sales_invoices_list():
    page = request.args.get("page",1,type=int)
    search = request.args.get("search", "")
    status_filter = request.args.get("status", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")
    sort_by = request.args.get("sort", "date_desc")
    per_page = 20
    offset = (page-1) * per_page
    conn = get_connection()
    cursor = conn.cursor()
    search_term = "%" + search + "%"
    where = """ WHERE (sales_invoices.invoice_no LIKE ? OR customers.name LIKE ? OR sales_invoices.po_no LIKE ?)"""
    params = [search_term, search_term, search_term]
    if status_filter:
        where += " AND sales_invoices.status = ?"
        params.append(status_filter)
    if from_date:
        where += " AND sales_invoices.invoice_date >= ?"
        params.append(from_date)
    if to_date:
        where += " AND sales_invoices.invoice_date <= ?"
        params.append(to_date)
    sort_options = {
        "date_desc": "sales_invoices.invoice_date DESC",
        "date_asc": "sales_invoices.invoice_date ASC",
        "amount_desc": "sales_invoices.total_amount DESC",
        "amount_asc": "sales_invoices.total_amount ASC"
    }
    order_by = sort_options.get(sort_by, "sales_invoices.invoice_date DESC")
    cursor.execute("""
        SELECT sales_invoices.*,
               customers.name as customer_name
        FROM sales_invoices
        LEFT JOIN customers ON sales_invoices.customer_id = customers.id
    """ + where + " ORDER BY " + order_by + " LIMIT ? OFFSET ?", params + [per_page, offset])
    invoices = cursor.fetchall()
    cursor.execute("""
        SELECT COUNT(*)
        FROM sales_invoices
        LEFT JOIN customers ON sales_invoices.customer_id = customers.id
    """ + where, params)
    total = cursor.fetchone()[0]
    total_pages = (total + per_page - 1) // per_page
    conn.close()
    return render_template("sales/sales_invoices.html", invoices=invoices, page=page, total_pages=total_pages, search=search, status_filter=status_filter, from_date=from_date, to_date=to_date, sort_by=sort_by)
@sales.route("/sales-invoices/add", methods=["GET", "POST"])
def add_sales_invoice():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        transporter_id = request.form.get("transporter_id")
        invoice_date = request.form.get("invoice_date")
        po_no = request.form.get("po_no")
        po_date = request.form.get("po_date")
        cr_days = request.form.get("cr_days") or 0
        due_date = request.form.get("due_date")
        remarks = request.form.get("remarks")
        invoice_no = "SI-" + invoice_date.replace("-", "") + "-" + str(int(cursor.execute("SELECT COUNT(*) FROM sales_invoices").fetchone()[0]) + 1).zfill(4)
        cursor.execute("""
            INSERT INTO sales_invoices (invoice_no, invoice_date, customer_id, transporter_id, po_no, po_date, cr_days, due_date, remarks)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (invoice_no, invoice_date, customer_id, transporter_id, po_no, po_date, cr_days, due_date, remarks))
        invoice_id = cursor.lastrowid
        products = request.form.getlist("product_id[]")
        unit_ids = request.form.getlist("unit_id[]")
        hsn_ids = request.form.getlist("hsn_id[]")
        quantities = request.form.getlist("quantity[]")
        rates = request.form.getlist("rate[]")
        discounts = request.form.getlist("discount[]")
        tax_rates = request.form.getlist("tax_rate[]")
        source_types = request.form.getlist("source_type[]")
        source_item_ids = request.form.getlist("source_item_id[]")
        total_amount = 0
        for i in range(len(products)):
            qty = float(quantities[i])
            rate = float(rates[i])
            discount = float(discounts[i]) if discounts[i] else 0
            tax_rate = float(tax_rates[i]) if tax_rates[i] else 18.0
            bef_tax_amt = qty * rate * (1 - discount/100)
            tax_amount = bef_tax_amt * tax_rate / 100
            amount = bef_tax_amt + tax_amount
            total_amount += amount
            source_type = source_types[i] if i < len(source_types) else ""
            source_item_id = source_item_ids[i] if i < len(source_item_ids) else ""
            cursor.execute("""
                INSERT INTO sales_invoice_items (invoice_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount, source_type, source_item_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (invoice_id, products[i], unit_ids[i], hsn_ids[i], qty, rate, discount, tax_rate, round(bef_tax_amt, 2), round(tax_amount, 2), round(amount, 2), source_type, source_item_id))
            if source_type == "order" and source_item_id:
                cursor.execute("UPDATE sales_order_items SET billed_qty = billed_qty + ? WHERE id = ?", (qty, source_item_id))
            elif source_type == "challan" and source_item_id:
                cursor.execute("UPDATE sales_challan_items SET billed_qty = billed_qty + ? WHERE id = ?", (qty, source_item_id))
            if source_type != "challan":
                add_stock_movement(cursor, products[i], "OUT", qty, "sales_invoice", invoice_id, invoice_no, invoice_date, rate)
        cursor.execute("UPDATE sales_invoices SET total_amount=? WHERE id=?", (round(total_amount, 2), invoice_id))
        conn.commit()
        conn.close()
        flash("Sales Invoice created successfully!", "success")
        return redirect(url_for("sales.sales_invoices_list"))
    cursor.execute("SELECT * FROM customers ORDER BY name")
    customers = cursor.fetchall()
    cursor.execute("SELECT * FROM products ORDER BY product_name")
    products = cursor.fetchall()
    cursor.execute("SELECT * FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT * FROM hsn ORDER BY hsn_code")
    hsns = cursor.fetchall()
    cursor.execute("SELECT * FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("sales/add_sales_invoice.html", customers=customers, products=products, units=units, hsns=hsns, transporters=transporters)

@sales.route("/sales-invoices/view/<int:invoice_id>")
def view_sales_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sales_invoices.*,
               customers.name as customer_name,
               transporters.name as transporter_name
        FROM sales_invoices
        LEFT JOIN customers ON sales_invoices.customer_id = customers.id
        LEFT JOIN transporters ON sales_invoices.transporter_id = transporters.id
        WHERE sales_invoices.id = ?
    """, (invoice_id,))
    invoice = cursor.fetchone()
    cursor.execute("""
        SELECT sales_invoice_items.*,
               products.product_name,
               units.unit_name,
               hsn.hsn_code
        FROM sales_invoice_items
        LEFT JOIN products ON sales_invoice_items.product_id = products.id
        LEFT JOIN units ON sales_invoice_items.unit_id = units.id
        LEFT JOIN hsn ON sales_invoice_items.hsn_id = hsn.id
        WHERE sales_invoice_items.invoice_id = ?
    """, (invoice_id,))
    items = cursor.fetchall()
    conn.close()
    return render_template("sales/view_sales_invoice.html", invoice=invoice, items=items)

@sales.route("/sales-invoices/delete/<int:invoice_id>")
def delete_sales_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sales_invoice_items WHERE invoice_id = ?", (invoice_id,))
    cursor.execute("DELETE FROM sales_invoices WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()
    flash("Sales Invoice deleted successfully!", "success")
    return redirect(url_for("sales.sales_invoices_list"))

# ============================================
# SALES QUOTATION
# ============================================
@sales.route("/sales-quotations")
def sales_quotations_list():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sales_quotations.*,
               customers.name as customer_name
        FROM sales_quotations
        LEFT JOIN customers ON sales_quotations.customer_id = customers.id
        ORDER BY sales_quotations.quotation_date DESC
        LIMIT ? OFFSET ?
    """, (per_page, offset))
    quotations = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) FROM sales_quotations")
    total = cursor.fetchone()[0]
    total_pages = (total + per_page - 1) // per_page
    conn.close()
    return render_template("sales/sales_quotations.html", quotations=quotations, page=page, total_pages=total_pages)

@sales.route("/sales-quotations/add", methods=["GET", "POST"])
def add_sales_quotation():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        quotation_date = request.form.get("quotation_date")
        valid_till = request.form.get("valid_till")
        kind_attn = request.form.get("kind_attn")
        remarks = request.form.get("remarks")
        quotation_no = "QT-" + quotation_date.replace("-", "") + "-" + str(int(cursor.execute("SELECT COUNT(*) FROM sales_quotations").fetchone()[0]) + 1).zfill(4)
        cursor.execute("""
            INSERT INTO sales_quotations (quotation_no, quotation_date, customer_id, valid_till, kind_attn, remarks)
            VALUES (?,?,?,?,?,?)
        """, (quotation_no, quotation_date, customer_id, valid_till, kind_attn, remarks))
        quotation_id = cursor.lastrowid
        products = request.form.getlist("product_id[]")
        unit_ids = request.form.getlist("unit_id[]")
        hsn_ids = request.form.getlist("hsn_id[]")
        quantities = request.form.getlist("quantity[]")
        rates = request.form.getlist("rate[]")
        discounts = request.form.getlist("discount[]")
        tax_rates = request.form.getlist("tax_rate[]")
        total_amount = 0
        for i in range(len(products)):
            qty = float(quantities[i])
            rate = float(rates[i])
            discount = float(discounts[i]) if discounts[i] else 0
            tax_rate = float(tax_rates[i]) if tax_rates[i] else 18.0
            bef_tax_amt = qty * rate * (1 - discount/100)
            tax_amount = bef_tax_amt * tax_rate / 100
            amount = bef_tax_amt + tax_amount
            total_amount += amount
            cursor.execute("""
                INSERT INTO sales_quotation_items (quotation_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (quotation_id, products[i], unit_ids[i], hsn_ids[i], qty, rate, discount, tax_rate, round(bef_tax_amt, 2), round(tax_amount, 2), round(amount, 2)))
        cursor.execute("UPDATE sales_quotations SET total_amount=? WHERE id=?", (round(total_amount, 2), quotation_id))
        sync_quotation_opportunity(cursor, quotation_id)
        conn.commit()
        conn.close()
        flash("Sales Quotation created successfully!", "success")
        return redirect(url_for("sales.sales_quotations_list"))
    cursor.execute("SELECT * FROM customers ORDER BY name")
    customers = cursor.fetchall()
    cursor.execute("SELECT * FROM products ORDER BY product_name")
    products = cursor.fetchall()
    cursor.execute("SELECT * FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT * FROM hsn ORDER BY hsn_code")
    hsns = cursor.fetchall()
    conn.close()
    return render_template("sales/add_sales_quotation.html", customers=customers, products=products, units=units, hsns=hsns)

@sales.route("/sales-quotations/view/<int:quotation_id>")
def view_sales_quotation(quotation_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sales_quotations.*,
               customers.name as customer_name
        FROM sales_quotations
        LEFT JOIN customers ON sales_quotations.customer_id = customers.id
        WHERE sales_quotations.id = ?
    """, (quotation_id,))
    quotation = cursor.fetchone()
    cursor.execute("""
        SELECT sales_quotation_items.*,
               products.product_name,
               units.unit_name,
               hsn.hsn_code
        FROM sales_quotation_items
        LEFT JOIN products ON sales_quotation_items.product_id = products.id
        LEFT JOIN units ON sales_quotation_items.unit_id = units.id
        LEFT JOIN hsn ON sales_quotation_items.hsn_id = hsn.id
        WHERE sales_quotation_items.quotation_id = ?
    """, (quotation_id,))
    items = cursor.fetchall()
    conn.close()
    return render_template("sales/view_sales_quotation.html", quotation=quotation, items=items)

@sales.route("/sales-quotations/delete/<int:quotation_id>")
def delete_sales_quotation(quotation_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sales_quotation_items WHERE quotation_id = ?", (quotation_id,))
    cursor.execute("DELETE FROM sales_quotations WHERE id = ?", (quotation_id,))
    conn.commit()
    conn.close()
    flash("Sales Quotation deleted successfully!", "success")
    return redirect(url_for("sales.sales_quotations_list"))

# ============================================
# SALES ORDER
# ============================================
@sales.route("/sales-orders")
def sales_orders_list():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("search", "")
    per_page = 20
    offset = (page - 1) * per_page
    conn = get_connection()
    cursor = conn.cursor()
    search_term = "%" + search + "%"
    cursor.execute("""
        SELECT sales_orders.*,
               customers.name as customer_name,
               (SELECT SUM(quantity) FROM sales_order_items WHERE order_id = sales_orders.id) as total_qty,
               (SELECT SUM(billed_qty) FROM sales_order_items WHERE order_id = sales_orders.id) as total_billed
        FROM sales_orders
        LEFT JOIN customers ON sales_orders.customer_id = customers.id
        WHERE sales_orders.order_no LIKE ?
           OR customers.name LIKE ?
           OR sales_orders.po_no LIKE ?
        ORDER BY sales_orders.order_date DESC
        LIMIT ? OFFSET ?
    """, (search_term, search_term, search_term, per_page, offset))
    orders = cursor.fetchall()
    cursor.execute("""
        SELECT COUNT(*)
        FROM sales_orders
        LEFT JOIN customers ON sales_orders.customer_id = customers.id
        WHERE sales_orders.order_no LIKE ?
           OR customers.name LIKE ?
           OR sales_orders.po_no LIKE ?
    """, (search_term, search_term, search_term))
    total = cursor.fetchone()[0]
    total_pages = (total + per_page - 1) // per_page
    conn.close()
    return render_template("sales/sales_orders.html", orders=orders, page=page, total_pages=total_pages, search=search)

@sales.route("/sales-orders/add", methods=["GET", "POST"])
def add_sales_order():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        order_date = request.form.get("order_date")
        po_no = request.form.get("po_no")
        po_date = request.form.get("po_date")
        kind_attn = request.form.get("kind_attn")
        remarks = request.form.get("remarks")
        order_no = "SO-" + order_date.replace("-", "") + "-" + str(int(cursor.execute("SELECT COUNT(*) FROM sales_orders").fetchone()[0]) + 1).zfill(4)
        cursor.execute("""
            INSERT INTO sales_orders (order_no, order_date, customer_id, po_no, po_date, kind_attn, remarks)
            VALUES (?,?,?,?,?,?,?)
        """, (order_no, order_date, customer_id, po_no, po_date, kind_attn, remarks))
        order_id = cursor.lastrowid
        products = request.form.getlist("product_id[]")
        unit_ids = request.form.getlist("unit_id[]")
        hsn_ids = request.form.getlist("hsn_id[]")
        quantities = request.form.getlist("quantity[]")
        rates = request.form.getlist("rate[]")
        discounts = request.form.getlist("discount[]")
        tax_rates = request.form.getlist("tax_rate[]")
        total_amount = 0
        for i in range(len(products)):
            qty = float(quantities[i])
            rate = float(rates[i])
            discount = float(discounts[i]) if discounts[i] else 0
            tax_rate = float(tax_rates[i]) if tax_rates[i] else 18.0
            bef_tax_amt = qty * rate * (1 - discount/100)
            tax_amount = bef_tax_amt * tax_rate / 100
            amount = bef_tax_amt + tax_amount
            total_amount += amount
            cursor.execute("""
                INSERT INTO sales_order_items (order_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (order_id, products[i], unit_ids[i], hsn_ids[i], qty, rate, discount, tax_rate, round(bef_tax_amt, 2), round(tax_amount, 2), round(amount, 2)))
        cursor.execute("UPDATE sales_orders SET total_amount=? WHERE id=?", (round(total_amount, 2), order_id))
        conn.commit()
        conn.close()
        flash("Sales Order created successfully!", "success")
        return redirect(url_for("sales.sales_orders_list"))
    cursor.execute("SELECT * FROM customers ORDER BY name")
    customers = cursor.fetchall()
    cursor.execute("SELECT * FROM products ORDER BY product_name")
    products = cursor.fetchall()
    cursor.execute("SELECT * FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT * FROM hsn ORDER BY hsn_code")
    hsns = cursor.fetchall()
    conn.close()
    return render_template("sales/add_sales_order.html", customers=customers, products=products, units=units, hsns=hsns)

@sales.route("/sales-orders/view/<int:order_id>")
def view_sales_order(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sales_orders.*,
               customers.name as customer_name
        FROM sales_orders
        LEFT JOIN customers ON sales_orders.customer_id = customers.id
        WHERE sales_orders.id = ?
    """, (order_id,))
    order = cursor.fetchone()
    cursor.execute("""
        SELECT sales_order_items.*,
               products.product_name,
               units.unit_name,
               hsn.hsn_code
        FROM sales_order_items
        LEFT JOIN products ON sales_order_items.product_id = products.id
        LEFT JOIN units ON sales_order_items.unit_id = units.id
        LEFT JOIN hsn ON sales_order_items.hsn_id = hsn.id
        WHERE sales_order_items.order_id = ?
    """, (order_id,))
    items = cursor.fetchall()
    conn.close()
    return render_template("sales/view_sales_order.html", order=order, items=items)

@sales.route("/sales-orders/delete/<int:order_id>")
def delete_sales_order(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sales_order_items WHERE order_id = ?", (order_id,))
    cursor.execute("DELETE FROM sales_orders WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    flash("Sales Order deleted successfully!", "success")
    return redirect(url_for("sales.sales_orders_list"))

# ============================================
# PROFORMA INVOICE
# ============================================
@sales.route("/proforma-invoices")
def proforma_invoices_list():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("search", "")
    per_page = 20
    offset = (page - 1) * per_page
    conn = get_connection()
    cursor = conn.cursor()
    search_term = "%" + search + "%"
    cursor.execute("""
        SELECT proforma_invoices.*,
               customers.name as customer_name
        FROM proforma_invoices
        LEFT JOIN customers ON proforma_invoices.customer_id = customers.id
        WHERE proforma_invoices.proforma_no LIKE ?
           OR customers.name LIKE ?
           OR proforma_invoices.po_no LIKE ?
        ORDER BY proforma_invoices.proforma_date DESC
        LIMIT ? OFFSET ?
    """, (search_term, search_term, search_term, per_page, offset))
    proformas = cursor.fetchall()
    cursor.execute("""
        SELECT COUNT(*) 
        FROM proforma_invoices
        LEFT JOIN customers ON proforma_invoices.customer_id = customers.id
        WHERE proforma_invoices.proforma_no LIKE ?
           OR customers.name LIKE ?
           OR proforma_invoices.po_no LIKE ?
    """, (search_term, search_term, search_term))
    total = cursor.fetchone()[0]
    total_pages = (total + per_page - 1) // per_page
    conn.close()
    return render_template("sales/proforma_invoices.html", proformas=proformas, page=page, total_pages=total_pages, search=search)

@sales.route("/proforma-invoices/add", methods=["GET", "POST"])
def add_proforma_invoice():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        transporter_id = request.form.get("transporter_id")
        proforma_date = request.form.get("proforma_date")
        po_no = request.form.get("po_no")
        po_date = request.form.get("po_date")
        kind_attn = request.form.get("kind_attn")
        remarks = request.form.get("remarks")
        proforma_no = "PF-" + proforma_date.replace("-", "") + "-" + str(int(cursor.execute("SELECT COUNT(*) FROM proforma_invoices").fetchone()[0]) + 1).zfill(4)
        cursor.execute("""
            INSERT INTO proforma_invoices (proforma_no, proforma_date, customer_id, transporter_id, po_no, po_date, kind_attn, remarks)
            VALUES (?,?,?,?,?,?,?,?)
        """, (proforma_no, proforma_date, customer_id, transporter_id, po_no, po_date, kind_attn, remarks))
        proforma_id = cursor.lastrowid
        products = request.form.getlist("product_id[]")
        unit_ids = request.form.getlist("unit_id[]")
        hsn_ids = request.form.getlist("hsn_id[]")
        quantities = request.form.getlist("quantity[]")
        rates = request.form.getlist("rate[]")
        discounts = request.form.getlist("discount[]")
        tax_rates = request.form.getlist("tax_rate[]")
        total_amount = 0
        for i in range(len(products)):
            qty = float(quantities[i])
            rate = float(rates[i])
            discount = float(discounts[i]) if discounts[i] else 0
            tax_rate = float(tax_rates[i]) if tax_rates[i] else 18.0
            bef_tax_amt = qty * rate * (1 - discount/100)
            tax_amount = bef_tax_amt * tax_rate / 100
            amount = bef_tax_amt + tax_amount
            total_amount += amount
            cursor.execute("""
                INSERT INTO proforma_invoice_items (proforma_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (proforma_id, products[i], unit_ids[i], hsn_ids[i], qty, rate, discount, tax_rate, round(bef_tax_amt, 2), round(tax_amount, 2), round(amount, 2)))
        cursor.execute("UPDATE proforma_invoices SET total_amount=? WHERE id=?", (round(total_amount, 2), proforma_id))
        conn.commit()
        conn.close()
        flash("Proforma Invoice created successfully!", "success")
        return redirect(url_for("sales.proforma_invoices_list"))
    cursor.execute("SELECT * FROM customers ORDER BY name")
    customers = cursor.fetchall()
    cursor.execute("SELECT * FROM products ORDER BY product_name")
    products = cursor.fetchall()
    cursor.execute("SELECT * FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT * FROM hsn ORDER BY hsn_code")
    hsns = cursor.fetchall()
    cursor.execute("SELECT * FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("sales/add_proforma_invoice.html", customers=customers, products=products, units=units, hsns=hsns, transporters=transporters)

@sales.route("/proforma-invoices/view/<int:proforma_id>")
def view_proforma_invoice(proforma_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT proforma_invoices.*,
               customers.name as customer_name,
               transporters.name as transporter_name
        FROM proforma_invoices
        LEFT JOIN customers ON proforma_invoices.customer_id = customers.id
        LEFT JOIN transporters ON proforma_invoices.transporter_id = transporters.id
        WHERE proforma_invoices.id = ?
    """, (proforma_id,))
    proforma = cursor.fetchone()
    cursor.execute("""
        SELECT proforma_invoice_items.*,
               products.product_name,
               units.unit_name,
               hsn.hsn_code
        FROM proforma_invoice_items
        LEFT JOIN products ON proforma_invoice_items.product_id = products.id
        LEFT JOIN units ON proforma_invoice_items.unit_id = units.id
        LEFT JOIN hsn ON proforma_invoice_items.hsn_id = hsn.id
        WHERE proforma_invoice_items.proforma_id = ?
    """, (proforma_id,))
    items = cursor.fetchall()
    conn.close()
    return render_template("sales/view_proforma_invoice.html", proforma=proforma, items=items)

@sales.route("/proforma-invoices/delete/<int:proforma_id>")
def delete_proforma_invoice(proforma_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM proforma_invoice_items WHERE proforma_id = ?", (proforma_id,))
    cursor.execute("DELETE FROM proforma_invoices WHERE id = ?", (proforma_id,))
    conn.commit()
    conn.close()
    flash("Proforma Invoice deleted successfully!", "success")
    return redirect(url_for("sales.proforma_invoices_list"))

# ============================================
# SALES CHALLAN
# ============================================
@sales.route("/sales-challans")
def sales_challans_list():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("search", "")
    per_page = 20
    offset = (page - 1) * per_page
    conn = get_connection()
    cursor = conn.cursor()
    search_term = "%" + search + "%"
    cursor.execute("""
        SELECT sales_challans.*,
               customers.name as customer_name,
               (SELECT SUM(quantity) FROM sales_challan_items WHERE challan_id = sales_challans.id) as total_qty,
               (SELECT SUM(billed_qty) FROM sales_challan_items WHERE challan_id = sales_challans.id) as total_billed
        FROM sales_challans
        LEFT JOIN customers ON sales_challans.customer_id = customers.id
        WHERE sales_challans.challan_no LIKE ?
           OR customers.name LIKE ?
           OR sales_challans.po_no LIKE ?
        ORDER BY sales_challans.challan_date DESC
        LIMIT ? OFFSET ?
    """, (search_term, search_term, search_term, per_page, offset))
    challans = cursor.fetchall()
    cursor.execute("""
        SELECT COUNT(*) 
        FROM sales_challans
        LEFT JOIN customers ON sales_challans.customer_id = customers.id
        WHERE sales_challans.challan_no LIKE ?
           OR customers.name LIKE ?
           OR sales_challans.po_no LIKE ?
    """, (search_term, search_term, search_term))
    total = cursor.fetchone()[0]
    total_pages = (total + per_page - 1) // per_page
    conn.close()
    return render_template("sales/sales_challans.html", challans=challans, page=page, total_pages=total_pages, search=search)

@sales.route("/sales-challans/add", methods=["GET", "POST"])
def add_sales_challan():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        transporter_id = request.form.get("transporter_id")
        challan_date = request.form.get("challan_date")
        po_no = request.form.get("po_no")
        po_date = request.form.get("po_date")
        kind_attn = request.form.get("kind_attn")
        remarks = request.form.get("remarks")
        challan_no = "CH-" + challan_date.replace("-", "") + "-" + str(int(cursor.execute("SELECT COUNT(*) FROM sales_challans").fetchone()[0]) + 1).zfill(4)
        cursor.execute("""
            INSERT INTO sales_challans (challan_no, challan_date, customer_id, transporter_id, po_no, po_date, kind_attn, remarks)
            VALUES (?,?,?,?,?,?,?,?)
        """, (challan_no, challan_date, customer_id, transporter_id, po_no, po_date, kind_attn, remarks))
        challan_id = cursor.lastrowid
        products = request.form.getlist("product_id[]")
        unit_ids = request.form.getlist("unit_id[]")
        hsn_ids = request.form.getlist("hsn_id[]")
        quantities = request.form.getlist("quantity[]")
        rates = request.form.getlist("rate[]")
        discounts = request.form.getlist("discount[]")
        tax_rates = request.form.getlist("tax_rate[]")
        total_amount = 0
        for i in range(len(products)):
            qty = float(quantities[i])
            rate = float(rates[i])
            discount = float(discounts[i]) if discounts[i] else 0
            tax_rate = float(tax_rates[i]) if tax_rates[i] else 18.0
            bef_tax_amt = qty * rate * (1 - discount/100)
            tax_amount = bef_tax_amt * tax_rate / 100
            amount = bef_tax_amt + tax_amount
            total_amount += amount
            cursor.execute("""
                INSERT INTO sales_challan_items (challan_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (challan_id, products[i], unit_ids[i], hsn_ids[i], qty, rate, discount, tax_rate, round(bef_tax_amt, 2), round(tax_amount, 2), round(amount, 2)))
            add_stock_movement(cursor, products[i], "OUT", qty, "challan", challan_id, challan_no, challan_date, rate)
        cursor.execute("UPDATE sales_challans SET total_amount=? WHERE id=?", (round(total_amount, 2), challan_id))
        conn.commit()
        conn.close()
        flash("Sales Challan created successfully!", "success")
        return redirect(url_for("sales.sales_challans_list"))
    cursor.execute("SELECT * FROM customers ORDER BY name")
    customers = cursor.fetchall()
    cursor.execute("SELECT * FROM products ORDER BY product_name")
    products = cursor.fetchall()
    cursor.execute("SELECT * FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT * FROM hsn ORDER BY hsn_code")
    hsns = cursor.fetchall()
    cursor.execute("SELECT * FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("sales/add_sales_challan.html", customers=customers, products=products, units=units, hsns=hsns, transporters=transporters)

@sales.route("/sales-challans/view/<int:challan_id>")
def view_sales_challan(challan_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sales_challans.*,
               customers.name as customer_name,
               transporters.name as transporter_name
        FROM sales_challans
        LEFT JOIN customers ON sales_challans.customer_id = customers.id
        LEFT JOIN transporters ON sales_challans.transporter_id = transporters.id
        WHERE sales_challans.id = ?
    """, (challan_id,))
    challan = cursor.fetchone()
    cursor.execute("""
        SELECT sales_challan_items.*,
               products.product_name,
               units.unit_name,
               hsn.hsn_code
        FROM sales_challan_items
        LEFT JOIN products ON sales_challan_items.product_id = products.id
        LEFT JOIN units ON sales_challan_items.unit_id = units.id
        LEFT JOIN hsn ON sales_challan_items.hsn_id = hsn.id
        WHERE sales_challan_items.challan_id = ?
    """, (challan_id,))
    items = cursor.fetchall()
    conn.close()
    return render_template("sales/view_sales_challan.html", challan=challan, items=items)

@sales.route("/sales-challans/delete/<int:challan_id>")
def delete_sales_challan(challan_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sales_challan_items WHERE challan_id = ?", (challan_id,))
    cursor.execute("DELETE FROM sales_challans WHERE id = ?", (challan_id,))
    conn.commit()
    conn.close()
    flash("Sales Challan deleted successfully!", "success")
    return redirect(url_for("sales.sales_challans_list"))

# ============================================
# SALES RETURN
# ============================================
@sales.route("/sales-returns")
def sales_returns_list():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("search", "")
    per_page = 20
    offset = (page - 1) * per_page
    conn = get_connection()
    cursor = conn.cursor()
    search_term = "%" + search + "%"
    cursor.execute("""
        SELECT sales_returns.*,
               customers.name as customer_name
        FROM sales_returns
        LEFT JOIN customers ON sales_returns.customer_id = customers.id
        WHERE sales_returns.return_no LIKE ?
           OR customers.name LIKE ?
        ORDER BY sales_returns.return_date DESC
        LIMIT ? OFFSET ?
    """, (search_term, search_term, per_page, offset))
    returns = cursor.fetchall()
    cursor.execute("""
        SELECT COUNT(*) 
        FROM sales_returns
        LEFT JOIN customers ON sales_returns.customer_id = customers.id
        WHERE sales_returns.return_no LIKE ?
           OR customers.name LIKE ?
    """, (search_term, search_term))
    total = cursor.fetchone()[0]
    total_pages = (total + per_page - 1) // per_page
    conn.close()
    return render_template("sales/sales_returns.html", returns=returns, page=page, total_pages=total_pages, search=search)

@sales.route("/sales-returns/add", methods=["GET", "POST"])
def add_sales_return():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        transporter_id = request.form.get("transporter_id")
        invoice_id = request.form.get("invoice_id")
        return_date = request.form.get("return_date")
        reason = request.form.get("reason")
        remarks = request.form.get("remarks")
        return_no = "SR-" + return_date.replace("-", "") + "-" + str(int(cursor.execute("SELECT COUNT(*) FROM sales_returns").fetchone()[0]) + 1).zfill(4)
        cursor.execute("""
            INSERT INTO sales_returns (return_no, return_date, customer_id, transporter_id, invoice_id, reason, remarks)
            VALUES (?,?,?,?,?,?,?)
        """, (return_no, return_date, customer_id, transporter_id, invoice_id,  reason, remarks))
        return_id = cursor.lastrowid
        products = request.form.getlist("product_id[]")
        unit_ids = request.form.getlist("unit_id[]")
        hsn_ids = request.form.getlist("hsn_id[]")
        quantities = request.form.getlist("quantity[]")
        rates = request.form.getlist("rate[]")
        discounts = request.form.getlist("discount[]")
        tax_rates = request.form.getlist("tax_rate[]")
        total_amount = 0
        for i in range(len(products)):
            qty = float(quantities[i])
            rate = float(rates[i])
            discount = float(discounts[i]) if discounts[i] else 0
            tax_rate = float(tax_rates[i]) if tax_rates[i] else 18.0
            bef_tax_amt = qty * rate * (1 - discount/100)
            tax_amount = bef_tax_amt * tax_rate / 100
            amount = bef_tax_amt + tax_amount
            total_amount += amount
            cursor.execute("""
                INSERT INTO sales_return_items (return_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (return_id, products[i], unit_ids[i], hsn_ids[i], qty, rate, discount, tax_rate, round(bef_tax_amt, 2), round(tax_amount, 2), round(amount, 2)))
            add_stock_movement(cursor, products[i], "IN", qty, "sales_return", return_id, return_no, return_date, rate)
        cursor.execute("UPDATE sales_returns SET total_amount=? WHERE id=?", (round(total_amount, 2), return_id))
        conn.commit()
        conn.close()
        flash("Sales Return created successfully!", "success")
        return redirect(url_for("sales.sales_returns_list"))
    cursor.execute("SELECT * FROM customers ORDER BY name")
    customers = cursor.fetchall()
    cursor.execute("SELECT * FROM products ORDER BY product_name")
    products = cursor.fetchall()
    cursor.execute("SELECT * FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT * FROM hsn ORDER BY hsn_code")
    hsns = cursor.fetchall()
    cursor.execute("SELECT * FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    cursor.execute("""
        SELECT sales_invoices.id, sales_invoices.invoice_no, customers.name as customer_name
        FROM sales_invoices
        LEFT JOIN customers ON sales_invoices.customer_id= customers.id
        ORDER BY sales_invoices.invoice_date DESC
    """)
    invoices = cursor.fetchall()
    conn.close()
    return render_template("sales/add_sales_return.html", customers=customers, products=products, units=units, hsns=hsns, transporters=transporters, invoices=invoices)

@sales.route("/sales-returns/view/<int:return_id>")
def view_sales_return(return_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sales_returns.*,
               customers.name as customer_name,
               transporters.name as transporter_name,
               sales_invoices.invoice_no as invoice_no
        FROM sales_returns
        LEFT JOIN customers ON sales_returns.customer_id = customers.id
        LEFT JOIN transporters ON sales_returns.transporter_id = transporters.id
        LEFT JOIN sales_invoices ON sales_returns.invoice_id = sales_invoices.id
        WHERE sales_returns.id = ?
    """, (return_id,))
    ret = cursor.fetchone()
    cursor.execute("""
        SELECT sales_return_items.*,
               products.product_name,
               units.unit_name,
               hsn.hsn_code
        FROM sales_return_items
        LEFT JOIN products ON sales_return_items.product_id = products.id
        LEFT JOIN units ON sales_return_items.unit_id = units.id
        LEFT JOIN hsn ON sales_return_items.hsn_id = hsn.id
        WHERE sales_return_items.return_id = ?
    """, (return_id,))
    items = cursor.fetchall()
    conn.close()
    return render_template("sales/view_sales_return.html", ret=ret, items=items)

@sales.route("/sales-returns/delete/<int:return_id>")
def delete_sales_return(return_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sales_return_items WHERE return_id = ?", (return_id,))
    cursor.execute("DELETE FROM sales_returns WHERE id = ?", (return_id,))
    conn.commit()
    conn.close()
    flash("Sales Return deleted successfully!", "success")
    return redirect(url_for("sales.sales_returns_list"))

@sales.route("/api/pending-items/<int:customer_id>/<int:product_id>")
def api_pending_items(customer_id, product_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sales_order_items.id as item_id,
               sales_order_items.quantity,
               sales_order_items.billed_qty,
               sales_order_items.rate,
               sales_order_items.discount,
               sales_order_items.tax_rate,
               sales_order_items.unit_id,
               sales_order_items.hsn_id,
               sales_orders.order_no as doc_no
        FROM sales_order_items
        LEFT JOIN sales_orders ON sales_order_items.order_id = sales_orders.id
        WHERE sales_orders.customer_id = ?
          AND sales_order_items.product_id = ?
          AND sales_order_items.quantity > sales_order_items.billed_qty
    """, (customer_id, product_id))
    order_items = cursor.fetchall()
    cursor.execute("""
        SELECT sales_challan_items.id as item_id,
               sales_challan_items.quantity,
               sales_challan_items.billed_qty,
               sales_challan_items.rate,
               sales_challan_items.discount,
               sales_challan_items.tax_rate,
               sales_challan_items.unit_id,
               sales_challan_items.hsn_id,
               sales_challans.challan_no as doc_no
        FROM sales_challan_items
        LEFT JOIN sales_challans ON sales_challan_items.challan_id = sales_challans.id
        WHERE sales_challans.customer_id = ?
          AND sales_challan_items.product_id = ?
          AND sales_challan_items.quantity > sales_challan_items.billed_qty
    """, (customer_id, product_id))
    challan_items = cursor.fetchall()
    conn.close()
    result=[]
    for item in order_items:
        result.append({
            "source_type": "order",
            "item_id": item["item_id"],
            "doc_no": item["doc_no"],
            "pending_qty": item["quantity"] - item["billed_qty"],
            "rate": item["rate"],
            "discount": item["discount"],
            "tax_rate": item["tax_rate"],
            "unit_id": item["unit_id"],
            "hsn_id": item["hsn_id"]
        })
    for item in challan_items:
        result.append({
            "source_type": "challan",
            "item_id": item["item_id"],
            "doc_no": item["doc_no"],
            "pending_qty": item["quantity"] - item["billed_qty"],
            "rate": item["rate"],
            "discount": item["discount"],
            "tax_rate": item["tax_rate"],
            "unit_id": item["unit_id"],
            "hsn_id": item["hsn_id"]
        })
    return {"pending": result}