from flask import Blueprint
from flask import render_template, request, redirect, url_for, flash
from database import get_connection as _get_connection
from modules.document_database import (
    install_document_connection_cleanup,
    managed_document_connection,
    next_document_number,
)
from modules.stock import add_stock_movement
from modules.auth import require_system

purchases = Blueprint("purchases", __name__)
get_connection = managed_document_connection(_get_connection)
install_document_connection_cleanup(purchases)
purchases.before_request(require_system("distribution"))


# ============================================
# PURCHASE ORDER
# ============================================
@purchases.route("/purchase-orders")
def purchase_orders_list():
    page = request.args.get("page",1, type=int)
    search = request.args.get("search", "")
    status_filter = request.args.get("status", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")
    sort_by = request.args.get("sort", "date_desc")
    per_page= 20
    offset= (page-1) * per_page
    conn = get_connection()
    cursor= conn.cursor()
    search_term = "%" + search + "%"
    where = " WHERE (purchase_orders.order_no LIKE ? OR suppliers.name LIKE ?)"
    params = [search_term, search_term]
    if status_filter:
        where += " AND purchase_orders.status = ?"
        params.append(status_filter)
    if from_date:
        where += " AND purchase_orders.order_date >= ?"
        params.append(from_date)
    if to_date:
        where += " AND purchase_orders.order_date <= ?"
        params.append(to_date)
    sort_options = {
        "date_desc": "purchase_orders.order_date DESC",
        "date_asc": "purchase_orders.order_date ASC",
        "amount_desc": "purchase_orders.total_amount DESC",
        "amount_asc": "purchase_orders.total_amount ASC"
    }
    order_by = sort_options.get(sort_by, "purchase_orders.order_date DESC")
    cursor.execute("""
        SELECT purchase_orders.*,
        suppliers.name as supplier_name
        FROM purchase_orders
        LEFT JOIN suppliers ON purchase_orders.supplier_id = suppliers.id
    """ + where + " ORDER BY " + order_by + " LIMIT ? OFFSET ?", params + [per_page, offset])
    orders = cursor.fetchall()
    cursor.execute("""
        SELECT COUNT(*)
        FROM purchase_orders
        LEFT JOIN suppliers ON purchase_orders.supplier_id = suppliers.id
    """ + where, params)
    total = cursor.fetchone()[0]
    total_pages = (total + per_page - 1) // per_page
    conn.close()
    return render_template("purchases/purchase_orders.html", orders=orders, page=page, total_pages=total_pages, search=search, status_filter=status_filter, from_date=from_date, to_date=to_date, sort_by=sort_by)

@purchases.route("/purchase-orders/add", methods=["GET", "POST"])
def add_purchase_order():
    conn= get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        supplier_id = request.form.get("supplier_id")
        transporter_id = request.form.get("transporter_id")
        order_date = request.form.get("order_date")
        kind_attn = request.form.get("kind_attn")
        sup_ref_no = request.form.get("sup_ref_no")
        cr_days = request.form.get("cr_days") or 0
        valid_till = request.form.get("valid_till")
        remarks = request.form.get("remarks")
        prefix = "PO-" + order_date.replace("-", "") + "-"
        order_no = next_document_number(
            cursor, "purchase_order", order_date.replace("-", ""), prefix, prefix
        )
        cursor.execute ("""
            INSERT INTO purchase_orders (order_no, supplier_id, transporter_id, order_date, kind_attn, sup_ref_no, cr_days, valid_till, remarks)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (order_no, supplier_id, transporter_id, order_date, kind_attn, sup_ref_no, cr_days, valid_till, remarks))
        order_id = cursor.lastrowid
        products = request.form.getlist("product_id[]")
        quantities = request.form.getlist("quantity[]")
        rates = request.form.getlist("rate[]")
        discounts = request.form.getlist("discount[]")
        tax_rates = request.form.getlist("tax_rate[]")
        unit_ids = request.form.getlist("unit_id[]")
        hsn_ids = request.form.getlist("hsn_id[]")
        total_amount = 0
        for i in range(len(products)):
            qty = float(quantities[i])
            rate = float(rates[i])
            discount = float(discounts[i] if discounts[i] else 0)
            tax_rate = float(tax_rates[i] if tax_rates[i] else 18.0)
            bef_tax_amt = qty * rate * (1- discount/100)
            tax_amount = bef_tax_amt * tax_rate/100
            amount = bef_tax_amt + tax_amount   
            total_amount += amount
            cursor.execute("""
                INSERT INTO purchase_order_items (order_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (order_id, products[i], unit_ids[i], hsn_ids[i], qty, rate, discount,tax_rate, round(bef_tax_amt, 2), round(tax_amount, 2), round(amount, 2)))
        cursor.execute("UPDATE purchase_orders SET total_amount=? WHERE id=?", (round(total_amount, 2), order_id))
        conn.commit()
        conn.close()
        flash("Purchase order created successfully!", "success")
        return redirect(url_for("purchases.purchase_orders_list"))
    cursor.execute("SELECT * FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    cursor.execute("SELECT * FROM products ORDER BY product_name")
    products = cursor.fetchall()
    cursor.execute("SELECT * FROM units ORDER BY unit_name")
    units= cursor.fetchall()
    cursor.execute("SELECT * FROM hsn ORDER BY hsn_code")
    hsns = cursor.fetchall()
    cursor.execute("SELECT * FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("purchases/add_purchase_order.html", suppliers=suppliers, products=products, units=units, hsns=hsns, transporters=transporters)

@purchases.route("/purchase-orders/view/<int:order_id>")
def view_purchase_order(order_id):
    conn= get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT purchase_orders.*, 
               suppliers.name as supplier_name,
               transporters.name as transporter_name
        FROM purchase_orders
        LEFT JOIN suppliers ON purchase_orders.supplier_id = suppliers.id
        LEFT JOIN transporters ON purchase_orders.transporter_id = transporters.id
        WHERE purchase_orders.id = ?
    """, (order_id,))
    order = cursor.fetchone()
    cursor.execute("""
    SELECT purchase_order_items.*,
           products.product_name,
           units.unit_name,
           hsn.hsn_code
    FROM purchase_order_items
    LEFT JOIN products ON purchase_order_items.product_id = products.id
    LEFT JOIN units ON purchase_order_items.unit_id = units.id
    LEFT JOIN hsn ON purchase_order_items.hsn_id = hsn.id
        WHERE purchase_order_items.order_id = ?
    """, (order_id,))
    items = cursor.fetchall()
    conn.close()
    return render_template("purchases/view_purchase_order.html", order=order, items=items)

@purchases.route("/purchase-orders/delete/<int:order_id>")
def delete_purchase_order(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM purchase_order_items WHERE order_id = ?", (order_id,))
    cursor.execute("DELETE FROM purchase_orders WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    flash("Purchase order Deleted successfully", "success")
    return redirect(url_for("purchases.purchase_orders_list"))

# ============================================
# PURCHASE INVOICE
# ============================================

@purchases.route("/purchase-invoices")
def purchase_invoices_list():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    offset = (page-1) * per_page
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT purchase_invoices.*,
        suppliers.name as supplier_name
        FROM purchase_invoices
        LEFT JOIN suppliers ON purchase_invoices.supplier_id = suppliers.id
        ORDER BY purchase_invoices.invoice_date DESC
        LIMIT ? OFFSET ?
    """, (per_page, offset))
    invoices = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) FROM purchase_invoices")
    total = cursor.fetchone()[0]
    total_pages = (total + per_page - 1) // per_page
    conn.close()
    return render_template("purchases/purchase_invoices.html", invoices=invoices, page=page, total_pages=total_pages)

@purchases.route("/purchase-invoices/add", methods=["GET", "POST"])
def add_purchase_invoice():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        supplier_id = request.form.get("supplier_id")
        transporter_id = request.form.get("transporter_id")
        invoice_date = request.form.get("invoice_date")
        sup_invoice_no = request.form.get("sup_invoice_no")
        sup_invoice_date = request.form.get("sup_invoice_date")
        cr_days = request.form.get("cr_days") or 0
        due_date = request.form.get("due_date")
        remarks = request.form.get("remarks")
        invoice_prefix = "PI-" + invoice_date.replace("-", "") + "-"
        invoice_no = next_document_number(
            cursor, "purchase_invoice", "global", invoice_prefix, "PI-"
        )
        cursor.execute("""
            INSERT INTO purchase_invoices (invoice_no, invoice_date, supplier_id, transporter_id, sup_invoice_no, sup_invoice_date, cr_days, due_date, remarks)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (invoice_no, invoice_date, supplier_id, transporter_id, sup_invoice_no, sup_invoice_date, cr_days, due_date, remarks))
        invoice_id = cursor.lastrowid
        products = request.form.getlist("product_id[]")
        unit_ids = request.form.getlist("unit_id[]")
        hsn_ids = request.form.getlist("hsn_id[]")
        quantities = request.form.getlist("quantity[]")
        rates = request.form.getlist("rate[]")
        discounts = request.form.getlist("discount[]")
        tax_rates = request.form.getlist("tax_rate[]")
        total_amount = 0
        for i in range (len(products)):
            qty=float(quantities[i])
            rate = float(rates[i])
            discount = float(discounts[i]) if discounts[i] else 0 
            tax_rate = float(tax_rates[i]) if tax_rates[i] else 18.0
            bef_tax_amt = qty * rate * (1- discount/100)
            tax_amount = bef_tax_amt * tax_rate / 100
            amount = bef_tax_amt + tax_amount
            total_amount += amount
            cursor.execute("""
                INSERT INTO purchase_invoice_items (invoice_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (invoice_id, products[i], unit_ids[i], hsn_ids[i], qty, rate, discount, tax_rate, round(bef_tax_amt, 2), round(tax_amount, 2), round(amount, 2)))
        cursor.execute("UPDATE purchase_invoices SET total_amount=? WHERE id=?", (round(total_amount, 2), invoice_id))
        conn.commit()
        conn.close()
        flash("Purchase Invoice created successfully!", "success")
        return redirect(url_for("purchases.purchase_invoices_list"))
    cursor.execute("SELECT * FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    cursor.execute("SELECT * FROM products ORDER BY product_name")
    products = cursor.fetchall()
    cursor.execute("SELECT * FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT * FROM hsn ORDER BY hsn_code")
    hsns = cursor.fetchall()
    cursor.execute("SELECT * FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("purchases/add_purchase_invoice.html", suppliers=suppliers, products=products, units=units, hsns=hsns, transporters=transporters)        

@purchases.route("/purchase-invoices/view/<int:invoice_id>")
def view_purchase_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT purchase_invoices.*,
               suppliers.name as supplier_name,
               transporters.name as transporter_name
        FROM purchase_invoices
        LEFT JOIN suppliers ON purchase_invoices.supplier_id = suppliers.id
        LEFT JOIN transporters ON purchase_invoices.transporter_id = transporters.id
        WHERE purchase_invoices.id = ?
    """, (invoice_id,))
    invoice = cursor.fetchone()
    cursor.execute("""
        SELECT purchase_invoice_items.*,
               products.product_name,
               units.unit_name,
               hsn.hsn_code
        FROM purchase_invoice_items
        LEFT JOIN products ON purchase_invoice_items.product_id = products.id
        LEFT JOIN units ON purchase_invoice_items.unit_id = units.id
        LEFT JOIN hsn ON purchase_invoice_items.hsn_id = hsn.id
        WHERE purchase_invoice_items.invoice_id = ?
    """, (invoice_id,))
    items = cursor.fetchall()
    conn.close()
    return render_template("purchases/view_purchase_invoice.html", invoice=invoice, items=items)

@purchases.route("/purchase-invoices/delete/<int:invoice_id>")
def delete_purchase_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM purchase_invoice_items WHERE invoice_id = ?", (invoice_id,))
    cursor.execute("DELETE FROM purchase_invoices WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()
    flash("Purchase Invoice deleted successfully!", "success")
    return redirect(url_for("purchases.purchase_invoices_list"))

# ============================================
# PURCHASE GRN
# ============================================
@purchases.route("/purchase-grn")
def purchase_grn_list():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT purchase_grn.*,
               suppliers.name as supplier_name
        FROM purchase_grn
        LEFT JOIN suppliers ON purchase_grn.supplier_id = suppliers.id
        ORDER BY purchase_grn.grn_date DESC
        LIMIT ? OFFSET ?
    """, (per_page, offset))
    grns = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) FROM purchase_grn")
    total = cursor.fetchone()[0]
    total_pages = (total + per_page - 1) // per_page
    conn.close()
    return render_template("purchases/purchase_grn.html", grns=grns, page=page, total_pages=total_pages)

@purchases.route("/purchase-grn/add", methods=["GET", "POST"])
def add_purchase_grn():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        supplier_id = request.form.get("supplier_id")
        transporter_id = request.form.get("transporter_id")
        grn_date = request.form.get("grn_date")
        remarks = request.form.get("remarks")
        grn_prefix = "GRN-" + grn_date.replace("-", "") + "-"
        grn_no = next_document_number(
            cursor, "purchase_grn", "global", grn_prefix, "GRN-"
        )
        cursor.execute("""
            INSERT INTO purchase_grn (grn_no, grn_date, supplier_id, transporter_id, remarks)
            VALUES (?,?,?,?,?)
        """, (grn_no, grn_date, supplier_id, transporter_id, remarks))
        grn_id = cursor.lastrowid
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
                INSERT INTO purchase_grn_items (grn_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (grn_id, products[i], unit_ids[i], hsn_ids[i], qty, rate, discount, tax_rate, round(bef_tax_amt, 2), round(tax_amount, 2), round(amount, 2)))
            add_stock_movement(cursor, products[i], "IN", qty, "grn", grn_id, grn_no, grn_date, rate)
        cursor.execute("UPDATE purchase_grn SET total_amount=? WHERE id=?", (round(total_amount, 2), grn_id))
        conn.commit()
        conn.close()
        flash("Purchase GRN created successfully!", "success")
        return redirect(url_for("purchases.purchase_grn_list"))
    cursor.execute("SELECT * FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    cursor.execute("SELECT * FROM products ORDER BY product_name")
    products = cursor.fetchall()
    cursor.execute("SELECT * FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT * FROM hsn ORDER BY hsn_code")
    hsns = cursor.fetchall()
    cursor.execute("SELECT * FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("purchases/add_purchase_grn.html", suppliers=suppliers, products=products, units=units, hsns=hsns, transporters=transporters)

@purchases.route("/purchase-grn/view/<int:grn_id>")
def view_purchase_grn(grn_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT purchase_grn.*, 
               suppliers.name as supplier_name,
               transporters.name as transporter_name
        FROM purchase_grn
        LEFT JOIN suppliers ON purchase_grn.supplier_id = suppliers.id
        LEFT JOIN transporters ON purchase_grn.transporter_id = transporters.id
        WHERE purchase_grn.id = ?
    """, (grn_id,))
    grn = cursor.fetchone()
    cursor.execute("""
        SELECT purchase_grn_items.*,
               products.product_name,
               units.unit_name,
               hsn.hsn_code
        FROM purchase_grn_items
        LEFT JOIN products ON purchase_grn_items.product_id = products.id
        LEFT JOIN units ON purchase_grn_items.unit_id = units.id
        LEFT JOIN hsn ON purchase_grn_items.hsn_id = hsn.id
        WHERE purchase_grn_items.grn_id = ?
    """, (grn_id,))
    items = cursor.fetchall()
    conn.close()
    return render_template("purchases/view_purchase_grn.html", grn=grn, items=items)

@purchases.route("/purchase-grn/delete/<int:grn_id>")
def delete_purchase_grn(grn_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM purchase_grn_items WHERE grn_id = ?", (grn_id,))
    cursor.execute("DELETE FROM purchase_grn WHERE id = ?", (grn_id,))
    conn.commit()
    conn.close()
    flash("Purchase GRN deleted successfully!", "success")
    return redirect(url_for("purchases.purchase_grn_list"))

# ============================================
# PURCHASE RETURN
# ============================================
@purchases.route("/purchase-returns")
def purchase_returns_list():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT purchase_returns.*,
               suppliers.name as supplier_name
        FROM purchase_returns
        LEFT JOIN suppliers ON purchase_returns.supplier_id = suppliers.id
        ORDER BY purchase_returns.return_date DESC
        LIMIT ? OFFSET ?
    """, (per_page, offset))
    returns = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) FROM purchase_returns")
    total = cursor.fetchone()[0]
    total_pages = (total + per_page - 1) // per_page
    conn.close()
    return render_template("purchases/purchase_returns.html", returns=returns, page=page, total_pages=total_pages)

@purchases.route("/purchase-returns/add", methods=["GET", "POST"])
def add_purchase_return():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        supplier_id = request.form.get("supplier_id")
        transporter_id = request.form.get("transporter_id")
        return_date = request.form.get("return_date")
        reason = request.form.get("reason")
        remarks = request.form.get("remarks")
        return_prefix = "PR-" + return_date.replace("-", "") + "-"
        return_no = next_document_number(
            cursor, "purchase_return", "global", return_prefix, "PR-"
        )
        cursor.execute("""
            INSERT INTO purchase_returns (return_no, return_date, supplier_id, transporter_id, reason, remarks)
            VALUES (?,?,?,?,?,?)
        """, (return_no, return_date, supplier_id, transporter_id, reason, remarks))
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
                INSERT INTO purchase_return_items (return_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (return_id, products[i], unit_ids[i], hsn_ids[i], qty, rate, discount, tax_rate, round(bef_tax_amt, 2), round(tax_amount, 2), round(amount, 2)))
            add_stock_movement(cursor, products[i], "OUT", qty, "purchase_return", return_id, return_no, return_date, rate)
        cursor.execute("UPDATE purchase_returns SET total_amount=? WHERE id=?", (round(total_amount, 2), return_id))
        conn.commit()
        conn.close()
        flash("Purchase Return created successfully!", "success")
        return redirect(url_for("purchases.purchase_returns_list"))
    cursor.execute("SELECT * FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    cursor.execute("SELECT * FROM products ORDER BY product_name")
    products = cursor.fetchall()
    cursor.execute("SELECT * FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT * FROM hsn ORDER BY hsn_code")
    hsns = cursor.fetchall()
    cursor.execute("SELECT * FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("purchases/add_purchase_return.html", suppliers=suppliers, products=products, units=units, hsns=hsns, transporters=transporters)

@purchases.route("/purchase-returns/view/<int:return_id>")
def view_purchase_return(return_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT purchase_returns.*, 
               suppliers.name as supplier_name,
               transporters.name as transporter_name
        FROM purchase_returns
        LEFT JOIN suppliers ON purchase_returns.supplier_id = suppliers.id
        LEFT JOIN transporters ON purchase_returns.transporter_id = transporters.id
        WHERE purchase_returns.id = ?
    """, (return_id,))
    ret = cursor.fetchone()
    cursor.execute("""
        SELECT purchase_return_items.*,
               products.product_name,
               units.unit_name,
               hsn.hsn_code
        FROM purchase_return_items
        LEFT JOIN products ON purchase_return_items.product_id = products.id
        LEFT JOIN units ON purchase_return_items.unit_id = units.id
        LEFT JOIN hsn ON purchase_return_items.hsn_id = hsn.id
        WHERE purchase_return_items.return_id = ?
    """, (return_id,))
    items = cursor.fetchall()
    conn.close()
    return render_template("purchases/view_purchase_return.html", ret=ret, items=items)

@purchases.route("/purchase-returns/delete/<int:return_id>")
def delete_purchase_return(return_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM purchase_return_items WHERE return_id = ?", (return_id,))
    cursor.execute("DELETE FROM purchase_returns WHERE id = ?", (return_id,))
    conn.commit()
    conn.close()
    flash("Purchase Return deleted successfully!", "success")
    return redirect(url_for("purchases.purchase_returns_list"))

@purchases.route("/api/purchase-order/<int:order_id>")
def api_purchase_order(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT purchase_order_items.*,
              products.product_name
        FROM purchase_order_items
        LEFT JOIN products ON purchase_order_items.product_id = products.id
        WHERE purchase_order_items.order_id = ?
    """, (order_id,))
    items = cursor.fetchall()
    conn.close()
    result = []
    for item in items:
        result.append({
            "product_id": item["product_id"],
            "product_name": item["product_name"],
            "unit_id": item["unit_id"],
            "hsn_id": item["hsn_id"],
            "quantity": item["quantity"],
            "rate": item["rate"],
            "discount": item["discount"],
            "tax_rate": item["tax_rate"]
        })
    return {"items":result}

@purchases.route("/api/supplier-orders/<int:supplier_id>")
def api_supplier_orders(supplier_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, order_no
        FROM purchase_orders
        WHERE supplier_id = ?
        ORDER BY order_date DESC
    """, (supplier_id,))
    orders = cursor.fetchall()
    conn.close()
    result=[]
    for order in orders:
        result.append({"id": order["id"], "order_no": order["order_no"]})
    return{"orders": result}

@purchases.route("/api/supplier-grns/<int:supplier_id>")
def api_supplier_grns(supplier_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, grn_no
        FROM purchase_grn
        WHERE supplier_id = ?
        ORDER BY grn_date DESC
    """, (supplier_id,))
    grns = cursor.fetchall()
    conn.close()
    result = []
    for grn in grns:
        result.append({"id": grn["id"], "grn_no": grn["grn_no"]})
    return {"grns": result}

@purchases.route("/api/purchase-grn/<int:grn_id>")
def api_purchase_grn(grn_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT purchase_grn_items.*,
               products.product_name
        FROM purchase_grn_items
        LEFT JOIN products ON purchase_grn_items.product_id = products.id
        WHERE purchase_grn_items.grn_id = ?
    """, (grn_id,))
    items = cursor.fetchall()
    conn.close()
    result = []
    for item in items:
        result.append({
            "product_id": item["product_id"],
            "product_name": item["product_name"],
            "unit_id": item["unit_id"],
            "hsn_id": item["hsn_id"],
            "quantity": item["quantity"],
            "rate": item["rate"],
            "discount": item["discount"],
            "tax_rate": item["tax_rate"]
        })
    return {"items": result}