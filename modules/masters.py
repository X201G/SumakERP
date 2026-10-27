from flask import Blueprint
from flask import render_template, request, redirect, url_for, flash
from database import get_connection
from modules.auth import require_system
masters = Blueprint("masters", __name__)
masters.before_request(require_system("distribution"))
from modules.permissions import enforce_permissions
masters.before_request(enforce_permissions)



# ============================================
# UNIT MASTER
# ============================================

@masters.route("/units")
def units_list():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    conn.close()
    return render_template("masters/units.html", units=units)

@masters.route("/units/add", methods= ["GET", "POST"])
def add_unit():
    if request.method == "POST":
        unit_name = request.form["unit_name"]
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO units (unit_name) VALUES (?)", (unit_name,))
        conn.commit()
        conn.close()
        flash("Unit added Successfully!", "success")
        return redirect(url_for("masters.units_list"))
    return render_template("masters/units.html")

@masters.route("/units/delete/<int:unit_id>")
def delete_unit(unit_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM units WHERE id = ?", (unit_id,) )
    conn.commit()
    conn.close()
    flash("Unit deleted successfully! ", "danger")
    return redirect(url_for("masters.units_list"))

@masters.route("/units/edit/<int:unit_id>", methods= ["GET", "POST"])
def edit_unit(unit_id):
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        unit_name = request.form["unit_name"]
        cursor.execute("UPDATE units SET unit_name = ? WHERE id = ?", (unit_name, unit_id))
        conn.commit()
        conn.close()
        flash("Unit updated successfully!", "success")
        return redirect(url_for("masters.units_list"))
    cursor.execute("SELECT * FROM units WHERE id = ?", (unit_id,))
    unit = cursor.fetchone()
    conn.close()
    return render_template("masters/units.html",units=[], unit=unit, editing=True)

# ============================================
# CATEGORY MASTER
# ============================================

@masters.route("/categories")
def categories_list():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM categories ORDER BY category_name")
    categories = cursor.fetchall()
    conn.close()
    return render_template("masters/categories.html", categories=categories)

@masters.route("/categories/add", methods=["GET","POST"])
def add_category():
    if request.method == "POST":
        category_name = request.form["category_name"]
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO categories (category_name) VALUES (?)", (category_name,))
        conn.commit()
        conn.close()
        flash("Category added successfully!", "success")
        return redirect(url_for("masters.categories_list"))
    return render_template("masters/categories.html")

@masters.route("/categories/delete/<int:category_id>")
def delete_category(category_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM categories WHERE id=?", (category_id,))
    conn.commit()
    conn.close()
    flash("Category deleted successfully!", "danger")
    return redirect(url_for("masters.categories_list"))

@masters.route("/categories/edit/<int:category_id>", methods= ["GET", "POST"])
def edit_category(category_id):
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        category_name = request.form["category_name"]
        cursor.execute("UPDATE categories SET category_name = ? WHERE id = ?", (category_name, category_id))
        conn.commit()
        conn.close()
        flash("Category updated successfully!", "success")
        return redirect(url_for("masters.categories_list"))
    cursor.execute("SELECT * FROM categories WHERE id = ?", (category_id,))
    category = cursor.fetchone()
    conn.close()
    return render_template("masters/categories.html",categories=[], category=category, editing=True)
    

@masters.route("/subcategories")
def subcategories_list():
    conn= get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT subcategories.*, categories.category_name
        FROM subcategories
        LEFT JOIN categories ON subcategories.category_id = categories.id
        ORDER BY subcategory_name
    """)
    subcategories = cursor.fetchall()
    cursor.execute("SELECT * FROM categories ORDER BY category_name")
    categories = cursor.fetchall()
    conn.close()
    return render_template("masters/subcategories.html", subcategories = subcategories, categories = categories)

@masters.route("/subcategories/add", methods = ["GET", "POST"])
def add_subcategory():
    if request.method == "POST":
        subcategory_name = request.form["subcategory_name"]
        conn = get_connection()
        cursor = conn.cursor()
        category_id = request.form.get("category_id") or None
        cursor.execute("INSERT INTO subcategories (subcategory_name, category_id) VALUES (?,?)", (subcategory_name, category_id))
        conn.commit()
        conn.close()
        flash("Subcategory added successfully!", "success")
        return redirect(url_for("masters.subcategories_list"))
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM categories ORDER BY category_name")
    categories = cursor.fetchall()
    conn.close()
    return render_template("masters/subcategories.html", categories = categories)

@masters.route("/subcategories/delete/<int:subcategory_id>")
def delete_subcategory(subcategory_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM subcategories WHERE id=?", (subcategory_id,))
    conn.commit()
    conn.close()
    flash("Subcategory deleted successfully!", "danger")
    return redirect(url_for("masters.subcategories_list"))

@masters.route("/subcategories/edit/<int:subcategory_id>", methods= ["GET", "POST"])
def edit_subcategory(subcategory_id):
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        subcategory_name = request.form["subcategory_name"]
        cursor.execute("UPDATE subcategories SET subcategory_name = ? WHERE id = ?", (subcategory_name, subcategory_id))
        conn.commit()
        conn.close()
        flash("subcategory updated successfully!", "success")
        return redirect(url_for("masters.subcategories_list"))
    cursor.execute("SELECT * FROM subcategories WHERE id = ?", (subcategory_id,))
    subcategory = cursor.fetchone()
    cursor.execute("SELECT * FROM categories ORDER by category_name")
    categories = cursor.fetchall() 
    conn.close()
    return render_template("masters/subcategories.html",subcategories=[], subcategory=subcategory, categories = categories, editing=True)
    

# ============================================
# HSN MASTER
# ============================================

@masters.route("/hsn")
def hsn_list():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM hsn ORDER BY hsn_code")
    hsns = cursor.fetchall()
    conn.close()
    return render_template("masters/hsn.html", hsns = hsns)

@masters.route("/hsn/add", methods =["GET", "POST"])
def add_hsn():
    if request.method == "POST":
        hsn_code =  request.form["hsn_code"]
        description = request.form.get("description")
        gst_rate = request.form.get("gst_rate") or 18.0
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO hsn (hsn_code,  description, gst_rate) VALUES (?,?,?)", (hsn_code, description, gst_rate))
        conn.commit()
        conn.close()
        flash("HSN added successfully!", "success")
        return redirect(url_for("masters.hsn_list"))
    return render_template("masters/hsn.html")

@masters.route("/hsn/delete/<int:hsn_id>")
def delete_hsn(hsn_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM hsn WHERE id=?", (hsn_id,))
    conn.commit()
    conn.close()
    flash("HSN  deleted successfully!", "danger")
    return redirect(url_for("masters.hsn_list"))

@masters.route("/hsn/edit/<int:hsn_id>", methods= ["GET", "POST"])
def edit_hsn(hsn_id):
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        hsn_code = request.form["hsn_code"]
        description = request.form.get("description")
        gst_rate = request.form.get("gst_rate") or 18.0
        cursor.execute("UPDATE hsn SET hsn_code = ?, description = ?, gst_rate = ? WHERE id = ?", (hsn_code, description, gst_rate, hsn_id))
        conn.commit()
        conn.close()
        flash("hsn updated successfully!", "success")
        return redirect(url_for("masters.hsn_list"))
    cursor.execute("SELECT * FROM hsn WHERE id = ?", (hsn_id,))
    hsn = cursor.fetchone() 
    conn.close()
    return render_template("masters/hsn.html",hsns=[], hsn=hsn, editing=True)
    

# ============================================
# Customer master
# ============================================

@masters.route("/customers")
def customers_list():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    offset = (page - 1)* per_page
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM customers ORDER BY name LIMIT ? OFFSET ?", (per_page, offset))
    customers = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) FROM customers")
    total = cursor.fetchone()[0]
    total_pages = (total + per_page - 1) // per_page
    conn.close()
    return render_template("masters/customers.html", customers=customers, page=page, total_pages=total_pages)

@masters.route("/customers/add", methods=["GET", "POST"])
def add_customers():
    if request.method == "POST":
        name = request.form["name"]
        phone = request.form.get("phone")
        email = request.form.get("email")
        address = request.form.get("address")
        city = request.form.get("city")
        state = request.form.get("state")
        gst_number = request.form.get("gst_number")
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO customers (name, phone, email, address, city, state, gst_number) VALUES (?,?,?,?,?,?,?)", (name, phone, email, address, city, state, gst_number))
        conn.commit()
        conn.close()
        flash("Customer added successfully!", "success")
        return redirect(url_for("masters.customers_list"))
    return render_template("masters/customers.html")

@masters.route("/customers/delete/<int:customer_id>")
def delete_customer(customer_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM customers WHERE id=?", (customer_id,))
    conn.commit()
    conn.close()
    flash("Customer deleted successfully!", "danger")
    return redirect(url_for("masters.customers_list"))

@masters.route("/customers/edit/<int:customer_id>", methods=["GET", "POST"])
def edit_customer(customer_id):
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        name = request.form["name"]
        phone = request.form.get("phone")
        email = request.form.get("email")
        address = request.form.get("address")
        city = request.form.get("city")
        state = request.form.get("state")
        gst_number = request.form.get("gst_number")
        cursor.execute("UPDATE customers SET name=?, phone=?, email=?, address=?, city=?, state=?, gst_number =? WHERE id=?", (name,phone, email, address, city, state, gst_number, customer_id))
        conn.commit()
        conn.close()
        flash("Customer added successfully!", "success")
        return redirect(url_for("masters.customers_list"))
    cursor.execute("SELECT * FROM customers WHERE id = ?", (customer_id,))
    customer = cursor.fetchone()
    conn.close()
    return render_template("masters/customers.html", customers=[], customer=customer, editing=True, page=1, total_pages=1)

# ============================================
# Supplier Master
# ============================================

@masters.route("/suppliers")
def suppliers_list():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    conn.close()
    return render_template("masters/suppliers.html", suppliers=suppliers)

@masters.route("/suppliers/add", methods=["GET", "POST"])
def add_suppliers():
    if request.method == "POST":
        name = request.form["name"]
        phone = request.form.get("phone")
        email = request.form.get("email")
        address = request.form.get("address")
        city = request.form.get("city")
        state = request.form.get("state")
        gst_number = request.form.get("gst_number")
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO suppliers (name, phone, email, address, city, state, gst_number) VALUES (?,?,?,?,?,?,?)", (name, phone, email, address, city, state, gst_number))
        conn.commit()
        conn.close()
        flash("Supplier added successfully!", "success")
        return redirect(url_for("masters.suppliers_list"))
    return render_template("masters/suppliers.html")

@masters.route("/suppliers/delete/<int:supplier_id>")
def delete_supplier(supplier_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM suppliers WHERE id=?", (supplier_id,))
    conn.commit()
    conn.close()
    flash("Supplier deleted successfully!", "danger")
    return redirect(url_for("masters.suppliers_list"))

# ============================================
# PRODUCT MASTER
# ============================================

@masters.route("/products")
def products_list():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    offset = (page - 1)* per_page
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT Products.*,
            categories.category_name,
            subcategories.subcategory_name,
            units.unit_name,
            hsn.hsn_code,
            suppliers.name as supplier_name
        FROM products
        LEFT JOIN categories ON products.category_id = categories.id
        LEFT JOIN subcategories ON products.subcategory_id = subcategories.id
        LEFT JOIN units ON products.unit_id = units.id
        LEFT JOIN hsn ON products.hsn_id = hsn.id
        LEFT JOIN suppliers ON products.supplier_id = suppliers.id
        ORDER by product_name
        LIMIT ? OFFSET ?
    """, (per_page, offset))
    products = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) FROM products")
    total = cursor.fetchone()[0]
    total_pages = (total + per_page - 1) // per_page
    cursor.execute("SELECT * FROM categories ORDER BY category_name")
    categories = cursor.fetchall()
    cursor.execute("SELECT * FROM subcategories ORDER BY subcategory_name")
    subcategories = cursor.fetchall()
    cursor.execute("SELECT * FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT * FROM hsn ORDER BY hsn_code")
    hsns = cursor.fetchall()
    cursor.execute("SELECT * FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    conn.close()
    return render_template("masters/products.html", products=products, categories=categories, units=units, hsns=hsns, subcategories=subcategories, suppliers=suppliers, page=page, total_pages=total_pages)

@masters.route("/products/add", methods = ["GET", "POST"])
def add_products():
    if request.method == "POST":
        product_name = request.form["product_name"]
        print_description = request.form.get("print_description")
        category_id = request.form.get("category_id")
        subcategory_id = request.form.get("subcategory_id")
        unit_id = request.form.get("unit_id")
        hsn_id = request.form.get("hsn_id")
        tax_rate = request.form.get("tax_rate") or 18.0
        supplier_id = request.form.get("supplier_id")
        purchase_price = request.form.get("purchase_price")
        purchase_discount = request.form.get("purchase_discount") or 0
        sale_price = request.form.get("sale_price")
        sale_discount = request.form.get("sale_discount") or 0
        conn= get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO products (product_name, print_description, category_id, subcategory_id, unit_id, hsn_id, tax_rate, supplier_id, purchase_price, purchase_discount, sale_price, sale_discount) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (product_name, print_description, category_id, subcategory_id, unit_id, hsn_id, tax_rate, supplier_id, purchase_price, purchase_discount, sale_price, sale_discount))
        conn.commit()
        conn.close()
        flash("Product added successfully!", "success")
        return redirect(url_for("masters.products_list"))
        
    conn = get_connection()
    cursor= conn.cursor()
    cursor.execute("SELECT * FROM categories ORDER BY category_name")
    categories = cursor.fetchall()
    cursor.execute("SELECT * FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT * FROM hsn ORDER BY hsn_code")
    hsns = cursor.fetchall()
    cursor.execute("SELECT * FROM subcategories ORDER BY subcategory_name")
    subcategories = cursor.fetchall()
    cursor.execute("SELECT * FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    conn.close()
    return render_template("masters/products.html", categories=categories, units=units, hsns=hsns, subcategories=subcategories, suppliers=suppliers)

@masters.route("/products/delete/<int:product_id>")
def delete_product(product_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM products WHERE id=?", (product_id,))
    conn.commit()
    conn.close()
    flash("Product deleted successfully!", "danger")
    return redirect(url_for("masters.products_list"))

# ============================================
# TRANSPORTER MASTER
# ============================================
@masters.route("/transporters")
def transporters_list():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("masters/transporters.html", transporters=transporters)

@masters.route("/transporters/add", methods=["POST"])
def add_transporter():
    name = request.form["name"]
    phone = request.form.get("phone")
    email = request.form.get("email")
    address = request.form.get("address")
    city = request.form.get("city")
    state = request.form.get("state")
    gst_number = request.form.get("gst_number")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO transporters (name, phone, email, address, city, state, gst_number)
        VALUES (?,?,?,?,?,?,?)
    """, (name, phone, email, address, city, state, gst_number))
    conn.commit()
    conn.close()
    flash("Transporter added successfully!", "success")
    return redirect(url_for("masters.transporters_list"))

@masters.route("/transporters/delete/<int:transporter_id>")
def delete_transporter(transporter_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM transporters WHERE id = ?", (transporter_id,))
    conn.commit()
    conn.close()
    flash("Transporter deleted successfully!", "success")
    return redirect(url_for("masters.transporters_list"))
