import sqlite3
from flask import Blueprint
from flask import render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash
from database import get_connection as _get_connection
from modules.auth import require_system
from modules.permissions import enforce_permissions, SECTIONS, SECTION_LABELS, SECTION_GROUPS
from modules.document_database import (
    install_document_connection_cleanup,
    managed_document_connection,
)

get_connection = managed_document_connection(_get_connection)
manufacturing = Blueprint("manufacturing", __name__)
manufacturing.before_request(require_system("manufacturing"))
manufacturing.before_request(enforce_permissions)
install_document_connection_cleanup(manufacturing)

TOOL_TYPES = ["Drill", "Endmill", "Reamer", "Holemill", "Tap", "Special Tool"]
TOOL_TYPE_CODE_PREFIX = {
    "Drill": "DR",
    "Endmill": "EM",
    "Reamer": "RM",
    "Holemill": "HM",
    "Tap": "TP",
    "Special Tool": "ST",
}

def generate_item_code(tool_type, brand_name, cursor):
    prefix = TOOL_TYPE_CODE_PREFIX.get(tool_type, "XX")
    brand_code = brand_name[:2].upper() if brand_name else ""
    code_base = f"{prefix}{brand_code}"
    cursor.execute("SELECT item_code FROM mfg_tools WHERE item_code LIKE ?", (f"{code_base}%",))
    existing_numbers = []
    for row in cursor.fetchall():
        try:
            existing_numbers.append(int(row["item_code"][len(code_base):]))
        except (ValueError, TypeError):
            pass
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"{code_base}{str(next_number).zfill(4)}"

CUTTING_ALLOWANCE_MM = 4

def _final_price(price, discount_percent):
    price = float(price or 0)
    discount_percent = float(discount_percent or 0)
    return round(price * (1 - discount_percent / 100), 2)

@manufacturing.route("/manufacturing")
def index():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as c FROM mfg_rods")
    total_rods = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) as c FROM mfg_tools")
    total_tools = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) as c FROM mfg_job_batches")
    total_batches = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) as c FROM mfg_challans")
    total_challans = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) as c FROM mfg_invoices")
    total_invoices = cursor.fetchone()["c"]

    pending_count = 0
    complete_count = 0
    cursor.execute("""
        SELECT batch_id,
               MAX(CASE WHEN receive_date IS NULL THEN 1 ELSE 0 END) as any_pending,
               GROUP_CONCAT(stages) as all_stages
        FROM mfg_job_stages
        GROUP BY batch_id
    """)
    for row in cursor.fetchall():
        if row["any_pending"]:
            pending_count += 1
        else:
            covered = set(row["all_stages"].split(","))
            if len(covered) == len(STAGES):
                complete_count += 1

    cursor.execute("""
        SELECT mfg_job_batches.*, mfg_tools.tool_name, customers.name as customer_name
        FROM mfg_job_batches
        JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
        LEFT JOIN customers ON mfg_job_batches.customer_id = customers.id
        ORDER BY mfg_job_batches.id DESC LIMIT 5
    """)
    recent_batches = cursor.fetchall()
    conn.close()

    return render_template("manufacturing/dashboard.html",
        total_rods=total_rods, total_tools=total_tools, total_batches=total_batches,
        total_challans=total_challans, total_invoices=total_invoices,
        pending_count=pending_count, complete_count=complete_count,
        recent_batches=recent_batches)

@manufacturing.route("/manufacturing/no-access")
def no_access():
    return render_template("manufacturing/no_access.html")

# ============================================
# RAW MATERIAL - CARBIDE ROD
# ============================================

@manufacturing.route("/manufacturing/rods")
def rods_list():
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
        where = "WHERE (mfg_rods.grade LIKE ? OR suppliers.name LIKE ?)"
        params = [f"%{q_search}%", f"%{q_search}%"]

    cursor.execute(f"""
        SELECT mfg_rods.*, suppliers.name as supplier_name
        FROM mfg_rods
        LEFT JOIN suppliers ON mfg_rods.supplier_id = suppliers.id
        {where}
        ORDER BY mfg_rods.rod_date DESC, mfg_rods.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    rods = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_rods
        LEFT JOIN suppliers ON mfg_rods.supplier_id = suppliers.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/rods.html", rods=rods, page=page, total_pages=total_pages, total=total, suppliers=suppliers, q=q)

@manufacturing.route("/manufacturing/rods/add", methods=["GET", "POST"])
def add_rod():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        rod_date = request.form["rod_date"]
        grade = request.form.get("grade")
        diameter_mm = request.form["diameter_mm"]
        length_mm = request.form.get("length_mm") or 330
        weight_kg = request.form.get("weight_kg") or None
        quantity = request.form["quantity"]
        purchase_price = request.form.get("purchase_price") or 0
        purchase_discount_percent = request.form.get("purchase_discount_percent") or 0
        sales_price = request.form.get("sales_price") or None
        sales_discount_percent = request.form.get("sales_discount_percent") or 0
        rate = _final_price(purchase_price, purchase_discount_percent)
        remarks = request.form.get("remarks")
        cursor.execute("""
            INSERT INTO mfg_rods (rod_date, grade, diameter_mm, length_mm, weight_kg, remaining_length_mm, quantity, supplier_id, rate,
                                   purchase_price, purchase_discount_percent, sales_price, sales_discount_percent, remarks)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (rod_date, grade, diameter_mm, length_mm, weight_kg, length_mm, quantity, None, rate,
              purchase_price, purchase_discount_percent, sales_price, sales_discount_percent, remarks))
        rod_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO mfg_stock_movements (item_type, item_id, movement_type, quantity, source_type, source_id, source_no, movement_date)
            VALUES (?,?,?,?,?,?,?,?)
        """, ("rod", rod_id, "in", length_mm, "purchase", rod_id, grade or f"Rod #{rod_id}", rod_date))
        conn.commit()
        conn.close()
        flash("Rod entry added successfully!", "success")
        return redirect(url_for("manufacturing.rods_list"))

    conn.close()
    return render_template("manufacturing/add_rod.html")

@manufacturing.route("/manufacturing/rods/edit/<int:rod_id>", methods=["GET", "POST"])
def edit_rod(rod_id):
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        grade = request.form.get("grade")
        diameter_mm = request.form["diameter_mm"]
        supplier_id = request.form.get("supplier_id") or None
        purchase_price = request.form.get("purchase_price") or 0
        purchase_discount_percent = request.form.get("purchase_discount_percent") or 0
        sales_price = request.form.get("sales_price") or None
        sales_discount_percent = request.form.get("sales_discount_percent") or 0
        rate = _final_price(purchase_price, purchase_discount_percent)
        remarks = request.form.get("remarks")
        cursor.execute("""
            UPDATE mfg_rods SET grade=?, diameter_mm=?, supplier_id=?, rate=?,
                                 purchase_price=?, purchase_discount_percent=?, sales_price=?, sales_discount_percent=?, remarks=?
            WHERE id=?
        """, (grade, diameter_mm, supplier_id, rate, purchase_price, purchase_discount_percent,
              sales_price, sales_discount_percent, remarks, rod_id))
        conn.commit()
        conn.close()
        flash("Rod entry updated successfully!", "success")
        return redirect(url_for("manufacturing.rods_list"))

    cursor.execute("SELECT * FROM mfg_rods WHERE id = ?", (rod_id,))
    rod = cursor.fetchone()
    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/edit_rod.html", rod=rod, suppliers=suppliers)

@manufacturing.route("/manufacturing/rods/delete/<int:rod_id>")
def delete_rod(rod_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as c FROM mfg_job_batches WHERE rod_id = ?", (rod_id,))
    if cursor.fetchone()["c"] > 0:
        conn.close()
        flash("Cannot delete this rod - it's already used in a job batch. Edit it instead if something needs correcting.", "danger")
        return redirect(url_for("manufacturing.rods_list"))
    cursor.execute("DELETE FROM mfg_rods WHERE id=?", (rod_id,))
    conn.commit()
    conn.close()
    flash("Rod entry deleted successfully!", "danger")
    return redirect(url_for("manufacturing.rods_list"))

# ============================================
# TOOL MASTER
# ============================================

@manufacturing.route("/manufacturing/tools")
def tools_list():
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
        where = "WHERE (mfg_tools.item_code LIKE ? OR mfg_tools.tool_name LIKE ? OR mfg_tools.tool_type LIKE ? OR customers.name LIKE ? OR mfg_brands.name LIKE ?)"
        params = [f"%{q_search}%", f"%{q_search}%", f"%{q_search}%", f"%{q_search}%", f"%{q_search}%"]

    cursor.execute(f"""
        SELECT mfg_tools.*, customers.name as customer_name, mfg_brands.name as brand_name
        FROM mfg_tools
        LEFT JOIN customers ON mfg_tools.customer_id = customers.id
        LEFT JOIN mfg_brands ON mfg_tools.brand_id = mfg_brands.id
        {where}
        ORDER BY mfg_tools.tool_name LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    tools = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_tools
        LEFT JOIN customers ON mfg_tools.customer_id = customers.id
        LEFT JOIN mfg_brands ON mfg_tools.brand_id = mfg_brands.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    conn.close()
    return render_template("manufacturing/tools.html", tools=tools, page=page, total_pages=total_pages, total=total, q=q)

@manufacturing.route("/manufacturing/tools/add", methods=["GET", "POST"])
def add_tool():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        tool_name = request.form["tool_name"]
        tool_type = request.form["tool_type"]
        diameter_mm = request.form.get("diameter_mm") or None
        overall_length_mm = request.form.get("overall_length_mm") or None
        flute_length_mm = request.form.get("flute_length_mm") or None
        shank_diameter_mm = request.form.get("shank_diameter_mm") or None
        customer_id = request.form.get("customer_id") or None
        brand_id = request.form.get("brand_id") or None
        purchase_price = request.form.get("purchase_price") or None
        purchase_discount_percent = request.form.get("purchase_discount_percent") or 0
        sales_price = request.form.get("sales_price") or None
        sales_discount_percent = request.form.get("sales_discount_percent") or 0
        remarks = request.form.get("remarks")

        if not customer_id:
            flash("Please select a customer - every tool is dedicated to one.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_tool"))

        brand_name = None
        if brand_id:
            cursor.execute("SELECT name FROM mfg_brands WHERE id = ?", (brand_id,))
            brand_row = cursor.fetchone()
            brand_name = brand_row["name"] if brand_row else None
        cursor.execute("BEGIN IMMEDIATE")
        item_code = generate_item_code(tool_type, brand_name, cursor)

        cursor.execute("""
            INSERT INTO mfg_tools (item_code, brand_id, tool_name, tool_type, diameter_mm, overall_length_mm, flute_length_mm, shank_diameter_mm, customer_id,
                                    purchase_price, purchase_discount_percent, sales_price, sales_discount_percent, remarks)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (item_code, brand_id, tool_name, tool_type, diameter_mm, overall_length_mm, flute_length_mm, shank_diameter_mm, customer_id,
              purchase_price, purchase_discount_percent, sales_price, sales_discount_percent, remarks))
        conn.commit()
        conn.close()
        flash(f"Tool added successfully! Item code: {item_code}", "success")
        return redirect(url_for("manufacturing.tools_list"))

    cursor.execute("SELECT id, name FROM customers ORDER BY name")
    customers = cursor.fetchall()
    cursor.execute("SELECT id, tool_type, name FROM mfg_brands ORDER BY tool_type, name")
    brands = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/add_tool.html", tool_types=TOOL_TYPES, customers=customers, brands=brands)

@manufacturing.route("/manufacturing/tools/edit/<int:tool_id>", methods=["GET", "POST"])
def edit_tool(tool_id):
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        tool_name = request.form["tool_name"]
        tool_type = request.form["tool_type"]
        diameter_mm = request.form.get("diameter_mm") or None
        overall_length_mm = request.form.get("overall_length_mm") or None
        flute_length_mm = request.form.get("flute_length_mm") or None
        shank_diameter_mm = request.form.get("shank_diameter_mm") or None
        customer_id = request.form.get("customer_id") or None
        brand_id = request.form.get("brand_id") or None
        purchase_price = request.form.get("purchase_price") or None
        purchase_discount_percent = request.form.get("purchase_discount_percent") or 0
        sales_price = request.form.get("sales_price") or None
        sales_discount_percent = request.form.get("sales_discount_percent") or 0
        remarks = request.form.get("remarks")

        if not customer_id:
            flash("Please select a customer - every tool is dedicated to one.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.edit_tool", tool_id=tool_id))

        cursor.execute("""
            UPDATE mfg_tools SET tool_name=?, tool_type=?, diameter_mm=?, overall_length_mm=?, flute_length_mm=?, shank_diameter_mm=?, customer_id=?, brand_id=?,
                                  purchase_price=?, purchase_discount_percent=?, sales_price=?, sales_discount_percent=?, remarks=?
            WHERE id=?
        """, (tool_name, tool_type, diameter_mm, overall_length_mm, flute_length_mm, shank_diameter_mm, customer_id, brand_id,
              purchase_price, purchase_discount_percent, sales_price, sales_discount_percent, remarks, tool_id))
        conn.commit()
        conn.close()
        flash("Tool updated successfully! (Item code stays the same once assigned.)", "success")
        return redirect(url_for("manufacturing.tools_list"))

    cursor.execute("SELECT * FROM mfg_tools WHERE id = ?", (tool_id,))
    tool = cursor.fetchone()
    cursor.execute("SELECT id, name FROM customers ORDER BY name")
    customers = cursor.fetchall()
    cursor.execute("SELECT id, tool_type, name FROM mfg_brands ORDER BY tool_type, name")
    brands = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/edit_tool.html", tool=tool, tool_types=TOOL_TYPES, customers=customers, brands=brands)

# ============================================
# BRAND MASTER
# ============================================

@manufacturing.route("/manufacturing/brands")
def brands_list():
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
        where = "WHERE (name LIKE ? OR tool_type LIKE ?)"
        params = [f"%{q_search}%", f"%{q_search}%"]

    cursor.execute(f"SELECT * FROM mfg_brands {where} ORDER BY tool_type, name LIMIT ? OFFSET ?", params + [per_page, offset])
    brands = cursor.fetchall()

    cursor.execute(f"SELECT COUNT(*) FROM mfg_brands {where}", params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    conn.close()
    return render_template("manufacturing/brands.html", brands=brands, page=page, total_pages=total_pages, total=total, q=q)

@manufacturing.route("/manufacturing/brands/add", methods=["GET", "POST"])
def add_brand():
    if request.method == "POST":
        conn = get_connection()
        cursor = conn.cursor()
        tool_type = request.form["tool_type"]
        name = request.form["name"].strip()
        if not tool_type or not name:
            flash("Product type and brand name are both required.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_brand"))
        cursor.execute("SELECT id FROM mfg_brands WHERE tool_type = ? AND name = ?", (tool_type, name))
        if cursor.fetchone():
            flash(f"Brand '{name}' already exists for {tool_type}.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_brand"))
        cursor.execute("INSERT INTO mfg_brands (tool_type, name) VALUES (?,?)", (tool_type, name))
        conn.commit()
        conn.close()
        flash("Brand added successfully!", "success")
        return redirect(url_for("manufacturing.brands_list"))
    return render_template("manufacturing/add_brand.html", tool_types=TOOL_TYPES)

@manufacturing.route("/manufacturing/brands/edit/<int:brand_id>", methods=["GET", "POST"])
def edit_brand(brand_id):
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        tool_type = request.form["tool_type"]
        name = request.form["name"].strip()
        if not tool_type or not name:
            flash("Product type and brand name are both required.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.edit_brand", brand_id=brand_id))
        cursor.execute("SELECT id FROM mfg_brands WHERE tool_type = ? AND name = ? AND id != ?", (tool_type, name, brand_id))
        if cursor.fetchone():
            flash(f"Brand '{name}' already exists for {tool_type}.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.edit_brand", brand_id=brand_id))
        cursor.execute("UPDATE mfg_brands SET tool_type=?, name=? WHERE id=?", (tool_type, name, brand_id))
        conn.commit()
        conn.close()
        flash("Brand updated successfully!", "success")
        return redirect(url_for("manufacturing.brands_list"))
    cursor.execute("SELECT * FROM mfg_brands WHERE id = ?", (brand_id,))
    brand = cursor.fetchone()
    conn.close()
    return render_template("manufacturing/edit_brand.html", brand=brand, tool_types=TOOL_TYPES)

@manufacturing.route("/manufacturing/brands/delete/<int:brand_id>")
def delete_brand(brand_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as c FROM mfg_tools WHERE brand_id = ?", (brand_id,))
    if cursor.fetchone()["c"] > 0:
        conn.close()
        flash("Cannot delete this brand - it's already used by a tool.", "danger")
        return redirect(url_for("manufacturing.brands_list"))
    cursor.execute("DELETE FROM mfg_brands WHERE id = ?", (brand_id,))
    conn.commit()
    conn.close()
    flash("Brand deleted successfully!", "danger")
    return redirect(url_for("manufacturing.brands_list"))

# ============================================
# ITEM CATEGORY MASTER (Manufacturing's own list - Distribution's shared
# categories are tool-product-specific, not appropriate for purchase items)
# ============================================

@manufacturing.route("/manufacturing/item-categories")
def item_categories_list():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_item_categories ORDER BY name")
    categories = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/item_categories.html", categories=categories)

@manufacturing.route("/manufacturing/item-categories/add", methods=["GET", "POST"])
def add_item_category():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        name = request.form["name"].strip()
        if not name:
            flash("Category name is required.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_item_category"))
        cursor.execute("SELECT id FROM mfg_item_categories WHERE name = ?", (name,))
        if cursor.fetchone():
            flash(f"Category '{name}' already exists.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_item_category"))
        cursor.execute("INSERT INTO mfg_item_categories (name) VALUES (?)", (name,))
        conn.commit()
        conn.close()
        flash("Category added successfully!", "success")
        return redirect(url_for("manufacturing.item_categories_list"))
    return render_template("manufacturing/add_item_category.html")

@manufacturing.route("/manufacturing/item-categories/edit/<int:category_id>", methods=["GET", "POST"])
def edit_item_category(category_id):
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        name = request.form["name"].strip()
        if not name:
            flash("Category name is required.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.edit_item_category", category_id=category_id))
        cursor.execute("SELECT id FROM mfg_item_categories WHERE name = ? AND id != ?", (name, category_id))
        if cursor.fetchone():
            flash(f"Category '{name}' already exists.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.edit_item_category", category_id=category_id))
        cursor.execute("UPDATE mfg_item_categories SET name=? WHERE id=?", (name, category_id))
        conn.commit()
        conn.close()
        flash("Category updated successfully!", "success")
        return redirect(url_for("manufacturing.item_categories_list"))
    cursor.execute("SELECT * FROM mfg_item_categories WHERE id = ?", (category_id,))
    category = cursor.fetchone()
    conn.close()
    return render_template("manufacturing/edit_item_category.html", category=category)

@manufacturing.route("/manufacturing/item-categories/delete/<int:category_id>")
def delete_item_category(category_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as c FROM mfg_items WHERE category_id = ?", (category_id,))
    if cursor.fetchone()["c"] > 0:
        conn.close()
        flash("Cannot delete this category - it's already used by an item.", "danger")
        return redirect(url_for("manufacturing.item_categories_list"))
    cursor.execute("DELETE FROM mfg_item_categories WHERE id = ?", (category_id,))
    conn.commit()
    conn.close()
    flash("Category deleted successfully!", "danger")
    return redirect(url_for("manufacturing.item_categories_list"))


# ============================================
# ITEM MASTER (grinding wheels, oils, other purchasable Manufacturing consumables -
# not rods, not tool designs. Category is Manufacturing's own; Unit reuses Distribution's
# shared master; HSN is free text, typed directly - no dictionary/suggestions yet.)
# ============================================

@manufacturing.route("/manufacturing/items")
def items_list():
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
        where = "WHERE (mfg_items.item_name LIKE ? OR mfg_item_categories.name LIKE ? OR mfg_items.hsn_code LIKE ?)"
        params = [f"%{q_search}%", f"%{q_search}%", f"%{q_search}%"]

    cursor.execute(f"""
        SELECT mfg_items.*, mfg_item_categories.name as category_name, units.unit_name
        FROM mfg_items
        LEFT JOIN mfg_item_categories ON mfg_items.category_id = mfg_item_categories.id
        LEFT JOIN units ON mfg_items.unit_id = units.id
        {where}
        ORDER BY mfg_items.item_name LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    items = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_items
        LEFT JOIN mfg_item_categories ON mfg_items.category_id = mfg_item_categories.id
        LEFT JOIN units ON mfg_items.unit_id = units.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    conn.close()
    return render_template("manufacturing/items.html", items=items, page=page, total_pages=total_pages, total=total, q=q)

@manufacturing.route("/manufacturing/items/add", methods=["GET", "POST"])
def add_item():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        item_name = request.form["item_name"]
        print_description = request.form.get("print_description")
        category_id = request.form.get("category_id") or None
        unit_id = request.form.get("unit_id") or None
        hsn_code = request.form.get("hsn_code") or None
        tax_rate = request.form.get("tax_rate") or 18
        purchase_price = request.form.get("purchase_price") or 0
        purchase_discount_percent = request.form.get("purchase_discount_percent") or 0
        sales_price = request.form.get("sales_price") or None
        sales_discount_percent = request.form.get("sales_discount_percent") or 0
        remarks = request.form.get("remarks")
        cursor.execute("""
            INSERT INTO mfg_items (item_name, print_description, category_id, unit_id, hsn_code, tax_rate, purchase_price,
                                    purchase_discount_percent, sales_price, sales_discount_percent, remarks)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (item_name, print_description, category_id, unit_id, hsn_code, tax_rate, purchase_price,
              purchase_discount_percent, sales_price, sales_discount_percent, remarks))
        conn.commit()
        conn.close()
        flash("Item added successfully!", "success")
        return redirect(url_for("manufacturing.items_list"))

    cursor.execute("SELECT id, name FROM mfg_item_categories ORDER BY name")
    categories = cursor.fetchall()
    cursor.execute("SELECT id, unit_name FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/add_item.html", categories=categories, units=units)

@manufacturing.route("/manufacturing/items/edit/<int:item_id>", methods=["GET", "POST"])
def edit_item(item_id):
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        item_name = request.form["item_name"]
        print_description = request.form.get("print_description")
        category_id = request.form.get("category_id") or None
        unit_id = request.form.get("unit_id") or None
        hsn_code = request.form.get("hsn_code") or None
        tax_rate = request.form.get("tax_rate") or 18
        purchase_price = request.form.get("purchase_price") or 0
        purchase_discount_percent = request.form.get("purchase_discount_percent") or 0
        sales_price = request.form.get("sales_price") or None
        sales_discount_percent = request.form.get("sales_discount_percent") or 0
        remarks = request.form.get("remarks")
        cursor.execute("""
            UPDATE mfg_items SET item_name=?, print_description=?, category_id=?, unit_id=?, hsn_code=?, tax_rate=?, purchase_price=?,
                                  purchase_discount_percent=?, sales_price=?, sales_discount_percent=?, remarks=?
            WHERE id=?
        """, (item_name, print_description, category_id, unit_id, hsn_code, tax_rate, purchase_price,
              purchase_discount_percent, sales_price, sales_discount_percent, remarks, item_id))
        conn.commit()
        conn.close()
        flash("Item updated successfully!", "success")
        return redirect(url_for("manufacturing.items_list"))

    cursor.execute("SELECT * FROM mfg_items WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    cursor.execute("SELECT id, name FROM mfg_item_categories ORDER BY name")
    categories = cursor.fetchall()
    cursor.execute("SELECT id, unit_name FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/edit_item.html", item=item, categories=categories, units=units)

@manufacturing.route("/manufacturing/items/delete/<int:item_id>")
def delete_item(item_id):
    conn = get_connection()
    cursor = conn.cursor()
    for table in ["mfg_purchase_order_items", "mfg_purchase_grn_items", "mfg_purchase_invoice_items", "mfg_purchase_return_items"]:
        cursor.execute(f"SELECT COUNT(*) as c FROM {table} WHERE item_type='item' AND item_id=?", (item_id,))
        if cursor.fetchone()["c"] > 0:
            conn.close()
            flash("Cannot delete this item - it's already used in a purchase document. Edit it instead if something needs correcting.", "danger")
            return redirect(url_for("manufacturing.items_list"))
    cursor.execute("DELETE FROM mfg_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    flash("Item deleted successfully!", "danger")
    return redirect(url_for("manufacturing.items_list"))


@manufacturing.route("/manufacturing/tools/delete/<int:tool_id>")
def delete_tool(tool_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as c FROM mfg_job_batches WHERE tool_id = ?", (tool_id,))
    if cursor.fetchone()["c"] > 0:
        conn.close()
        flash("Cannot delete this tool - it's already used in a job batch. Edit it instead if something needs correcting.", "danger")
        return redirect(url_for("manufacturing.tools_list"))
    cursor.execute("DELETE FROM mfg_tools WHERE id=?", (tool_id,))
    conn.commit()
    conn.close()
    flash("Tool deleted successfully!", "danger")
    return redirect(url_for("manufacturing.tools_list"))


# ============================================
# TRANSPORTER MASTER (shared with Distribution's `transporters` table - a transport
# company isn't specific to one system, but Manufacturing has its own login with no
# access to Distribution's pages, so it needs its own list/add/delete here)
# ============================================

@manufacturing.route("/manufacturing/transporters")
def transporters_list():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/transporters.html", transporters=transporters)

@manufacturing.route("/manufacturing/transporters/add", methods=["POST"])
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
    return redirect(url_for("manufacturing.transporters_list"))

@manufacturing.route("/manufacturing/transporters/edit/<int:transporter_id>", methods=["GET", "POST"])
def edit_transporter(transporter_id):
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
        cursor.execute("""
            UPDATE transporters SET name=?, phone=?, email=?, address=?, city=?, state=?, gst_number=?
            WHERE id=?
        """, (name, phone, email, address, city, state, gst_number, transporter_id))
        conn.commit()
        conn.close()
        flash("Transporter updated successfully!", "success")
        return redirect(url_for("manufacturing.transporters_list"))
    cursor.execute("SELECT * FROM transporters WHERE id = ?", (transporter_id,))
    transporter = cursor.fetchone()
    conn.close()
    return render_template("manufacturing/edit_transporter.html", transporter=transporter)

@manufacturing.route("/manufacturing/transporters/delete/<int:transporter_id>")
def delete_transporter(transporter_id):
    conn = get_connection()
    cursor = conn.cursor()
    for table in ["mfg_challans", "mfg_resharpen_challans", "mfg_purchase_orders", "mfg_purchase_grn", "mfg_purchase_invoices", "mfg_purchase_returns"]:
        cursor.execute(f"SELECT COUNT(*) as c FROM {table} WHERE transporter_id=?", (transporter_id,))
        if cursor.fetchone()["c"] > 0:
            conn.close()
            flash("Cannot delete this transporter - it's already used on a Manufacturing document. Edit it instead if something needs correcting.", "danger")
            return redirect(url_for("manufacturing.transporters_list"))
    cursor.execute("DELETE FROM transporters WHERE id = ?", (transporter_id,))
    conn.commit()
    conn.close()
    flash("Transporter deleted successfully!", "success")
    return redirect(url_for("manufacturing.transporters_list"))


# ============================================
# USERS & PERMISSIONS (admin only - Manufacturing-scoped, separate from
# Distribution's shared /users page)
# ============================================

def _require_admin():
    if session.get("role") != "admin":
        flash("Only an admin can manage users and permissions.", "danger")
        return redirect(url_for("manufacturing.no_access"))

@manufacturing.route("/manufacturing/users")
def manufacturing_users_list():
    guard = _require_admin()
    if guard:
        return guard
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE system='manufacturing' ORDER BY username")
    users = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/users.html", users=users)

@manufacturing.route("/manufacturing/users/add", methods=["GET", "POST"])
def manufacturing_add_user():
    guard = _require_admin()
    if guard:
        return guard
    if request.method == "POST":
        username = request.form["username"].strip()
        full_name = request.form.get("full_name")
        role = request.form.get("role") or "staff"
        password = request.form["password"]
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name, role, system) VALUES (?,?,?,?,?)",
                (username, generate_password_hash(password), full_name, role, "manufacturing")
            )
            conn.commit()
            flash("User added successfully!", "success")
        except Exception as error:
            is_unique_violation = (
                isinstance(error, sqlite3.IntegrityError)
                and "UNIQUE constraint failed" in str(error)
            ) or getattr(error, "sqlstate", None) == "23505"
            if not is_unique_violation:
                raise
            flash("That username is already taken.", "danger")
        conn.close()
        return redirect(url_for("manufacturing.manufacturing_users_list"))
    return render_template("manufacturing/add_user.html")

@manufacturing.route("/manufacturing/users/<int:user_id>/permissions", methods=["GET", "POST"])
def manufacturing_user_permissions(user_id):
    guard = _require_admin()
    if guard:
        return guard
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id=? AND system='manufacturing'", (user_id,))
    user = cursor.fetchone()
    if not user:
        conn.close()
        flash("User not found.", "danger")
        return redirect(url_for("manufacturing.manufacturing_users_list"))

    if request.method == "POST":
        cursor.execute("DELETE FROM permissions WHERE user_id=?", (user_id,))
        for section, _label in SECTIONS:
            can_view = 1 if request.form.get(f"view_{section}") else 0
            can_create = 1 if request.form.get(f"create_{section}") else 0
            can_edit = 1 if request.form.get(f"edit_{section}") else 0
            can_delete = 1 if request.form.get(f"delete_{section}") else 0
            if can_view or can_create or can_edit or can_delete:
                cursor.execute(
                    "INSERT INTO permissions (user_id, section, can_view, can_create, can_edit, can_delete) VALUES (?,?,?,?,?,?)",
                    (user_id, section, can_view, can_create, can_edit, can_delete)
                )
        conn.commit()
        conn.close()
        flash("Permissions updated successfully!", "success")
        return redirect(url_for("manufacturing.manufacturing_users_list"))

    cursor.execute("SELECT section, can_view, can_create, can_edit, can_delete FROM permissions WHERE user_id=?", (user_id,))
    existing = {row["section"]: row for row in cursor.fetchall()}
    conn.close()
    return render_template("manufacturing/user_permissions.html", user=user,
        groups=SECTION_GROUPS, labels=SECTION_LABELS, existing=existing)


# ============================================
# JOB WORK BATCHES (a run of N pieces of one tool, tracked through
# Blank Making -> Tool Manufacturing -> Coating)
# ============================================

STAGES = [
    {"key": "blank_making", "label": "Blank Making"},
    {"key": "tool_manufacturing", "label": "Tool Manufacturing"},
    {"key": "coating", "label": "Coating"},
]

def stage_label_for(keys):
    labels = [s["label"] for s in STAGES if s["key"] in keys]
    if len(labels) == len(STAGES):
        return "Full Manufacturing"
    return " + ".join(labels)

def compute_tool_stock(cursor, tool_id):
    cursor.execute("SELECT * FROM mfg_job_batches WHERE tool_id = ?", (tool_id,))
    batches = cursor.fetchall()

    not_started_qty = 0
    stage_qty = {"blank_making": 0, "tool_manufacturing": 0, "coating": 0}
    ready_qty = {"blank_making": 0, "tool_manufacturing": 0, "coating": 0}
    finished_qty = 0

    for b in batches:
        cursor.execute("SELECT * FROM mfg_job_stages WHERE batch_id = ?", (b["id"],))
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
        missing = [s["key"] for s in STAGES if s["key"] not in covered]
        if not missing:
            # Use the actual quantity that survived each completed stage (the smallest
            # quantity_received across this batch's stages), not the originally planned
            # batch quantity - otherwise rejects/breakage during manufacturing silently
            # inflate the finished-stock count over time.
            received_qtys = [op["quantity_received"] for op in ops if op["quantity_received"]]
            finished_qty += min(received_qtys) if received_qtys else b["quantity"]
        else:
            ready_qty[missing[0]] += b["quantity"]

    cursor.execute("""
        SELECT COALESCE(SUM(quantity), 0) as total FROM mfg_stock_movements
        WHERE item_type = 'tool' AND item_id = ? AND movement_type = 'out' AND source_type = 'delivery'
    """, (tool_id,))
    delivered_qty = cursor.fetchone()["total"]

    return {
        "not_started": not_started_qty, "stage_qty": stage_qty, "ready_qty": ready_qty,
        "finished_in_hand": round(finished_qty - delivered_qty, 2), "delivered": delivered_qty
    }

def generate_batch_no(batch_date, cursor):
    date_part = batch_date.replace("-", "")
    cursor.execute("SELECT batch_no FROM mfg_job_batches WHERE batch_no LIKE ?", (f"JW-{date_part}-%",))
    existing_numbers = []
    for row in cursor.fetchall():
        try:
            existing_numbers.append(int(row["batch_no"].split("-")[-1]))
        except ValueError:
            pass
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"JW-{date_part}-{str(next_number).zfill(4)}"

def generate_challan_no(challan_type, challan_date, cursor):
    prefix = "JWC" if challan_type == "vendor_jobwork" else "DC"
    date_part = challan_date.replace("-", "")
    cursor.execute("SELECT challan_no FROM mfg_challans WHERE challan_no LIKE ?", (f"{prefix}-{date_part}-%",))
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
    cursor.execute("SELECT invoice_no FROM mfg_invoices WHERE invoice_no LIKE ?", (f"MI-{date_part}-%",))
    existing_numbers = []
    for row in cursor.fetchall():
        try:
            existing_numbers.append(int(row["invoice_no"].split("-")[-1]))
        except ValueError:
            pass
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"MI-{date_part}-{str(next_number).zfill(4)}"

@manufacturing.route("/manufacturing/batches")
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
        where_parts.append("(mfg_job_batches.batch_no LIKE ? OR mfg_tools.tool_name LIKE ? OR customers.name LIKE ?)")
        params += [f"%{q_search}%", f"%{q_search}%", f"%{q_search}%"]
    if from_date:
        where_parts.append("mfg_job_batches.batch_date >= ?")
        params.append(from_date)
    if to_date:
        where_parts.append("mfg_job_batches.batch_date <= ?")
        params.append(to_date)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    cursor.execute(f"""
        SELECT mfg_job_batches.*, mfg_tools.tool_name, mfg_tools.tool_type, customers.name as customer_name
        FROM mfg_job_batches
        JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
        LEFT JOIN customers ON mfg_job_batches.customer_id = customers.id
        {where}
        ORDER BY mfg_job_batches.batch_date DESC, mfg_job_batches.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    batches = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_job_batches
        JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
        LEFT JOIN customers ON mfg_job_batches.customer_id = customers.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    batch_list = []
    for b in batches:
        cursor.execute("SELECT * FROM mfg_job_stages WHERE batch_id = ? ORDER BY id", (b["id"],))
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
            elif len(covered) == len(STAGES):
                status, status_class = "Complete", "success"
            else:
                status, status_class = "In Progress", "info"
        batch_list.append({"batch": b, "status": status, "status_class": status_class})

    conn.close()
    return render_template("manufacturing/batches.html", batch_list=batch_list, page=page, total_pages=total_pages, total=total, q=q, from_date=from_date, to_date=to_date)

def suggest_best_rod(tool_id, quantity, grade, cursor):
    cursor.execute("SELECT overall_length_mm FROM mfg_tools WHERE id = ?", (tool_id,))
    tool_row = cursor.fetchone()
    if not tool_row or not tool_row["overall_length_mm"]:
        return None
    length_needed = (tool_row["overall_length_mm"] + CUTTING_ALLOWANCE_MM) * quantity
    where = "WHERE remaining_length_mm >= ?"
    params = [length_needed]
    if grade:
        where += " AND grade = ?"
        params.append(grade)
    cursor.execute(f"SELECT id FROM mfg_rods {where} ORDER BY remaining_length_mm ASC LIMIT 1", params)
    row = cursor.fetchone()
    return row["id"] if row else None

@manufacturing.route("/manufacturing/batches/add", methods=["GET", "POST"])
def add_batch():
    conn = get_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        batch_date = request.form["batch_date"]
        tool_id = request.form["tool_id"]
        rod_id = request.form.get("rod_id")
        grade = request.form.get("grade") or None
        override_reason = request.form.get("override_reason", "").strip() or None
        customer_id = request.form.get("customer_id") or None
        quantity = int(request.form["quantity"])
        remarks = request.form.get("remarks")

        if not grade:
            flash("Please select a grade.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_batch"))

        if not rod_id:
            flash("Please select a rod with enough length available.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_batch"))

        cursor.execute("BEGIN IMMEDIATE")
        suggested_rod_id = suggest_best_rod(tool_id, quantity, grade, cursor)
        if suggested_rod_id and str(suggested_rod_id) != str(rod_id) and not override_reason:
            flash("You've picked a different rod than the suggested best fit - please give a reason.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_batch"))

        cursor.execute("SELECT overall_length_mm FROM mfg_tools WHERE id = ?", (tool_id,))
        tool_row = cursor.fetchone()
        tool_length = (tool_row["overall_length_mm"] or 0) if tool_row else 0
        length_needed = (tool_length + CUTTING_ALLOWANCE_MM) * quantity

        cursor.execute("SELECT remaining_length_mm FROM mfg_rods WHERE id = ?", (rod_id,))
        rod_row = cursor.fetchone()
        remaining = rod_row["remaining_length_mm"] if rod_row else 0

        if length_needed > remaining:
            flash(f"This needs {length_needed:.1f}mm of rod but only {remaining:.1f}mm is left - reduce the quantity or pick a different rod.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_batch"))

        batch_no = generate_batch_no(batch_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_job_batches (batch_no, batch_date, tool_id, rod_id, customer_id, quantity, length_needed_mm, rod_override_reason, remarks)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (batch_no, batch_date, tool_id, rod_id, customer_id, quantity, length_needed, override_reason, remarks))
        new_batch_id = cursor.lastrowid
        cursor.execute("UPDATE mfg_rods SET remaining_length_mm = remaining_length_mm - ? WHERE id = ?", (length_needed, rod_id))
        cursor.execute("""
            INSERT INTO mfg_stock_movements (item_type, item_id, movement_type, quantity, source_type, source_id, source_no, movement_date)
            VALUES (?,?,?,?,?,?,?,?)
        """, ("rod", rod_id, "out", length_needed, "batch_reserve", new_batch_id, batch_no, batch_date))
        conn.commit()
        conn.close()
        flash(f"Batch {batch_no} created successfully! ({length_needed:.1f}mm reserved from the rod)", "success")
        return redirect(url_for("manufacturing.batches_list"))

    cursor.execute("SELECT id, item_code, tool_name, tool_type, overall_length_mm, customer_id FROM mfg_tools ORDER BY tool_name")
    tools = cursor.fetchall()
    cursor.execute("SELECT id, rod_date, grade, diameter_mm, remaining_length_mm FROM mfg_rods WHERE remaining_length_mm > 0 ORDER BY rod_date DESC")
    rods = cursor.fetchall()
    cursor.execute("SELECT DISTINCT grade FROM mfg_rods WHERE remaining_length_mm > 0 AND grade IS NOT NULL AND grade != '' ORDER BY grade")
    grades = [row["grade"] for row in cursor.fetchall()]
    cursor.execute("SELECT id, name FROM customers ORDER BY name")
    customers = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/add_batch.html", tools=tools, rods=rods, grades=grades, customers=customers, cutting_allowance=CUTTING_ALLOWANCE_MM)
@manufacturing.route("/manufacturing/batches/<int:batch_id>")
def batch_detail(batch_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_job_batches.*, mfg_tools.tool_name, mfg_tools.tool_type,
               mfg_rods.grade as rod_grade, mfg_rods.diameter_mm as rod_diameter,
               mfg_rods.remaining_length_mm as rod_remaining_length_mm,
               mfg_rods.rate as rod_rate, mfg_rods.length_mm as rod_length_mm,
               customers.name as customer_name
        FROM mfg_job_batches
        JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
        LEFT JOIN mfg_rods ON mfg_job_batches.rod_id = mfg_rods.id
        LEFT JOIN customers ON mfg_job_batches.customer_id = customers.id
        WHERE mfg_job_batches.id = ?
    """, (batch_id,))
    batch = cursor.fetchone()

    cursor.execute("""
        SELECT mfg_job_stages.*, suppliers.name as vendor_name, mfg_purchase_invoices.invoice_no as vendor_invoice_no
        FROM mfg_job_stages
        LEFT JOIN suppliers ON mfg_job_stages.vendor_id = suppliers.id
        LEFT JOIN mfg_purchase_invoices ON mfg_job_stages.purchase_invoice_id = mfg_purchase_invoices.id
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
    not_started = [s for s in STAGES if s["key"] not in covered_keys]

    rod_cost = None
    if batch["rod_id"] and batch["rod_length_mm"] and batch["length_needed_mm"]:
        rod_cost = round((batch["length_needed_mm"] / batch["rod_length_mm"]) * (batch["rod_rate"] or 0), 2)
    total_cost = round((rod_cost or 0) + sum(op["rate"] for op in operations if op["rate"]), 2)

    cursor.execute("""
        SELECT mfg_challan_items.job_stage_id, mfg_challan_items.challan_id, mfg_challans.challan_no
        FROM mfg_challan_items
        JOIN mfg_challans ON mfg_challan_items.challan_id = mfg_challans.id
        WHERE mfg_challan_items.batch_id = ? AND mfg_challan_items.job_stage_id IS NOT NULL
    """, (batch_id,))
    op_challans = {r["job_stage_id"]: {"id": r["challan_id"], "no": r["challan_no"]} for r in cursor.fetchall()}

    cursor.execute("""
        SELECT mfg_challans.*
        FROM mfg_challans
        JOIN mfg_challan_items ON mfg_challan_items.challan_id = mfg_challans.id
        WHERE mfg_challan_items.batch_id = ? AND mfg_challans.challan_type = 'customer_delivery'
        ORDER BY mfg_challans.id
    """, (batch_id,))
    customer_challans = cursor.fetchall()

    cursor.execute("""
        SELECT mfg_invoices.*
        FROM mfg_invoices
        JOIN mfg_invoice_items ON mfg_invoice_items.invoice_id = mfg_invoices.id
        WHERE mfg_invoice_items.batch_id = ?
        ORDER BY mfg_invoices.id
    """, (batch_id,))
    invoices = cursor.fetchall()
    conn.close()

    def op_view(op):
        stage_keys = op["stages"].split(",")
        return {
            "id": op["id"], "stage_keys": stage_keys, "label": stage_label_for(stage_keys),
            "vendor_name": op["vendor_name"], "vendor_id": op["vendor_id"], "dispatch_date": op["dispatch_date"],
            "quantity_sent": op["quantity_sent"], "receive_date": op["receive_date"],
            "quantity_received": op["quantity_received"], "rod_returned_length_mm": op["rod_returned_length_mm"],
            "rate": op["rate"], "challan": op_challans.get(op["id"]),
            "purchase_invoice_id": op["purchase_invoice_id"], "vendor_invoice_no": op["vendor_invoice_no"],
        }

    pending_ops = [op_view(op) for op in operations if not op["receive_date"]]
    done_ops = [op_view(op) for op in operations if op["receive_date"]]
    blank_making_pending = any("blank_making" in op["stage_keys"] for op in pending_ops)
    batch_untouched = not operations and not customer_challans and not invoices

    return render_template("manufacturing/batch_detail.html",
        batch=batch, suppliers=suppliers, transporters=transporters, not_started=not_started,
        pending_ops=pending_ops, done_ops=done_ops,
        blank_making_pending=blank_making_pending,
        rod_cost=rod_cost, total_cost=total_cost,
        customer_challans=customer_challans, invoices=invoices,
        batch_untouched=batch_untouched)

@manufacturing.route("/manufacturing/batches/<int:batch_id>/stage/<int:stage_id>/invoice-vendor", methods=["GET", "POST"])
def create_vendor_invoice(batch_id, stage_id):
    """Turns a vendor job-work stage's cost into a real Purchase Invoice (payable) - manual,
    pre-filled from the stage's own rate/qty so it can be confirmed/adjusted against the
    vendor's actual bill before saving, not created automatically the moment a stage is
    received. Goes into the same mfg_purchase_invoices table Purchase Payments/Outstanding/
    Purchase Report already read from - no separate wiring needed for it to show up there."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_job_stages.*, mfg_job_batches.batch_no, suppliers.name as vendor_name
        FROM mfg_job_stages
        JOIN mfg_job_batches ON mfg_job_stages.batch_id = mfg_job_batches.id
        LEFT JOIN suppliers ON mfg_job_stages.vendor_id = suppliers.id
        WHERE mfg_job_stages.id = ? AND mfg_job_stages.batch_id = ?
    """, (stage_id, batch_id))
    stage = cursor.fetchone()

    if not stage or not stage["vendor_id"]:
        flash("Cannot create a purchase invoice - no vendor recorded for this stage.", "danger")
        conn.close()
        return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))
    if stage["purchase_invoice_id"]:
        flash("This stage is already linked to a purchase invoice.", "danger")
        conn.close()
        return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))

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
        cursor.execute("UPDATE mfg_job_stages SET purchase_invoice_id = ? WHERE id = ?", (invoice_id, stage_id))
        conn.commit()
        conn.close()
        flash(f"Purchase Invoice {invoice_no} created for {stage['vendor_name']}!", "success")
        return redirect(url_for("manufacturing.view_purchase_invoice", invoice_id=invoice_id))

    conn.close()
    default_description = f"Job-work: {stage_label_for(stage['stages'].split(','))} - Batch {stage['batch_no']}"
    return render_template("manufacturing/add_vendor_service_invoice.html",
        vendor_name=stage["vendor_name"], default_description=default_description,
        default_quantity=stage["quantity_received"] or stage["quantity_sent"], default_rate=stage["rate"] or 0,
        form_action=f"/manufacturing/batches/{batch_id}/stage/{stage_id}/invoice-vendor",
        back_url=f"/manufacturing/batches/{batch_id}")


def _batch_is_untouched(cursor, batch_id):
    cursor.execute("SELECT COUNT(*) as c FROM mfg_job_stages WHERE batch_id=?", (batch_id,))
    if cursor.fetchone()["c"] > 0:
        return False
    cursor.execute("SELECT COUNT(*) as c FROM mfg_challan_items WHERE batch_id=?", (batch_id,))
    if cursor.fetchone()["c"] > 0:
        return False
    cursor.execute("SELECT COUNT(*) as c FROM mfg_invoice_items WHERE batch_id=?", (batch_id,))
    if cursor.fetchone()["c"] > 0:
        return False
    return True

@manufacturing.route("/manufacturing/batches/<int:batch_id>/edit", methods=["GET", "POST"])
def edit_batch(batch_id):
    conn = get_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("SELECT * FROM mfg_job_batches WHERE id = ?", (batch_id,))
        batch = cursor.fetchone()
        untouched = _batch_is_untouched(cursor, batch_id)
        customer_id = request.form.get("customer_id") or None
        remarks = request.form.get("remarks")

        if untouched and request.form.get("tool_id"):
            tool_id = request.form["tool_id"]
            rod_id = request.form.get("rod_id")
            quantity = int(request.form["quantity"])
            if not rod_id:
                flash("Please select a rod with enough length available.", "danger")
                conn.close()
                return redirect(url_for("manufacturing.edit_batch", batch_id=batch_id))

            cursor.execute("SELECT overall_length_mm FROM mfg_tools WHERE id = ?", (tool_id,))
            tool_row = cursor.fetchone()
            tool_length = (tool_row["overall_length_mm"] or 0) if tool_row else 0
            length_needed = (tool_length + CUTTING_ALLOWANCE_MM) * quantity

            cursor.execute("SELECT remaining_length_mm FROM mfg_rods WHERE id = ?", (rod_id,))
            rod_row = cursor.fetchone()
            available = (rod_row["remaining_length_mm"] if rod_row else 0)
            if str(rod_id) == str(batch["rod_id"]):
                available += batch["length_needed_mm"]
            if length_needed > available:
                flash(f"This needs {length_needed:.1f}mm of rod but only {available:.1f}mm would be available - reduce the quantity or pick a different rod.", "danger")
                conn.close()
                return redirect(url_for("manufacturing.edit_batch", batch_id=batch_id))

            cursor.execute("UPDATE mfg_rods SET remaining_length_mm = remaining_length_mm + ? WHERE id = ?", (batch["length_needed_mm"], batch["rod_id"]))
            cursor.execute("DELETE FROM mfg_stock_movements WHERE source_type='batch_reserve' AND source_id=?", (batch_id,))
            cursor.execute("UPDATE mfg_rods SET remaining_length_mm = remaining_length_mm - ? WHERE id = ?", (length_needed, rod_id))
            cursor.execute("""
                INSERT INTO mfg_stock_movements (item_type, item_id, movement_type, quantity, source_type, source_id, source_no, movement_date)
                VALUES (?,?,?,?,?,?,?,?)
            """, ("rod", rod_id, "out", length_needed, "batch_reserve", batch_id, batch["batch_no"], batch["batch_date"]))
            cursor.execute("""
                UPDATE mfg_job_batches SET tool_id=?, rod_id=?, quantity=?, length_needed_mm=?, customer_id=?, remarks=?
                WHERE id=?
            """, (tool_id, rod_id, quantity, length_needed, customer_id, remarks, batch_id))
            conn.commit()
            conn.close()
            flash("Batch updated successfully!", "success")
            return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))

        cursor.execute("UPDATE mfg_job_batches SET customer_id=?, remarks=? WHERE id=?", (customer_id, remarks, batch_id))
        conn.commit()
        conn.close()
        flash("Batch updated successfully!", "success")
        return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))

    cursor.execute("SELECT * FROM mfg_job_batches WHERE id = ?", (batch_id,))
    batch = cursor.fetchone()
    untouched = _batch_is_untouched(cursor, batch_id)
    cursor.execute("SELECT id, name FROM customers ORDER BY name")
    customers = cursor.fetchall()
    tools, rods = [], []
    if untouched:
        cursor.execute("SELECT id, item_code, tool_name, tool_type, overall_length_mm, customer_id FROM mfg_tools ORDER BY tool_name")
        tools = cursor.fetchall()
        cursor.execute("SELECT id, rod_date, grade, diameter_mm, remaining_length_mm FROM mfg_rods WHERE remaining_length_mm > 0 OR id = ? ORDER BY rod_date DESC", (batch["rod_id"],))
        rods = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/edit_batch.html", batch=batch, customers=customers,
        untouched=untouched, tools=tools, rods=rods, cutting_allowance=CUTTING_ALLOWANCE_MM)

@manufacturing.route("/manufacturing/batches/delete/<int:batch_id>")
def delete_batch(batch_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_job_batches WHERE id=?", (batch_id,))
    batch = cursor.fetchone()
    if not batch:
        conn.close()
        flash("Batch not found.", "danger")
        return redirect(url_for("manufacturing.batches_list"))
    if not _batch_is_untouched(cursor, batch_id):
        conn.close()
        flash("Cannot delete this batch - work has already started (a stage, delivery challan, or invoice references it).", "danger")
        return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))

    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute("UPDATE mfg_rods SET remaining_length_mm = remaining_length_mm + ? WHERE id = ?", (batch["length_needed_mm"], batch["rod_id"]))
    cursor.execute("DELETE FROM mfg_stock_movements WHERE source_type='batch_reserve' AND source_id=?", (batch_id,))
    cursor.execute("DELETE FROM mfg_job_batches WHERE id=?", (batch_id,))
    conn.commit()
    conn.close()
    flash("Batch deleted successfully! The reserved rod length has been returned to stock.", "danger")
    return redirect(url_for("manufacturing.batches_list"))

@manufacturing.route("/manufacturing/batches/<int:batch_id>/send", methods=["POST"])
def send_operation(batch_id):
    stages = request.form.getlist("stages")
    vendor_id = request.form.get("vendor_id") or None
    transporter_id = request.form.get("transporter_id") or None
    dispatch_date = request.form["dispatch_date"]
    quantity_sent = int(request.form["quantity_sent"])
    remarks = request.form.get("remarks")

    if not stages:
        flash("Please select at least one stage to send.", "danger")
        return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute("SELECT quantity FROM mfg_job_batches WHERE id = ?", (batch_id,))
    batch_row = cursor.fetchone()
    if batch_row and quantity_sent > batch_row["quantity"]:
        conn.close()
        flash(f"Cannot send {quantity_sent} - the batch only has {batch_row['quantity']} pieces.", "danger")
        return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))

    cursor.execute("SELECT id FROM mfg_job_stages WHERE batch_id = ? AND receive_date IS NULL", (batch_id,))
    if cursor.fetchone():
        conn.close()
        flash("This batch already has a shipment in progress - receive it before sending another.", "danger")
        return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))

    cursor.execute("""
        INSERT INTO mfg_job_stages (batch_id, stages, vendor_id, dispatch_date, quantity_sent, remarks)
        VALUES (?,?,?,?,?,?)
    """, (batch_id, ",".join(stages), vendor_id, dispatch_date, quantity_sent, remarks))
    job_stage_id = cursor.lastrowid

    if vendor_id:
        challan_no = generate_challan_no("vendor_jobwork", dispatch_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_challans (challan_no, challan_date, challan_type, party_type, party_id, transporter_id, remarks)
            VALUES (?,?,?,?,?,?,?)
        """, (challan_no, dispatch_date, "vendor_jobwork", "supplier", vendor_id, transporter_id, remarks))
        challan_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO mfg_challan_items (challan_id, batch_id, job_stage_id, quantity)
            VALUES (?,?,?,?)
        """, (challan_id, batch_id, job_stage_id, quantity_sent))
        conn.commit()
        conn.close()
        return redirect(url_for("manufacturing.print_challan", challan_id=challan_id))

    conn.commit()
    conn.close()
    flash("Sent out for " + stage_label_for(stages) + "!", "success")
    return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))

@manufacturing.route("/manufacturing/batches/<int:batch_id>/receive/<int:job_stage_id>", methods=["POST"])
def receive_operation(batch_id, job_stage_id):
    receive_date = request.form["receive_date"]
    quantity_received = int(request.form["quantity_received"])
    rod_returned_length_mm = request.form.get("rod_returned_length_mm") or None
    rate = request.form.get("rate") or None

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT quantity_sent FROM mfg_job_stages WHERE id = ?", (job_stage_id,))
    stage_row = cursor.fetchone()
    if stage_row and stage_row["quantity_sent"] and quantity_received > stage_row["quantity_sent"]:
        conn.close()
        flash(f"Cannot receive {quantity_received} - only {stage_row['quantity_sent']} were sent.", "danger")
        return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))

    cursor.execute("""
        UPDATE mfg_job_stages SET receive_date=?, quantity_received=?, rod_returned_length_mm=?, rate=?
        WHERE id=?
    """, (receive_date, quantity_received, rod_returned_length_mm, rate, job_stage_id))

    if rod_returned_length_mm:
        cursor.execute("SELECT stages FROM mfg_job_stages WHERE id = ?", (job_stage_id,))
        op_row = cursor.fetchone()
        if op_row and "blank_making" in op_row["stages"].split(","):
            cursor.execute("SELECT rod_id, length_needed_mm FROM mfg_job_batches WHERE id = ?", (batch_id,))
            batch_row = cursor.fetchone()
            if batch_row and batch_row["rod_id"] and batch_row["length_needed_mm"]:
                # rod_returned_length_mm is the actual rod length THIS batch consumed, compared
                # against its own estimate (length_needed_mm). Adjust the rod's remaining stock
                # by the difference rather than overwriting it - an overwrite would erase other
                # batches' reservations on a rod that's shared across multiple batches.
                variance = float(rod_returned_length_mm) - batch_row["length_needed_mm"]
                if variance != 0:
                    cursor.execute("UPDATE mfg_rods SET remaining_length_mm = remaining_length_mm - ? WHERE id = ?", (variance, batch_row["rod_id"]))

    cursor.execute("SELECT stages, receive_date FROM mfg_job_stages WHERE batch_id = ?", (batch_id,))
    all_ops = cursor.fetchall()
    covered = set()
    for op in all_ops:
        covered.update(op["stages"].split(","))
    now_complete = len(covered) == len(STAGES) and all(op["receive_date"] for op in all_ops)
    if now_complete:
        cursor.execute("SELECT batch_no, tool_id, quantity FROM mfg_job_batches WHERE id = ?", (batch_id,))
        batch_row = cursor.fetchone()
        cursor.execute("""
            INSERT INTO mfg_stock_movements (item_type, item_id, movement_type, quantity, source_type, source_id, source_no, movement_date)
            VALUES (?,?,?,?,?,?,?,?)
        """, ("tool", batch_row["tool_id"], "in", batch_row["quantity"], "batch_complete", batch_id, batch_row["batch_no"], receive_date))

    conn.commit()
    conn.close()
    flash("Marked as received!", "success")
    return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))


# ============================================
# CHALLANS
# ============================================

@manufacturing.route("/manufacturing/batches/<int:batch_id>/generate-challan/<int:job_stage_id>", methods=["POST"])
def generate_challan(batch_id, job_stage_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_job_stages WHERE id = ?", (job_stage_id,))
    op = cursor.fetchone()

    if not op or not op["vendor_id"]:
        flash("Cannot generate challan - no vendor recorded for this shipment.", "danger")
        conn.close()
        return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))

    cursor.execute("BEGIN IMMEDIATE")
    challan_no = generate_challan_no("vendor_jobwork", op["dispatch_date"], cursor)
    cursor.execute("""
        INSERT INTO mfg_challans (challan_no, challan_date, challan_type, party_type, party_id, remarks)
        VALUES (?,?,?,?,?,?)
    """, (challan_no, op["dispatch_date"], "vendor_jobwork", "supplier", op["vendor_id"], op["remarks"]))
    challan_id = cursor.lastrowid
    cursor.execute("""
        INSERT INTO mfg_challan_items (challan_id, batch_id, job_stage_id, quantity)
        VALUES (?,?,?,?)
    """, (challan_id, batch_id, job_stage_id, op["quantity_sent"]))

    conn.commit()
    conn.close()
    flash(f"Challan {challan_no} generated!", "success")
    return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))

@manufacturing.route("/manufacturing/challans/pick-batch-delivery")
def pick_batch_for_delivery():
    """Entry point for '+ New Delivery Challan' from the Challans list. Delivery draws from
    a per-TOOL finished pool (compute_tool_stock's finished_in_hand), not a per-batch amount -
    add_customer_challan() already enforces that check itself, so this page just needs to
    surface batches worth picking (has a customer, and that tool still has stock in hand);
    it can't bypass or duplicate the real check."""
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    q = request.args.get("q", "")
    q_search = q.strip()

    conn = get_connection()
    cursor = conn.cursor()

    where_parts = ["mfg_job_batches.customer_id IS NOT NULL"]
    params = []
    if q_search:
        where_parts.append("(mfg_job_batches.batch_no LIKE ? OR mfg_tools.tool_name LIKE ? OR customers.name LIKE ?)")
        params += [f"%{q_search}%", f"%{q_search}%", f"%{q_search}%"]
    where = "WHERE " + " AND ".join(where_parts)

    cursor.execute(f"""
        SELECT mfg_job_batches.id, mfg_job_batches.batch_no, mfg_tools.id as tool_id, mfg_tools.tool_name, mfg_tools.tool_type,
               customers.name as customer_name
        FROM mfg_job_batches
        JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
        JOIN customers ON mfg_job_batches.customer_id = customers.id
        {where}
        ORDER BY mfg_job_batches.batch_no DESC
    """, params)
    candidates = cursor.fetchall()

    rows_all = []
    stock_cache = {}
    for b in candidates:
        if b["tool_id"] not in stock_cache:
            stock_cache[b["tool_id"]] = compute_tool_stock(cursor, b["tool_id"])["finished_in_hand"]
        finished = stock_cache[b["tool_id"]]
        if finished > 0:
            rows_all.append({"id": b["id"], "batch_no": b["batch_no"], "tool_name": b["tool_name"],
                              "tool_type": b["tool_type"], "customer_name": b["customer_name"], "finished_in_hand": finished})

    total = len(rows_all)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    rows = rows_all[offset:offset + per_page]

    conn.close()
    return render_template("manufacturing/pick_batch_for_delivery.html", rows=rows, page=page, total_pages=total_pages, total=total, q=q)


@manufacturing.route("/manufacturing/challans")
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
        where_parts.append("(mfg_challans.challan_no LIKE ? OR suppliers.name LIKE ? OR customers.name LIKE ?)")
        params += [f"%{q_search}%", f"%{q_search}%", f"%{q_search}%"]
    if challan_type:
        where_parts.append("mfg_challans.challan_type = ?")
        params.append(challan_type)
    if from_date:
        where_parts.append("mfg_challans.challan_date >= ?")
        params.append(from_date)
    if to_date:
        where_parts.append("mfg_challans.challan_date <= ?")
        params.append(to_date)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    cursor.execute(f"""
        SELECT mfg_challans.*,
               CASE WHEN party_type = 'supplier' THEN suppliers.name ELSE customers.name END as party_name
        FROM mfg_challans
        LEFT JOIN suppliers ON mfg_challans.party_type = 'supplier' AND mfg_challans.party_id = suppliers.id
        LEFT JOIN customers ON mfg_challans.party_type = 'customer' AND mfg_challans.party_id = customers.id
        {where}
        ORDER BY mfg_challans.challan_date DESC, mfg_challans.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    challans = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_challans
        LEFT JOIN suppliers ON mfg_challans.party_type = 'supplier' AND mfg_challans.party_id = suppliers.id
        LEFT JOIN customers ON mfg_challans.party_type = 'customer' AND mfg_challans.party_id = customers.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    conn.close()
    return render_template("manufacturing/challans.html", challans=challans, page=page, total_pages=total_pages, total=total, q=q, challan_type=challan_type, from_date=from_date, to_date=to_date)

@manufacturing.route("/manufacturing/challans/<int:challan_id>")
def view_challan(challan_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_challans WHERE id = ?", (challan_id,))
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
        SELECT mfg_challan_items.*, mfg_job_batches.batch_no, mfg_tools.tool_name, mfg_tools.tool_type,
               mfg_job_stages.stages as op_stages
        FROM mfg_challan_items
        JOIN mfg_job_batches ON mfg_challan_items.batch_id = mfg_job_batches.id
        JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
        LEFT JOIN mfg_job_stages ON mfg_challan_items.job_stage_id = mfg_job_stages.id
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
    delete_blocked_reason = _challan_delete_guard(conn2.cursor(), challan)
    conn2.close()

    return render_template("manufacturing/view_challan.html", challan=challan, party=party, items=items,
        transporter=transporter, delete_blocked_reason=delete_blocked_reason)

@manufacturing.route("/manufacturing/challans/<int:challan_id>/edit", methods=["GET", "POST"])
def edit_challan(challan_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_challans WHERE id = ?", (challan_id,))
    challan = cursor.fetchone()
    if not challan:
        conn.close()
        flash("Challan not found.", "danger")
        return redirect(url_for("manufacturing.challans_list"))

    if request.method == "POST":
        challan_date = request.form["challan_date"]
        transporter_id = request.form.get("transporter_id") or None
        remarks = request.form.get("remarks")
        cursor.execute("UPDATE mfg_challans SET challan_date=?, transporter_id=?, remarks=? WHERE id=?",
                       (challan_date, transporter_id, remarks, challan_id))
        conn.commit()
        conn.close()
        flash("Challan updated successfully!", "success")
        return redirect(url_for("manufacturing.view_challan", challan_id=challan_id))

    cursor.execute("SELECT id, name FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/edit_challan.html", challan=challan, transporters=transporters)

def _challan_delete_guard(cursor, challan):
    """Returns an error message if this challan can't be deleted yet, else None."""
    cursor.execute("SELECT * FROM mfg_challan_items WHERE challan_id = ?", (challan["id"],))
    items = cursor.fetchall()
    if challan["challan_type"] == "customer_delivery":
        for item in items:
            cursor.execute("SELECT COALESCE(SUM(quantity), 0) as total FROM mfg_invoice_items WHERE batch_id = ?", (item["batch_id"],))
            if cursor.fetchone()["total"] > 0:
                return "Cannot delete - this batch has already been invoiced, so we can't tell how much of that invoice depended on this delivery."
    elif challan["challan_type"] == "vendor_jobwork":
        for item in items:
            if not item["job_stage_id"]:
                continue
            cursor.execute("SELECT receive_date, purchase_invoice_id FROM mfg_job_stages WHERE id = ?", (item["job_stage_id"],))
            stage = cursor.fetchone()
            if stage and stage["receive_date"]:
                return "Cannot delete - this shipment has already been received back."
            if stage and stage["purchase_invoice_id"]:
                return "Cannot delete - a vendor purchase invoice already references this shipment."
    return None

@manufacturing.route("/manufacturing/challans/delete/<int:challan_id>")
def delete_challan(challan_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_challans WHERE id = ?", (challan_id,))
    challan = cursor.fetchone()
    if not challan:
        conn.close()
        flash("Challan not found.", "danger")
        return redirect(url_for("manufacturing.challans_list"))

    error = _challan_delete_guard(cursor, challan)
    if error:
        conn.close()
        flash(error, "danger")
        return redirect(url_for("manufacturing.view_challan", challan_id=challan_id))

    cursor.execute("SELECT * FROM mfg_challan_items WHERE challan_id = ?", (challan_id,))
    items = cursor.fetchall()

    cursor.execute("BEGIN IMMEDIATE")
    if challan["challan_type"] == "customer_delivery":
        cursor.execute("DELETE FROM mfg_stock_movements WHERE source_type='delivery' AND source_id=?", (challan_id,))
    elif challan["challan_type"] == "vendor_jobwork":
        for item in items:
            if item["job_stage_id"]:
                cursor.execute("DELETE FROM mfg_job_stages WHERE id = ?", (item["job_stage_id"],))
    cursor.execute("DELETE FROM mfg_challan_items WHERE challan_id = ?", (challan_id,))
    cursor.execute("DELETE FROM mfg_challans WHERE id = ?", (challan_id,))
    conn.commit()
    conn.close()
    flash("Challan deleted successfully!", "danger")
    return redirect(url_for("manufacturing.challans_list"))

@manufacturing.route("/manufacturing/batches/<int:batch_id>/challan/customer/new", methods=["GET", "POST"])
def add_customer_challan(batch_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_job_batches.*, mfg_tools.tool_name, customers.name as customer_name
        FROM mfg_job_batches
        JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
        LEFT JOIN customers ON mfg_job_batches.customer_id = customers.id
        WHERE mfg_job_batches.id = ?
    """, (batch_id,))
    batch = cursor.fetchone()

    if not batch["customer_id"]:
        flash("This batch has no customer assigned - add one before creating a delivery challan.", "danger")
        conn.close()
        return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))

    if request.method == "POST":
        challan_date = request.form["challan_date"]
        quantity = int(request.form["quantity"])
        transporter_id = request.form.get("transporter_id") or None
        remarks = request.form.get("remarks")

        available = compute_tool_stock(cursor, batch["tool_id"])["finished_in_hand"]
        if quantity > available:
            flash(f"Cannot deliver {quantity} - only {available} finished and in hand for this tool right now.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_customer_challan", batch_id=batch_id))

        cursor.execute("BEGIN IMMEDIATE")
        challan_no = generate_challan_no("customer_delivery", challan_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_challans (challan_no, challan_date, challan_type, party_type, party_id, transporter_id, remarks)
            VALUES (?,?,?,?,?,?,?)
        """, (challan_no, challan_date, "customer_delivery", "customer", batch["customer_id"], transporter_id, remarks))
        challan_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO mfg_challan_items (challan_id, batch_id, job_stage_id, quantity)
            VALUES (?,?,?,?)
        """, (challan_id, batch_id, None, quantity))
        cursor.execute("""
            INSERT INTO mfg_stock_movements (item_type, item_id, movement_type, quantity, source_type, source_id, source_no, movement_date)
            VALUES (?,?,?,?,?,?,?,?)
        """, ("tool", batch["tool_id"], "out", quantity, "delivery", challan_id, challan_no, challan_date))
        conn.commit()
        conn.close()
        flash(f"Delivery Challan {challan_no} created!", "success")
        return redirect(url_for("manufacturing.view_challan", challan_id=challan_id))

    cursor.execute("SELECT id, name FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/add_customer_challan.html", batch=batch, transporters=transporters)


@manufacturing.route("/manufacturing/challans/<int:challan_id>/print")
def print_challan(challan_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_challans WHERE id = ?", (challan_id,))
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
        SELECT mfg_challan_items.*, mfg_job_batches.batch_no, mfg_tools.tool_name, mfg_tools.tool_type,
               mfg_job_stages.stages as op_stages
        FROM mfg_challan_items
        JOIN mfg_job_batches ON mfg_challan_items.batch_id = mfg_job_batches.id
        JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
        LEFT JOIN mfg_job_stages ON mfg_challan_items.job_stage_id = mfg_job_stages.id
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

    doc_title = "Job Work Challan" if challan["challan_type"] == "vendor_jobwork" else "Delivery Challan"
    batch_id = item_rows[0]["batch_id"] if item_rows else None

    return render_template("manufacturing/print_document.html",
        doc_title=doc_title, doc_no=challan["challan_no"], doc_date=challan["challan_date"],
        party=party, items=items, show_pricing=False, transporter=transporter,
        subtotal=None, tax_amount=None, total=None, remarks=challan["remarks"],
        back_url=(f"/manufacturing/batches/{batch_id}" if batch_id else None))


@manufacturing.route("/manufacturing/invoices/pick-batch")
def pick_batch_for_invoice():
    """Entry point for '+ New Invoice' from the Invoices list - lets you find the batch to
    bill by tool/customer/batch no instead of drilling into it first. Just navigates to the
    exact same add_invoice(batch_id) route/form used from the batch page, so the
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
            SELECT mfg_job_batches.id, mfg_job_batches.batch_no, mfg_tools.tool_name, mfg_tools.tool_type,
                   customers.name as customer_name,
                   COALESCE((SELECT SUM(mfg_challan_items.quantity) FROM mfg_challan_items
                             JOIN mfg_challans ON mfg_challan_items.challan_id = mfg_challans.id
                             WHERE mfg_challan_items.batch_id = mfg_job_batches.id AND mfg_challans.challan_type = 'customer_delivery'), 0) as delivered_qty,
                   COALESCE((SELECT SUM(quantity) FROM mfg_invoice_items WHERE batch_id = mfg_job_batches.id), 0) as invoiced_qty
            FROM mfg_job_batches
            JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
            LEFT JOIN customers ON mfg_job_batches.customer_id = customers.id
            WHERE mfg_job_batches.customer_id IS NOT NULL
        )
        WHERE (delivered_qty - invoiced_qty) > 0
        {where}
        ORDER BY batch_no DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    rows = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT mfg_job_batches.id, mfg_job_batches.batch_no, mfg_tools.tool_name, customers.name as customer_name,
                   COALESCE((SELECT SUM(mfg_challan_items.quantity) FROM mfg_challan_items
                             JOIN mfg_challans ON mfg_challan_items.challan_id = mfg_challans.id
                             WHERE mfg_challan_items.batch_id = mfg_job_batches.id AND mfg_challans.challan_type = 'customer_delivery'), 0) as delivered_qty,
                   COALESCE((SELECT SUM(quantity) FROM mfg_invoice_items WHERE batch_id = mfg_job_batches.id), 0) as invoiced_qty
            FROM mfg_job_batches
            JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
            LEFT JOIN customers ON mfg_job_batches.customer_id = customers.id
            WHERE mfg_job_batches.customer_id IS NOT NULL
        )
        WHERE (delivered_qty - invoiced_qty) > 0
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    conn.close()
    return render_template("manufacturing/pick_batch_for_invoice.html", rows=rows, page=page, total_pages=total_pages, total=total, q=q)


@manufacturing.route("/manufacturing/invoices")
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
        where_parts.append("(mfg_invoices.invoice_no LIKE ? OR customers.name LIKE ?)")
        params += [f"%{q_search}%", f"%{q_search}%"]
    if from_date:
        where_parts.append("mfg_invoices.invoice_date >= ?")
        params.append(from_date)
    if to_date:
        where_parts.append("mfg_invoices.invoice_date <= ?")
        params.append(to_date)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    cursor.execute(f"""
        SELECT mfg_invoices.*, customers.name as customer_name
        FROM mfg_invoices
        LEFT JOIN customers ON mfg_invoices.customer_id = customers.id
        {where}
        ORDER BY mfg_invoices.invoice_date DESC, mfg_invoices.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    invoices = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_invoices
        LEFT JOIN customers ON mfg_invoices.customer_id = customers.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    conn.close()
    return render_template("manufacturing/invoices.html", invoices=invoices, page=page, total_pages=total_pages, total=total, q=q, from_date=from_date, to_date=to_date)

@manufacturing.route("/manufacturing/invoices/<int:invoice_id>")
def view_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_invoices WHERE id = ?", (invoice_id,))
    invoice = cursor.fetchone()
    cursor.execute("SELECT * FROM customers WHERE id = ?", (invoice["customer_id"],))
    customer = cursor.fetchone()
    cursor.execute("""
        SELECT mfg_invoice_items.*, mfg_job_batches.batch_no, mfg_tools.tool_name, mfg_tools.tool_type
        FROM mfg_invoice_items
        JOIN mfg_job_batches ON mfg_invoice_items.batch_id = mfg_job_batches.id
        JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
        WHERE invoice_id = ?
    """, (invoice_id,))
    items = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) as c FROM mfg_sales_payments WHERE invoice_id = ?", (invoice_id,))
    delete_blocked = cursor.fetchone()["c"] > 0
    conn.close()
    return render_template("manufacturing/view_invoice.html", invoice=invoice, customer=customer, items=items, delete_blocked=delete_blocked)

@manufacturing.route("/manufacturing/invoices/<int:invoice_id>/edit", methods=["GET", "POST"])
def edit_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_invoices WHERE id = ?", (invoice_id,))
    invoice = cursor.fetchone()
    if not invoice:
        conn.close()
        flash("Invoice not found.", "danger")
        return redirect(url_for("manufacturing.invoices_list"))
    cursor.execute("SELECT * FROM mfg_invoice_items WHERE invoice_id = ?", (invoice_id,))
    item = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) as c FROM mfg_sales_payments WHERE invoice_id = ?", (invoice_id,))
    untouched = cursor.fetchone()["c"] == 0

    if request.method == "POST":
        remarks = request.form.get("remarks")
        cr_days = int(request.form.get("cr_days") or 0)
        due_date = None
        if cr_days:
            from datetime import date, timedelta
            due_date = (date.fromisoformat(invoice["invoice_date"]) + timedelta(days=cr_days)).isoformat()

        if untouched and request.form.get("quantity"):
            quantity = float(request.form["quantity"])
            rate = float(request.form["rate"])
            tax_rate = float(request.form.get("tax_rate") or 18)

            cursor.execute("""
                SELECT COALESCE(SUM(mfg_challan_items.quantity), 0) as total
                FROM mfg_challan_items
                JOIN mfg_challans ON mfg_challan_items.challan_id = mfg_challans.id
                WHERE mfg_challan_items.batch_id = ? AND mfg_challans.challan_type = 'customer_delivery'
            """, (item["batch_id"],))
            delivered_qty = cursor.fetchone()["total"]
            cursor.execute("SELECT COALESCE(SUM(quantity), 0) as total FROM mfg_invoice_items WHERE batch_id = ? AND invoice_id != ?", (item["batch_id"], invoice_id))
            already_invoiced_qty = cursor.fetchone()["total"]
            available_to_invoice = delivered_qty - already_invoiced_qty
            if quantity > available_to_invoice:
                flash(f"Cannot invoice {quantity} - only {available_to_invoice} delivered and not yet invoiced for this batch.", "danger")
                conn.close()
                return redirect(url_for("manufacturing.edit_invoice", invoice_id=invoice_id))

            bef_tax_amt = round(quantity * rate, 2)
            tax_amount = round(bef_tax_amt * tax_rate / 100, 2)
            amount = round(bef_tax_amt + tax_amount, 2)

            cursor.execute("BEGIN IMMEDIATE")
            if item["order_item_id"]:
                cursor.execute("UPDATE mfg_customer_order_items SET billed_qty = billed_qty - ? WHERE id = ?", (item["quantity"], item["order_item_id"]))
                cursor.execute("UPDATE mfg_customer_order_items SET billed_qty = billed_qty + ? WHERE id = ?", (quantity, item["order_item_id"]))
            cursor.execute("""
                UPDATE mfg_invoice_items SET quantity=?, rate=?, tax_rate=?, bef_tax_amt=?, tax_amount=?, amount=?
                WHERE id=?
            """, (quantity, rate, tax_rate, bef_tax_amt, tax_amount, amount, item["id"]))
            cursor.execute("UPDATE mfg_invoices SET cr_days=?, due_date=?, remarks=?, total_amount=? WHERE id=?",
                           (cr_days, due_date, remarks, amount, invoice_id))
            conn.commit()
            conn.close()
            flash("Invoice updated successfully!", "success")
            return redirect(url_for("manufacturing.view_invoice", invoice_id=invoice_id))

        cursor.execute("UPDATE mfg_invoices SET cr_days=?, due_date=?, remarks=? WHERE id=?", (cr_days, due_date, remarks, invoice_id))
        conn.commit()
        conn.close()
        flash("Invoice updated successfully!", "success")
        return redirect(url_for("manufacturing.view_invoice", invoice_id=invoice_id))

    conn.close()
    return render_template("manufacturing/edit_invoice.html", invoice=invoice, item=item, untouched=untouched)

@manufacturing.route("/manufacturing/invoices/delete/<int:invoice_id>")
def delete_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as c FROM mfg_sales_payments WHERE invoice_id = ?", (invoice_id,))
    if cursor.fetchone()["c"] > 0:
        conn.close()
        flash("Cannot delete this invoice - a payment has already been recorded against it.", "danger")
        return redirect(url_for("manufacturing.view_invoice", invoice_id=invoice_id))

    cursor.execute("SELECT * FROM mfg_invoice_items WHERE invoice_id = ?", (invoice_id,))
    items = cursor.fetchall()

    cursor.execute("BEGIN IMMEDIATE")
    for item in items:
        if item["order_item_id"]:
            cursor.execute("UPDATE mfg_customer_order_items SET billed_qty = billed_qty - ? WHERE id = ?", (item["quantity"], item["order_item_id"]))
    cursor.execute("DELETE FROM mfg_invoice_items WHERE invoice_id = ?", (invoice_id,))
    cursor.execute("DELETE FROM mfg_invoices WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()
    flash("Invoice deleted successfully!", "danger")
    return redirect(url_for("manufacturing.invoices_list"))

@manufacturing.route("/manufacturing/invoices/<int:invoice_id>/print")
def print_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_invoices WHERE id = ?", (invoice_id,))
    invoice = cursor.fetchone()
    cursor.execute("SELECT * FROM customers WHERE id = ?", (invoice["customer_id"],))
    party = cursor.fetchone()
    cursor.execute("""
        SELECT mfg_invoice_items.*, mfg_job_batches.batch_no, mfg_tools.tool_name, mfg_tools.tool_type
        FROM mfg_invoice_items
        JOIN mfg_job_batches ON mfg_invoice_items.batch_id = mfg_job_batches.id
        JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
        WHERE invoice_id = ?
    """, (invoice_id,))
    item_rows = cursor.fetchall()
    conn.close()

    items = []
    for r in item_rows:
        items.append({
            "description": f"{r['tool_name']} ({r['tool_type']})",
            "quantity": r["quantity"], "rate": r["rate"], "amount": r["bef_tax_amt"],
            "batch_no": r["batch_no"]
        })

    return render_template("manufacturing/print_document.html",
        doc_title="Tax Invoice", doc_no=invoice["invoice_no"], doc_date=invoice["invoice_date"],
        party=party, items=items, show_pricing=True,
        subtotal=round(invoice["total_amount"] - sum(i["tax_amount"] for i in item_rows), 2),
        tax_amount=round(sum(i["tax_amount"] for i in item_rows), 2),
        total=invoice["total_amount"], remarks=invoice["remarks"])

@manufacturing.route("/manufacturing/batches/<int:batch_id>/invoice/new", methods=["GET", "POST"])
def add_invoice(batch_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_job_batches.*, mfg_tools.tool_name, customers.name as customer_name
        FROM mfg_job_batches
        JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
        LEFT JOIN customers ON mfg_job_batches.customer_id = customers.id
        WHERE mfg_job_batches.id = ?
    """, (batch_id,))
    batch = cursor.fetchone()

    if not batch["customer_id"]:
        flash("This batch has no customer assigned - add one before creating an invoice.", "danger")
        conn.close()
        return redirect(url_for("manufacturing.batch_detail", batch_id=batch_id))

    if request.method == "POST":
        invoice_date = request.form["invoice_date"]
        quantity = float(request.form["quantity"])
        rate = float(request.form["rate"])
        tax_rate = float(request.form.get("tax_rate") or 18)
        remarks = request.form.get("remarks")
        order_item_id = request.form.get("order_item_id") or None
        cr_days = int(request.form.get("cr_days") or 0)
        due_date = None
        if cr_days:
            from datetime import date, timedelta
            due_date = (date.fromisoformat(invoice_date) + timedelta(days=cr_days)).isoformat()

        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("""
            SELECT COALESCE(SUM(mfg_challan_items.quantity), 0) as total
            FROM mfg_challan_items
            JOIN mfg_challans ON mfg_challan_items.challan_id = mfg_challans.id
            WHERE mfg_challan_items.batch_id = ? AND mfg_challans.challan_type = 'customer_delivery'
        """, (batch_id,))
        delivered_qty = cursor.fetchone()["total"]
        cursor.execute("SELECT COALESCE(SUM(quantity), 0) as total FROM mfg_invoice_items WHERE batch_id = ?", (batch_id,))
        already_invoiced_qty = cursor.fetchone()["total"]
        available_to_invoice = delivered_qty - already_invoiced_qty
        if quantity > available_to_invoice:
            flash(f"Cannot invoice {quantity} - only {available_to_invoice} delivered and not yet invoiced for this batch. Create a delivery challan first.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_invoice", batch_id=batch_id))

        bef_tax_amt = round(quantity * rate, 2)
        tax_amount = round(bef_tax_amt * tax_rate / 100, 2)
        amount = round(bef_tax_amt + tax_amount, 2)

        invoice_no = generate_invoice_no(invoice_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_invoices (invoice_no, invoice_date, customer_id, cr_days, due_date, remarks, total_amount)
            VALUES (?,?,?,?,?,?,?)
        """, (invoice_no, invoice_date, batch["customer_id"], cr_days, due_date, remarks, amount))
        invoice_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO mfg_invoice_items (invoice_id, batch_id, order_item_id, quantity, rate, tax_rate, bef_tax_amt, tax_amount, amount)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (invoice_id, batch_id, order_item_id, quantity, rate, tax_rate, bef_tax_amt, tax_amount, amount))

        if order_item_id:
            cursor.execute("UPDATE mfg_customer_order_items SET billed_qty = billed_qty + ? WHERE id = ?", (quantity, order_item_id))

        conn.commit()
        conn.close()
        flash(f"Invoice {invoice_no} created!", "success")
        return redirect(url_for("manufacturing.view_invoice", invoice_id=invoice_id))

    cursor.execute("""
        SELECT mfg_invoice_items.rate
        FROM mfg_invoice_items
        JOIN mfg_invoices ON mfg_invoice_items.invoice_id = mfg_invoices.id
        JOIN mfg_job_batches ON mfg_invoice_items.batch_id = mfg_job_batches.id
        WHERE mfg_invoices.customer_id = ? AND mfg_job_batches.tool_id = ?
        ORDER BY mfg_invoice_items.id DESC LIMIT 1
    """, (batch["customer_id"], batch["tool_id"]))
    last_price_row = cursor.fetchone()
    last_price = last_price_row["rate"] if last_price_row else None
    if last_price is None:
        cursor.execute("SELECT sales_price, sales_discount_percent FROM mfg_tools WHERE id = ?", (batch["tool_id"],))
        tool_price_row = cursor.fetchone()
        if tool_price_row and tool_price_row["sales_price"] is not None:
            last_price = _final_price(tool_price_row["sales_price"], tool_price_row["sales_discount_percent"])

    cursor.execute("""
        SELECT mfg_customer_order_items.id as item_id, mfg_customer_order_items.quantity, mfg_customer_order_items.billed_qty,
               mfg_customer_order_items.rate, mfg_customer_orders.order_no, mfg_customer_orders.po_no
        FROM mfg_customer_order_items
        JOIN mfg_customer_orders ON mfg_customer_order_items.order_id = mfg_customer_orders.id
        WHERE mfg_customer_orders.customer_id = ? AND mfg_customer_order_items.tool_id = ?
          AND mfg_customer_order_items.quantity > mfg_customer_order_items.billed_qty
        ORDER BY mfg_customer_orders.order_date
    """, (batch["customer_id"], batch["tool_id"]))
    pending_orders = [{
        "item_id": r["item_id"], "order_no": r["order_no"], "po_no": r["po_no"],
        "pending_qty": r["quantity"] - r["billed_qty"], "rate": r["rate"]
    } for r in cursor.fetchall()]

    conn.close()
    return render_template("manufacturing/add_invoice.html", batch=batch, last_price=last_price, pending_orders=pending_orders)


# ============================================
# CUSTOMER ORDERS
# ============================================

def generate_order_no(order_date, cursor):
    date_part = order_date.replace("-", "")
    cursor.execute("SELECT order_no FROM mfg_customer_orders WHERE order_no LIKE ?", (f"CO-{date_part}-%",))
    existing_numbers = []
    for row in cursor.fetchall():
        try:
            existing_numbers.append(int(row["order_no"].split("-")[-1]))
        except ValueError:
            pass
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"CO-{date_part}-{str(next_number).zfill(4)}"

@manufacturing.route("/manufacturing/orders")
def orders_list():
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
        where = "WHERE (mfg_customer_orders.order_no LIKE ? OR mfg_customer_orders.po_no LIKE ? OR customers.name LIKE ?)"
        params = [f"%{q_search}%", f"%{q_search}%", f"%{q_search}%"]

    cursor.execute(f"""
        SELECT mfg_customer_orders.*, customers.name as customer_name,
               (SELECT COALESCE(SUM(quantity), 0) FROM mfg_customer_order_items WHERE order_id = mfg_customer_orders.id) as total_qty,
               (SELECT COALESCE(SUM(billed_qty), 0) FROM mfg_customer_order_items WHERE order_id = mfg_customer_orders.id) as total_billed
        FROM mfg_customer_orders
        LEFT JOIN customers ON mfg_customer_orders.customer_id = customers.id
        {where}
        ORDER BY mfg_customer_orders.order_date DESC, mfg_customer_orders.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    orders = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_customer_orders
        LEFT JOIN customers ON mfg_customer_orders.customer_id = customers.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    conn.close()
    return render_template("manufacturing/orders.html", orders=orders, page=page, total_pages=total_pages, total=total, q=q)

@manufacturing.route("/manufacturing/orders/add", methods=["GET", "POST"])
def add_order():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        order_date = request.form["order_date"]
        customer_id = request.form["customer_id"]
        po_no = request.form.get("po_no")
        remarks = request.form.get("remarks")
        tool_ids = request.form.getlist("tool_id[]")
        quantities = request.form.getlist("quantity[]")
        rates = request.form.getlist("rate[]")

        items = []
        for tid, qty, rate in zip(tool_ids, quantities, rates):
            if tid and qty:
                items.append((tid, int(qty), float(rate) if rate else None))

        if not items:
            flash("Add at least one tool with a quantity.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_order"))

        cursor.execute("BEGIN IMMEDIATE")
        order_no = generate_order_no(order_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_customer_orders (order_no, order_date, customer_id, po_no, remarks)
            VALUES (?,?,?,?,?)
        """, (order_no, order_date, customer_id, po_no, remarks))
        order_id = cursor.lastrowid
        for tid, qty, rate in items:
            cursor.execute("""
                INSERT INTO mfg_customer_order_items (order_id, tool_id, quantity, rate)
                VALUES (?,?,?,?)
            """, (order_id, tid, qty, rate))
        conn.commit()
        conn.close()
        flash(f"Order {order_no} created!", "success")
        return redirect(url_for("manufacturing.orders_list"))

    cursor.execute("SELECT id, name FROM customers ORDER BY name")
    customers = cursor.fetchall()
    cursor.execute("SELECT id, item_code, tool_name, tool_type, customer_id FROM mfg_tools ORDER BY tool_name")
    tools = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/add_order.html", customers=customers, tools=tools)

@manufacturing.route("/manufacturing/orders/<int:order_id>")
def view_order(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_customer_orders.*, customers.name as customer_name
        FROM mfg_customer_orders
        LEFT JOIN customers ON mfg_customer_orders.customer_id = customers.id
        WHERE mfg_customer_orders.id = ?
    """, (order_id,))
    order = cursor.fetchone()
    cursor.execute("""
        SELECT mfg_customer_order_items.*, mfg_tools.tool_name, mfg_tools.tool_type
        FROM mfg_customer_order_items
        JOIN mfg_tools ON mfg_customer_order_items.tool_id = mfg_tools.id
        WHERE order_id = ?
    """, (order_id,))
    items = cursor.fetchall()
    delete_blocked = _order_items_referenced(cursor, order_id)
    conn.close()
    return render_template("manufacturing/view_order.html", order=order, items=items, delete_blocked=delete_blocked)

def _order_items_referenced(cursor, order_id):
    cursor.execute("""
        SELECT COUNT(*) as c FROM mfg_invoice_items
        WHERE order_item_id IN (SELECT id FROM mfg_customer_order_items WHERE order_id=?)
    """, (order_id,))
    return cursor.fetchone()["c"] > 0

@manufacturing.route("/manufacturing/orders/<int:order_id>/edit", methods=["GET", "POST"])
def edit_order(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_customer_orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()
    if not order:
        conn.close()
        flash("Order not found.", "danger")
        return redirect(url_for("manufacturing.orders_list"))
    untouched = not _order_items_referenced(cursor, order_id)

    if request.method == "POST":
        customer_id = request.form.get("customer_id") or order["customer_id"]
        po_no = request.form.get("po_no")
        remarks = request.form.get("remarks")

        if untouched:
            tool_ids = request.form.getlist("tool_id[]")
            quantities = request.form.getlist("quantity[]")
            rates = request.form.getlist("rate[]")
            items = []
            for tid, qty, rate in zip(tool_ids, quantities, rates):
                if tid and qty:
                    items.append((tid, int(qty), float(rate) if rate else None))
            if not items:
                flash("Add at least one tool with a quantity.", "danger")
                conn.close()
                return redirect(url_for("manufacturing.edit_order", order_id=order_id))

            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("UPDATE mfg_customer_orders SET customer_id=?, po_no=?, remarks=? WHERE id=?", (customer_id, po_no, remarks, order_id))
            cursor.execute("DELETE FROM mfg_customer_order_items WHERE order_id=?", (order_id,))
            for tid, qty, rate in items:
                cursor.execute("INSERT INTO mfg_customer_order_items (order_id, tool_id, quantity, rate) VALUES (?,?,?,?)", (order_id, tid, qty, rate))
            conn.commit()
            conn.close()
            flash("Order updated successfully!", "success")
            return redirect(url_for("manufacturing.view_order", order_id=order_id))

        cursor.execute("UPDATE mfg_customer_orders SET po_no=?, remarks=? WHERE id=?", (po_no, remarks, order_id))
        conn.commit()
        conn.close()
        flash("Order updated successfully!", "success")
        return redirect(url_for("manufacturing.view_order", order_id=order_id))

    cursor.execute("SELECT id, name FROM customers ORDER BY name")
    customers = cursor.fetchall()
    cursor.execute("SELECT id, item_code, tool_name, tool_type, customer_id FROM mfg_tools ORDER BY tool_name")
    tools = cursor.fetchall()
    cursor.execute("SELECT * FROM mfg_customer_order_items WHERE order_id = ?", (order_id,))
    items = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/edit_order.html", order=order, customers=customers, tools=tools, items=items, untouched=untouched)

@manufacturing.route("/manufacturing/orders/delete/<int:order_id>")
def delete_order(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    if _order_items_referenced(cursor, order_id):
        conn.close()
        flash("Cannot delete this order - an invoice already references one of its items.", "danger")
        return redirect(url_for("manufacturing.view_order", order_id=order_id))
    cursor.execute("DELETE FROM mfg_customer_order_items WHERE order_id = ?", (order_id,))
    cursor.execute("DELETE FROM mfg_customer_orders WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    flash("Order deleted successfully!", "danger")
    return redirect(url_for("manufacturing.orders_list"))


# ============================================
# STOCK
# ============================================

@manufacturing.route("/manufacturing/stock")
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

    cursor.execute(f"SELECT id, tool_name, tool_type FROM mfg_tools {where} ORDER BY tool_name LIMIT ? OFFSET ?", params + [per_page, offset])
    tools = cursor.fetchall()

    cursor.execute(f"SELECT COUNT(*) FROM mfg_tools {where}", params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    tool_stock = []
    for tool in tools:
        s = compute_tool_stock(cursor, tool["id"])
        tool_stock.append({"tool": tool, **s})

    conn.close()
    return render_template("manufacturing/stock.html", tool_stock=tool_stock, stages=STAGES, page=page, total_pages=total_pages, total=total, q=q)

@manufacturing.route("/manufacturing/stock/rods")
def stock_rods_view():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    offset = (page - 1) * per_page
    q = request.args.get("q", "")
    q_search = q.strip()

    conn = get_connection()
    cursor = conn.cursor()

    where = "WHERE remaining_length_mm > 0"
    params = []
    if q_search:
        where += " AND grade LIKE ?"
        params.append(f"%{q_search}%")

    cursor.execute(f"SELECT * FROM mfg_rods {where} ORDER BY rod_date DESC LIMIT ? OFFSET ?", params + [per_page, offset])
    rods_in_stock = cursor.fetchall()

    cursor.execute(f"SELECT COUNT(*) FROM mfg_rods {where}", params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    conn.close()
    return render_template("manufacturing/stock_rods.html", rods_in_stock=rods_in_stock, page=page, total_pages=total_pages, total=total, q=q)

@manufacturing.route("/manufacturing/stock/ledger")
def stock_ledger():
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
        where = "WHERE (mfg_stock_movements.source_no LIKE ? OR mfg_rods.grade LIKE ? OR mfg_tools.tool_name LIKE ? OR mfg_items.item_name LIKE ?)"
        params = [f"%{q_search}%", f"%{q_search}%", f"%{q_search}%", f"%{q_search}%"]

    cursor.execute(f"""
        SELECT mfg_stock_movements.*, mfg_rods.grade as rod_grade, mfg_rods.diameter_mm as rod_diameter, mfg_tools.tool_name as tool_name, mfg_items.item_name as mfg_item_name
        FROM mfg_stock_movements
        LEFT JOIN mfg_rods ON mfg_stock_movements.item_type = 'rod' AND mfg_stock_movements.item_id = mfg_rods.id
        LEFT JOIN mfg_tools ON mfg_stock_movements.item_type = 'tool' AND mfg_stock_movements.item_id = mfg_tools.id
        LEFT JOIN mfg_items ON mfg_stock_movements.item_type = 'item' AND mfg_stock_movements.item_id = mfg_items.id
        {where}
        ORDER BY mfg_stock_movements.movement_date DESC, mfg_stock_movements.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    movement_rows = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_stock_movements
        LEFT JOIN mfg_rods ON mfg_stock_movements.item_type = 'rod' AND mfg_stock_movements.item_id = mfg_rods.id
        LEFT JOIN mfg_tools ON mfg_stock_movements.item_type = 'tool' AND mfg_stock_movements.item_id = mfg_tools.id
        LEFT JOIN mfg_items ON mfg_stock_movements.item_type = 'item' AND mfg_stock_movements.item_id = mfg_items.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    movements = []
    for m in movement_rows:
        if m["item_type"] == "rod":
            item_name = f"{m['rod_grade'] or 'Rod'} ({m['rod_diameter']}mm)" if m["rod_grade"] else "Rod #" + str(m["item_id"])
            unit = "mm"
        elif m["item_type"] == "item":
            item_name = m["mfg_item_name"] if m["mfg_item_name"] else "Item #" + str(m["item_id"])
            unit = "pcs"
        else:
            item_name = m["tool_name"] if m["tool_name"] else "Tool #" + str(m["item_id"])
            unit = "pcs"
        movements.append({"movement": m, "item_name": item_name, "unit": unit})

    conn.close()
    return render_template("manufacturing/stock_ledger.html", movements=movements, page=page, total_pages=total_pages, total=total, q=q)


# ============================================
# PURCHASE ORDERS (covers rods, generic items, and bought-in finished tools -
# each line's item_type says which: 'rod' has no master so carries grade/diameter/
# length directly on the line, 'item' references mfg_items, 'tool' references
# mfg_tools for finished tools bought complete from an outside vendor)
# ============================================

def generate_po_no(order_date, cursor):
    date_part = order_date.replace("-", "")
    cursor.execute("SELECT order_no FROM mfg_purchase_orders WHERE order_no LIKE ?", (f"MPO-{date_part}-%",))
    existing_numbers = []
    for row in cursor.fetchall():
        try:
            existing_numbers.append(int(row["order_no"].split("-")[-1]))
        except ValueError:
            pass
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"MPO-{date_part}-{str(next_number).zfill(4)}"

@manufacturing.route("/manufacturing/purchase-orders")
def purchase_orders_list():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    offset = (page - 1) * per_page
    q = request.args.get("q", "")
    q_search = q.strip()
    supplier_id = request.args.get("supplier_id", "")
    status = request.args.get("status", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")

    conn = get_connection()
    cursor = conn.cursor()

    where_parts = []
    params = []
    if q_search:
        where_parts.append("(mfg_purchase_orders.order_no LIKE ? OR suppliers.name LIKE ?)")
        params += [f"%{q_search}%", f"%{q_search}%"]
    if supplier_id:
        where_parts.append("mfg_purchase_orders.supplier_id = ?")
        params.append(supplier_id)
    if status:
        where_parts.append("mfg_purchase_orders.status = ?")
        params.append(status)
    if from_date:
        where_parts.append("mfg_purchase_orders.order_date >= ?")
        params.append(from_date)
    if to_date:
        where_parts.append("mfg_purchase_orders.order_date <= ?")
        params.append(to_date)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    cursor.execute(f"""
        SELECT mfg_purchase_orders.*, suppliers.name as supplier_name
        FROM mfg_purchase_orders
        LEFT JOIN suppliers ON mfg_purchase_orders.supplier_id = suppliers.id
        {where}
        ORDER BY mfg_purchase_orders.order_date DESC, mfg_purchase_orders.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    orders = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_purchase_orders
        LEFT JOIN suppliers ON mfg_purchase_orders.supplier_id = suppliers.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/purchase_orders.html", orders=orders, page=page, total_pages=total_pages, total=total,
        q=q, supplier_id=supplier_id, status=status, from_date=from_date, to_date=to_date, suppliers=suppliers)

@manufacturing.route("/manufacturing/purchase-orders/add", methods=["GET", "POST"])
def add_purchase_order():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        order_date = request.form["order_date"]
        supplier_id = request.form.get("supplier_id") or None
        transporter_id = request.form.get("transporter_id") or None
        remarks = request.form.get("remarks")

        item_types = request.form.getlist("item_type[]")
        item_ids = request.form.getlist("item_id[]")
        descriptions = request.form.getlist("description[]")
        rod_grades = request.form.getlist("rod_grade[]")
        rod_diameters = request.form.getlist("rod_diameter_mm[]")
        rod_lengths = request.form.getlist("rod_length_mm[]")
        quantities = request.form.getlist("quantity[]")
        unit_ids = request.form.getlist("unit_id[]")
        rates = request.form.getlist("rate[]")
        hsn_codes = request.form.getlist("hsn_code[]")
        tax_rates = request.form.getlist("tax_rate[]")

        items = []
        for i in range(len(item_types)):
            if not quantities[i] or float(quantities[i] or 0) <= 0:
                continue
            quantity = float(quantities[i])
            rate = float(rates[i]) if rates[i] else 0
            tax_rate = float(tax_rates[i]) if tax_rates[i] else 18
            bef_tax_amt = round(quantity * rate, 2)
            tax_amount = round(bef_tax_amt * tax_rate / 100, 2)
            amount = round(bef_tax_amt + tax_amount, 2)
            items.append({
                "item_type": item_types[i],
                "item_id": item_ids[i] or None,
                "description": descriptions[i],
                "rod_grade": rod_grades[i] or None,
                "rod_diameter_mm": float(rod_diameters[i]) if rod_diameters[i] else None,
                "rod_length_mm": float(rod_lengths[i]) if rod_lengths[i] else None,
                "quantity": quantity,
                "unit_id": unit_ids[i] or None,
                "rate": rate,
                "hsn_code": hsn_codes[i] or None,
                "tax_rate": tax_rate,
                "bef_tax_amt": bef_tax_amt,
                "tax_amount": tax_amount,
                "amount": amount,
            })

        if not items:
            flash("Add at least one line item with a quantity.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_purchase_order"))

        total_amount = round(sum(i["amount"] for i in items), 2)
        cursor.execute("BEGIN IMMEDIATE")
        order_no = generate_po_no(order_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_purchase_orders (order_no, order_date, supplier_id, transporter_id, remarks, total_amount)
            VALUES (?,?,?,?,?,?)
        """, (order_no, order_date, supplier_id, transporter_id, remarks, total_amount))
        order_id = cursor.lastrowid
        for it in items:
            cursor.execute("""
                INSERT INTO mfg_purchase_order_items
                (order_id, item_type, item_id, description, rod_grade, rod_diameter_mm, rod_length_mm,
                 quantity, unit_id, rate, hsn_code, tax_rate, bef_tax_amt, tax_amount, amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (order_id, it["item_type"], it["item_id"], it["description"], it["rod_grade"], it["rod_diameter_mm"],
                  it["rod_length_mm"], it["quantity"], it["unit_id"], it["rate"], it["hsn_code"], it["tax_rate"],
                  it["bef_tax_amt"], it["tax_amount"], it["amount"]))
        conn.commit()
        conn.close()
        flash(f"Purchase Order {order_no} created!", "success")
        return redirect(url_for("manufacturing.view_purchase_order", order_id=order_id))

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    cursor.execute("SELECT id, item_name, unit_id, hsn_code, purchase_price, purchase_discount_percent FROM mfg_items ORDER BY item_name")
    items_master = cursor.fetchall()
    cursor.execute("SELECT id, item_code, tool_name, tool_type, purchase_price, purchase_discount_percent FROM mfg_tools ORDER BY tool_name")
    tools_master = cursor.fetchall()
    cursor.execute("SELECT id, unit_name FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT id, name FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/add_purchase_order.html", suppliers=suppliers, items_master=items_master, tools_master=tools_master, units=units, transporters=transporters)

@manufacturing.route("/manufacturing/purchase-orders/<int:order_id>")
def view_purchase_order(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_purchase_orders.*, suppliers.name as supplier_name, transporters.name as transporter_name
        FROM mfg_purchase_orders
        LEFT JOIN suppliers ON mfg_purchase_orders.supplier_id = suppliers.id
        LEFT JOIN transporters ON mfg_purchase_orders.transporter_id = transporters.id
        WHERE mfg_purchase_orders.id = ?
    """, (order_id,))
    order = cursor.fetchone()
    cursor.execute("SELECT * FROM mfg_purchase_order_items WHERE order_id = ?", (order_id,))
    items = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) as c FROM mfg_purchase_grn WHERE order_id = ?", (order_id,))
    delete_blocked = cursor.fetchone()["c"] > 0
    conn.close()
    return render_template("manufacturing/view_purchase_order.html", order=order, items=items, delete_blocked=delete_blocked)

def _po_is_untouched(cursor, order_id):
    cursor.execute("SELECT COUNT(*) as c FROM mfg_purchase_grn WHERE order_id = ?", (order_id,))
    return cursor.fetchone()["c"] == 0

@manufacturing.route("/manufacturing/purchase-orders/<int:order_id>/edit", methods=["GET", "POST"])
def edit_purchase_order(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_purchase_orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()
    if not order:
        conn.close()
        flash("Purchase Order not found.", "danger")
        return redirect(url_for("manufacturing.purchase_orders_list"))
    untouched = _po_is_untouched(cursor, order_id)

    if request.method == "POST":
        transporter_id = request.form.get("transporter_id") or None
        remarks = request.form.get("remarks")

        if untouched:
            supplier_id = request.form.get("supplier_id") or None
            item_types = request.form.getlist("item_type[]")
            item_ids = request.form.getlist("item_id[]")
            descriptions = request.form.getlist("description[]")
            rod_grades = request.form.getlist("rod_grade[]")
            rod_diameters = request.form.getlist("rod_diameter_mm[]")
            rod_lengths = request.form.getlist("rod_length_mm[]")
            quantities = request.form.getlist("quantity[]")
            unit_ids = request.form.getlist("unit_id[]")
            rates = request.form.getlist("rate[]")
            hsn_codes = request.form.getlist("hsn_code[]")
            tax_rates = request.form.getlist("tax_rate[]")

            items = []
            for i in range(len(item_types)):
                if not quantities[i] or float(quantities[i] or 0) <= 0:
                    continue
                quantity = float(quantities[i])
                rate = float(rates[i]) if rates[i] else 0
                tax_rate = float(tax_rates[i]) if tax_rates[i] else 18
                bef_tax_amt = round(quantity * rate, 2)
                tax_amount = round(bef_tax_amt * tax_rate / 100, 2)
                amount = round(bef_tax_amt + tax_amount, 2)
                items.append({
                    "item_type": item_types[i], "item_id": item_ids[i] or None, "description": descriptions[i],
                    "rod_grade": rod_grades[i] or None,
                    "rod_diameter_mm": float(rod_diameters[i]) if rod_diameters[i] else None,
                    "rod_length_mm": float(rod_lengths[i]) if rod_lengths[i] else None,
                    "quantity": quantity, "unit_id": unit_ids[i] or None, "rate": rate,
                    "hsn_code": hsn_codes[i] or None, "tax_rate": tax_rate,
                    "bef_tax_amt": bef_tax_amt, "tax_amount": tax_amount, "amount": amount,
                })

            if not items:
                flash("Add at least one line item with a quantity.", "danger")
                conn.close()
                return redirect(url_for("manufacturing.edit_purchase_order", order_id=order_id))

            total_amount = round(sum(i["amount"] for i in items), 2)
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("UPDATE mfg_purchase_orders SET supplier_id=?, transporter_id=?, remarks=?, total_amount=? WHERE id=?",
                           (supplier_id, transporter_id, remarks, total_amount, order_id))
            cursor.execute("DELETE FROM mfg_purchase_order_items WHERE order_id=?", (order_id,))
            for it in items:
                cursor.execute("""
                    INSERT INTO mfg_purchase_order_items
                    (order_id, item_type, item_id, description, rod_grade, rod_diameter_mm, rod_length_mm,
                     quantity, unit_id, rate, hsn_code, tax_rate, bef_tax_amt, tax_amount, amount)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (order_id, it["item_type"], it["item_id"], it["description"], it["rod_grade"], it["rod_diameter_mm"],
                      it["rod_length_mm"], it["quantity"], it["unit_id"], it["rate"], it["hsn_code"], it["tax_rate"],
                      it["bef_tax_amt"], it["tax_amount"], it["amount"]))
            conn.commit()
            conn.close()
            flash("Purchase Order updated successfully!", "success")
            return redirect(url_for("manufacturing.view_purchase_order", order_id=order_id))

        cursor.execute("UPDATE mfg_purchase_orders SET transporter_id=?, remarks=? WHERE id=?", (transporter_id, remarks, order_id))
        conn.commit()
        conn.close()
        flash("Purchase Order updated successfully!", "success")
        return redirect(url_for("manufacturing.view_purchase_order", order_id=order_id))

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    cursor.execute("SELECT id, name FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    cursor.execute("SELECT * FROM mfg_purchase_order_items WHERE order_id = ?", (order_id,))
    items = cursor.fetchall()
    cursor.execute("SELECT id, item_name, unit_id, hsn_code, purchase_price, purchase_discount_percent FROM mfg_items ORDER BY item_name")
    items_master = cursor.fetchall()
    cursor.execute("SELECT id, item_code, tool_name, tool_type, purchase_price, purchase_discount_percent FROM mfg_tools ORDER BY tool_name")
    tools_master = cursor.fetchall()
    cursor.execute("SELECT id, unit_name FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/edit_purchase_order.html", order=order, suppliers=suppliers,
        transporters=transporters, items=items, items_master=items_master, tools_master=tools_master,
        units=units, untouched=untouched)

@manufacturing.route("/manufacturing/purchase-orders/delete/<int:order_id>")
def delete_purchase_order(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    if not _po_is_untouched(cursor, order_id):
        conn.close()
        flash("Cannot delete this Purchase Order - a GRN has already been raised against it.", "danger")
        return redirect(url_for("manufacturing.view_purchase_order", order_id=order_id))
    cursor.execute("DELETE FROM mfg_purchase_order_items WHERE order_id = ?", (order_id,))
    cursor.execute("DELETE FROM mfg_purchase_orders WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    flash("Purchase Order deleted successfully!", "danger")
    return redirect(url_for("manufacturing.purchase_orders_list"))

@manufacturing.route("/manufacturing/purchase-orders/<int:order_id>/print")
def print_purchase_order(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_purchase_orders.*, suppliers.name as supplier_name
        FROM mfg_purchase_orders
        LEFT JOIN suppliers ON mfg_purchase_orders.supplier_id = suppliers.id
        WHERE mfg_purchase_orders.id = ?
    """, (order_id,))
    order = cursor.fetchone()
    cursor.execute("SELECT * FROM suppliers WHERE id = ?", (order["supplier_id"],))
    party = cursor.fetchone()
    transporter = None
    if order["transporter_id"]:
        cursor.execute("SELECT * FROM transporters WHERE id = ?", (order["transporter_id"],))
        transporter = cursor.fetchone()
    cursor.execute("SELECT * FROM mfg_purchase_order_items WHERE order_id = ?", (order_id,))
    item_rows = cursor.fetchall()
    conn.close()

    items = [{"description": r["description"], "quantity": r["quantity"], "rate": r["rate"], "amount": r["amount"]} for r in item_rows]
    return render_template("manufacturing/print_document.html",
        doc_title="Purchase Order", doc_no=order["order_no"], doc_date=order["order_date"],
        party=party, items=items, show_pricing=True, show_batch_no=False, transporter=transporter,
        subtotal=round(order["total_amount"] - sum(i["tax_amount"] for i in item_rows), 2),
        tax_amount=round(sum(i["tax_amount"] for i in item_rows), 2),
        total=order["total_amount"], remarks=order["remarks"], back_url=f"/manufacturing/purchase-orders/{order_id}")


# ============================================
# PURCHASE GRN (the actual stock-IN moment - rod lines create a new mfg_rods
# entry, item/tool lines post straight to mfg_stock_movements, exactly mirroring
# how add_rod() and batch-complete already create stock elsewhere in this file)
# ============================================

def _recompute_po_status(cursor, order_id):
    cursor.execute("SELECT quantity, received_qty FROM mfg_purchase_order_items WHERE order_id = ?", (order_id,))
    po_items = cursor.fetchall()
    if po_items and all((r["received_qty"] or 0) >= r["quantity"] for r in po_items):
        new_status = "completed"
    elif any((r["received_qty"] or 0) > 0 for r in po_items):
        new_status = "partial"
    else:
        new_status = "pending"
    cursor.execute("UPDATE mfg_purchase_orders SET status = ? WHERE id = ?", (new_status, order_id))

def generate_grn_no(grn_date, cursor):
    date_part = grn_date.replace("-", "")
    cursor.execute("SELECT grn_no FROM mfg_purchase_grn WHERE grn_no LIKE ?", (f"MGRN-{date_part}-%",))
    existing_numbers = []
    for row in cursor.fetchall():
        try:
            existing_numbers.append(int(row["grn_no"].split("-")[-1]))
        except ValueError:
            pass
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"MGRN-{date_part}-{str(next_number).zfill(4)}"

@manufacturing.route("/manufacturing/purchase-grn")
def purchase_grn_list():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    offset = (page - 1) * per_page
    q = request.args.get("q", "")
    q_search = q.strip()
    supplier_id = request.args.get("supplier_id", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")

    conn = get_connection()
    cursor = conn.cursor()

    where_parts = []
    params = []
    if q_search:
        where_parts.append("(mfg_purchase_grn.grn_no LIKE ? OR suppliers.name LIKE ?)")
        params += [f"%{q_search}%", f"%{q_search}%"]
    if supplier_id:
        where_parts.append("mfg_purchase_grn.supplier_id = ?")
        params.append(supplier_id)
    if from_date:
        where_parts.append("mfg_purchase_grn.grn_date >= ?")
        params.append(from_date)
    if to_date:
        where_parts.append("mfg_purchase_grn.grn_date <= ?")
        params.append(to_date)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    cursor.execute(f"""
        SELECT mfg_purchase_grn.*, suppliers.name as supplier_name
        FROM mfg_purchase_grn
        LEFT JOIN suppliers ON mfg_purchase_grn.supplier_id = suppliers.id
        {where}
        ORDER BY mfg_purchase_grn.grn_date DESC, mfg_purchase_grn.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    grns = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_purchase_grn
        LEFT JOIN suppliers ON mfg_purchase_grn.supplier_id = suppliers.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/purchase_grn.html", grns=grns, page=page, total_pages=total_pages, total=total,
        q=q, supplier_id=supplier_id, from_date=from_date, to_date=to_date, suppliers=suppliers)

@manufacturing.route("/manufacturing/purchase-grn/add", methods=["GET", "POST"])
def add_purchase_grn():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        grn_date = request.form["grn_date"]
        supplier_id = request.form.get("supplier_id") or None
        order_id = request.form.get("order_id") or None
        transporter_id = request.form.get("transporter_id") or None
        remarks = request.form.get("remarks")

        item_types = request.form.getlist("item_type[]")
        item_ids = request.form.getlist("item_id[]")
        descriptions = request.form.getlist("description[]")
        rod_grades = request.form.getlist("rod_grade[]")
        rod_diameters = request.form.getlist("rod_diameter_mm[]")
        rod_lengths = request.form.getlist("rod_length_mm[]")
        quantities = request.form.getlist("quantity[]")
        unit_ids = request.form.getlist("unit_id[]")
        rates = request.form.getlist("rate[]")
        hsn_codes = request.form.getlist("hsn_code[]")
        tax_rates = request.form.getlist("tax_rate[]")

        items = []
        for i in range(len(item_types)):
            if not quantities[i] or float(quantities[i] or 0) <= 0:
                continue
            quantity = float(quantities[i])
            rate = float(rates[i]) if rates[i] else 0
            tax_rate = float(tax_rates[i]) if tax_rates[i] else 18
            bef_tax_amt = round(quantity * rate, 2)
            tax_amount = round(bef_tax_amt * tax_rate / 100, 2)
            amount = round(bef_tax_amt + tax_amount, 2)
            items.append({
                "item_type": item_types[i],
                "item_id": item_ids[i] or None,
                "description": descriptions[i],
                "rod_grade": rod_grades[i] or None,
                "rod_diameter_mm": float(rod_diameters[i]) if rod_diameters[i] else None,
                "rod_length_mm": float(rod_lengths[i]) if rod_lengths[i] else None,
                "quantity": quantity,
                "unit_id": unit_ids[i] or None,
                "rate": rate,
                "hsn_code": hsn_codes[i] or None,
                "tax_rate": tax_rate,
                "bef_tax_amt": bef_tax_amt,
                "tax_amount": tax_amount,
                "amount": amount,
            })

        if not items:
            flash("Add at least one line item with a quantity.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_purchase_grn"))

        total_amount = round(sum(i["amount"] for i in items), 2)
        cursor.execute("BEGIN IMMEDIATE")
        grn_no = generate_grn_no(grn_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_purchase_grn (grn_no, grn_date, supplier_id, order_id, transporter_id, remarks, total_amount)
            VALUES (?,?,?,?,?,?,?)
        """, (grn_no, grn_date, supplier_id, order_id, transporter_id, remarks, total_amount))
        grn_id = cursor.lastrowid

        for it in items:
            cursor.execute("""
                INSERT INTO mfg_purchase_grn_items
                (grn_id, item_type, item_id, description, rod_grade, rod_diameter_mm, rod_length_mm,
                 quantity, unit_id, rate, hsn_code, tax_rate, bef_tax_amt, tax_amount, amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (grn_id, it["item_type"], it["item_id"], it["description"], it["rod_grade"], it["rod_diameter_mm"],
                  it["rod_length_mm"], it["quantity"], it["unit_id"], it["rate"], it["hsn_code"], it["tax_rate"],
                  it["bef_tax_amt"], it["tax_amount"], it["amount"]))

            if it["item_type"] == "rod":
                cursor.execute("""
                    INSERT INTO mfg_rods (rod_date, grade, diameter_mm, length_mm, remaining_length_mm, quantity, supplier_id, rate, remarks)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (grn_date, it["rod_grade"], it["rod_diameter_mm"], it["rod_length_mm"], it["rod_length_mm"],
                      it["quantity"], supplier_id, it["rate"], f"From GRN {grn_no}"))
                new_rod_id = cursor.lastrowid
                cursor.execute("""
                    INSERT INTO mfg_stock_movements (item_type, item_id, movement_type, quantity, source_type, source_id, source_no, movement_date)
                    VALUES (?,?,?,?,?,?,?,?)
                """, ("rod", new_rod_id, "in", it["rod_length_mm"], "grn", grn_id, grn_no, grn_date))
            else:
                cursor.execute("""
                    INSERT INTO mfg_stock_movements (item_type, item_id, movement_type, quantity, source_type, source_id, source_no, movement_date)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (it["item_type"], it["item_id"], "in", it["quantity"], "grn", grn_id, grn_no, grn_date))

            if order_id:
                if it["item_type"] == "rod":
                    cursor.execute("""
                        SELECT id, received_qty FROM mfg_purchase_order_items
                        WHERE order_id = ? AND item_type = 'rod' AND rod_grade = ? AND rod_diameter_mm = ? AND rod_length_mm = ?
                        LIMIT 1
                    """, (order_id, it["rod_grade"], it["rod_diameter_mm"], it["rod_length_mm"]))
                else:
                    cursor.execute("""
                        SELECT id, received_qty FROM mfg_purchase_order_items
                        WHERE order_id = ? AND item_type = ? AND item_id = ?
                        LIMIT 1
                    """, (order_id, it["item_type"], it["item_id"]))
                po_item = cursor.fetchone()
                if po_item:
                    cursor.execute("UPDATE mfg_purchase_order_items SET received_qty = received_qty + ? WHERE id = ?",
                                   (it["quantity"], po_item["id"]))

        if order_id:
            _recompute_po_status(cursor, order_id)

        conn.commit()
        conn.close()
        flash(f"GRN {grn_no} created - stock updated!", "success")
        return redirect(url_for("manufacturing.view_purchase_grn", grn_id=grn_id))

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    cursor.execute("SELECT id, item_name, unit_id, hsn_code, purchase_price, purchase_discount_percent FROM mfg_items ORDER BY item_name")
    items_master = cursor.fetchall()
    cursor.execute("SELECT id, item_code, tool_name, tool_type, purchase_price, purchase_discount_percent FROM mfg_tools ORDER BY tool_name")
    tools_master = cursor.fetchall()
    cursor.execute("SELECT id, unit_name FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT id, name FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/add_purchase_grn.html", suppliers=suppliers, items_master=items_master, tools_master=tools_master, units=units, transporters=transporters)

@manufacturing.route("/manufacturing/purchase-grn/<int:grn_id>")
def view_purchase_grn(grn_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_purchase_grn.*, suppliers.name as supplier_name, mfg_purchase_orders.order_no as order_no, transporters.name as transporter_name
        FROM mfg_purchase_grn
        LEFT JOIN suppliers ON mfg_purchase_grn.supplier_id = suppliers.id
        LEFT JOIN mfg_purchase_orders ON mfg_purchase_grn.order_id = mfg_purchase_orders.id
        LEFT JOIN transporters ON mfg_purchase_grn.transporter_id = transporters.id
        WHERE mfg_purchase_grn.id = ?
    """, (grn_id,))
    grn = cursor.fetchone()
    cursor.execute("SELECT * FROM mfg_purchase_grn_items WHERE grn_id = ?", (grn_id,))
    items = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) as c FROM mfg_purchase_invoices WHERE grn_id = ?", (grn_id,))
    delete_blocked = cursor.fetchone()["c"] > 0
    conn.close()
    return render_template("manufacturing/view_purchase_grn.html", grn=grn, items=items, delete_blocked=delete_blocked)

@manufacturing.route("/manufacturing/purchase-grn/<int:grn_id>/edit", methods=["GET", "POST"])
def edit_purchase_grn(grn_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_purchase_grn WHERE id = ?", (grn_id,))
    grn = cursor.fetchone()
    if not grn:
        conn.close()
        flash("GRN not found.", "danger")
        return redirect(url_for("manufacturing.purchase_grn_list"))

    if request.method == "POST":
        transporter_id = request.form.get("transporter_id") or None
        remarks = request.form.get("remarks")
        cursor.execute("UPDATE mfg_purchase_grn SET transporter_id=?, remarks=? WHERE id=?", (transporter_id, remarks, grn_id))
        conn.commit()
        conn.close()
        flash("GRN updated successfully!", "success")
        return redirect(url_for("manufacturing.view_purchase_grn", grn_id=grn_id))

    cursor.execute("SELECT id, name FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/edit_purchase_grn.html", grn=grn, transporters=transporters)

def _grn_delete_guard(cursor, grn_id):
    cursor.execute("SELECT COUNT(*) as c FROM mfg_purchase_invoices WHERE grn_id = ?", (grn_id,))
    if cursor.fetchone()["c"] > 0:
        return "Cannot delete - a Purchase Invoice has already been raised against this GRN."
    cursor.execute("SELECT * FROM mfg_purchase_grn_items WHERE grn_id = ? AND item_type = 'rod'", (grn_id,))
    rod_items = cursor.fetchall()
    cursor.execute("SELECT item_id FROM mfg_stock_movements WHERE source_type='grn' AND source_id=? AND item_type='rod' ORDER BY id", (grn_id,))
    rod_movements = cursor.fetchall()
    for rod_item, movement in zip(rod_items, rod_movements):
        rod_id = movement["item_id"]
        cursor.execute("SELECT remaining_length_mm, length_mm FROM mfg_rods WHERE id = ?", (rod_id,))
        rod = cursor.fetchone()
        if rod and rod["remaining_length_mm"] != rod["length_mm"]:
            return "Cannot delete - some of the rod stock this GRN brought in has already been used."
        cursor.execute("SELECT COUNT(*) as c FROM mfg_job_batches WHERE rod_id = ?", (rod_id,))
        if cursor.fetchone()["c"] > 0:
            return "Cannot delete - some of the rod stock this GRN brought in has already been used."
    return None

@manufacturing.route("/manufacturing/purchase-grn/delete/<int:grn_id>")
def delete_purchase_grn(grn_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_purchase_grn WHERE id = ?", (grn_id,))
    grn = cursor.fetchone()
    if not grn:
        conn.close()
        flash("GRN not found.", "danger")
        return redirect(url_for("manufacturing.purchase_grn_list"))

    error = _grn_delete_guard(cursor, grn_id)
    if error:
        conn.close()
        flash(error, "danger")
        return redirect(url_for("manufacturing.view_purchase_grn", grn_id=grn_id))

    cursor.execute("SELECT * FROM mfg_purchase_grn_items WHERE grn_id = ?", (grn_id,))
    items = cursor.fetchall()
    cursor.execute("SELECT item_id FROM mfg_stock_movements WHERE source_type='grn' AND source_id=? AND item_type='rod' ORDER BY id", (grn_id,))
    rod_movements = cursor.fetchall()
    rod_movement_iter = iter(rod_movements)

    cursor.execute("BEGIN IMMEDIATE")
    for it in items:
        if grn["order_id"]:
            if it["item_type"] == "rod":
                cursor.execute("""
                    SELECT id FROM mfg_purchase_order_items
                    WHERE order_id = ? AND item_type = 'rod' AND rod_grade = ? AND rod_diameter_mm = ? AND rod_length_mm = ?
                    LIMIT 1
                """, (grn["order_id"], it["rod_grade"], it["rod_diameter_mm"], it["rod_length_mm"]))
            else:
                cursor.execute("""
                    SELECT id FROM mfg_purchase_order_items
                    WHERE order_id = ? AND item_type = ? AND item_id = ?
                    LIMIT 1
                """, (grn["order_id"], it["item_type"], it["item_id"]))
            po_item = cursor.fetchone()
            if po_item:
                cursor.execute("UPDATE mfg_purchase_order_items SET received_qty = received_qty - ? WHERE id = ?",
                               (it["quantity"], po_item["id"]))
        if it["item_type"] == "rod":
            movement = next(rod_movement_iter, None)
            if movement:
                cursor.execute("DELETE FROM mfg_rods WHERE id = ?", (movement["item_id"],))

    cursor.execute("DELETE FROM mfg_stock_movements WHERE source_type='grn' AND source_id=?", (grn_id,))
    cursor.execute("DELETE FROM mfg_purchase_grn_items WHERE grn_id = ?", (grn_id,))
    cursor.execute("DELETE FROM mfg_purchase_grn WHERE id = ?", (grn_id,))
    if grn["order_id"]:
        _recompute_po_status(cursor, grn["order_id"])
    conn.commit()
    conn.close()
    flash("GRN deleted successfully! Stock and PO received quantities have been reversed.", "danger")
    return redirect(url_for("manufacturing.purchase_grn_list"))

@manufacturing.route("/manufacturing/purchase-grn/<int:grn_id>/print")
def print_purchase_grn(grn_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_purchase_grn WHERE id = ?", (grn_id,))
    grn = cursor.fetchone()
    cursor.execute("SELECT * FROM suppliers WHERE id = ?", (grn["supplier_id"],))
    party = cursor.fetchone()
    transporter = None
    if grn["transporter_id"]:
        cursor.execute("SELECT * FROM transporters WHERE id = ?", (grn["transporter_id"],))
        transporter = cursor.fetchone()
    cursor.execute("SELECT * FROM mfg_purchase_grn_items WHERE grn_id = ?", (grn_id,))
    item_rows = cursor.fetchall()
    conn.close()

    items = [{"description": r["description"], "quantity": r["quantity"], "rate": r["rate"], "amount": r["amount"]} for r in item_rows]
    return render_template("manufacturing/print_document.html",
        doc_title="Goods Receipt Note", doc_no=grn["grn_no"], doc_date=grn["grn_date"],
        party=party, items=items, show_pricing=True, show_batch_no=False, transporter=transporter,
        subtotal=round(grn["total_amount"] - sum(i["tax_amount"] for i in item_rows), 2),
        tax_amount=round(sum(i["tax_amount"] for i in item_rows), 2),
        total=grn["total_amount"], remarks=grn["remarks"], back_url=f"/manufacturing/purchase-grn/{grn_id}")

@manufacturing.route("/manufacturing/api/purchase-order/<int:order_id>")
def api_mfg_purchase_order(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_purchase_order_items WHERE order_id = ?", (order_id,))
    items = cursor.fetchall()
    conn.close()
    result = []
    for item in items:
        remaining = (item["quantity"] or 0) - (item["received_qty"] or 0)
        result.append({
            "item_type": item["item_type"],
            "item_id": item["item_id"],
            "description": item["description"],
            "rod_grade": item["rod_grade"],
            "rod_diameter_mm": item["rod_diameter_mm"],
            "rod_length_mm": item["rod_length_mm"],
            "quantity": remaining if remaining > 0 else 0,
            "unit_id": item["unit_id"],
            "rate": item["rate"],
            "hsn_code": item["hsn_code"],
            "tax_rate": item["tax_rate"],
        })
    return {"items": result}

@manufacturing.route("/manufacturing/api/supplier-orders/<int:supplier_id>")
def api_mfg_supplier_orders(supplier_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, order_no FROM mfg_purchase_orders
        WHERE supplier_id = ? AND status != 'completed'
        ORDER BY order_date DESC
    """, (supplier_id,))
    orders = cursor.fetchall()
    conn.close()
    return {"orders": [{"id": o["id"], "order_no": o["order_no"]} for o in orders]}


# ============================================
# PURCHASE INVOICE (financial document only - never touches stock,
# GRN already moved stock when the goods physically arrived)
# ============================================

def generate_purchase_invoice_no(invoice_date, cursor):
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

@manufacturing.route("/manufacturing/purchase-invoices")
def purchase_invoices_list():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    offset = (page - 1) * per_page
    q = request.args.get("q", "")
    q_search = q.strip()
    supplier_id = request.args.get("supplier_id", "")
    status = request.args.get("status", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")

    conn = get_connection()
    cursor = conn.cursor()

    where_parts = []
    params = []
    if q_search:
        where_parts.append("(mfg_purchase_invoices.invoice_no LIKE ? OR suppliers.name LIKE ?)")
        params += [f"%{q_search}%", f"%{q_search}%"]
    if supplier_id:
        where_parts.append("mfg_purchase_invoices.supplier_id = ?")
        params.append(supplier_id)
    if status:
        where_parts.append("mfg_purchase_invoices.status = ?")
        params.append(status)
    if from_date:
        where_parts.append("mfg_purchase_invoices.invoice_date >= ?")
        params.append(from_date)
    if to_date:
        where_parts.append("mfg_purchase_invoices.invoice_date <= ?")
        params.append(to_date)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    cursor.execute(f"""
        SELECT mfg_purchase_invoices.*, suppliers.name as supplier_name
        FROM mfg_purchase_invoices
        LEFT JOIN suppliers ON mfg_purchase_invoices.supplier_id = suppliers.id
        {where}
        ORDER BY mfg_purchase_invoices.invoice_date DESC, mfg_purchase_invoices.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    invoices = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_purchase_invoices
        LEFT JOIN suppliers ON mfg_purchase_invoices.supplier_id = suppliers.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/purchase_invoices.html", invoices=invoices, page=page, total_pages=total_pages, total=total,
        q=q, supplier_id=supplier_id, status=status, from_date=from_date, to_date=to_date, suppliers=suppliers)

@manufacturing.route("/manufacturing/purchase-invoices/add", methods=["GET", "POST"])
def add_purchase_invoice():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        invoice_date = request.form["invoice_date"]
        supplier_id = request.form.get("supplier_id") or None
        order_id = request.form.get("order_id") or None
        grn_id = request.form.get("grn_id") or None
        transporter_id = request.form.get("transporter_id") or None
        sup_invoice_no = request.form.get("sup_invoice_no")
        sup_invoice_date = request.form.get("sup_invoice_date") or None
        cr_days = request.form.get("cr_days") or 0
        due_date = request.form.get("due_date") or None
        remarks = request.form.get("remarks")

        item_types = request.form.getlist("item_type[]")
        item_ids = request.form.getlist("item_id[]")
        descriptions = request.form.getlist("description[]")
        rod_grades = request.form.getlist("rod_grade[]")
        rod_diameters = request.form.getlist("rod_diameter_mm[]")
        rod_lengths = request.form.getlist("rod_length_mm[]")
        quantities = request.form.getlist("quantity[]")
        unit_ids = request.form.getlist("unit_id[]")
        rates = request.form.getlist("rate[]")
        hsn_codes = request.form.getlist("hsn_code[]")
        tax_rates = request.form.getlist("tax_rate[]")

        items = []
        for i in range(len(item_types)):
            if not quantities[i] or float(quantities[i] or 0) <= 0:
                continue
            quantity = float(quantities[i])
            rate = float(rates[i]) if rates[i] else 0
            tax_rate = float(tax_rates[i]) if tax_rates[i] else 18
            bef_tax_amt = round(quantity * rate, 2)
            tax_amount = round(bef_tax_amt * tax_rate / 100, 2)
            amount = round(bef_tax_amt + tax_amount, 2)
            items.append({
                "item_type": item_types[i],
                "item_id": item_ids[i] or None,
                "description": descriptions[i],
                "rod_grade": rod_grades[i] or None,
                "rod_diameter_mm": float(rod_diameters[i]) if rod_diameters[i] else None,
                "rod_length_mm": float(rod_lengths[i]) if rod_lengths[i] else None,
                "quantity": quantity,
                "unit_id": unit_ids[i] or None,
                "rate": rate,
                "hsn_code": hsn_codes[i] or None,
                "tax_rate": tax_rate,
                "bef_tax_amt": bef_tax_amt,
                "tax_amount": tax_amount,
                "amount": amount,
            })

        if not items:
            flash("Add at least one line item with a quantity.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_purchase_invoice"))

        total_amount = round(sum(i["amount"] for i in items), 2)
        cursor.execute("BEGIN IMMEDIATE")
        invoice_no = generate_purchase_invoice_no(invoice_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_purchase_invoices
            (invoice_no, invoice_date, supplier_id, order_id, grn_id, transporter_id, sup_invoice_no, sup_invoice_date, cr_days, due_date, remarks, total_amount)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (invoice_no, invoice_date, supplier_id, order_id, grn_id, transporter_id, sup_invoice_no, sup_invoice_date, cr_days, due_date, remarks, total_amount))
        invoice_id = cursor.lastrowid
        for it in items:
            cursor.execute("""
                INSERT INTO mfg_purchase_invoice_items
                (invoice_id, item_type, item_id, description, rod_grade, rod_diameter_mm, rod_length_mm,
                 quantity, unit_id, rate, hsn_code, tax_rate, bef_tax_amt, tax_amount, amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (invoice_id, it["item_type"], it["item_id"], it["description"], it["rod_grade"], it["rod_diameter_mm"],
                  it["rod_length_mm"], it["quantity"], it["unit_id"], it["rate"], it["hsn_code"], it["tax_rate"],
                  it["bef_tax_amt"], it["tax_amount"], it["amount"]))
        conn.commit()
        conn.close()
        flash(f"Purchase Invoice {invoice_no} created!", "success")
        return redirect(url_for("manufacturing.view_purchase_invoice", invoice_id=invoice_id))

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    cursor.execute("SELECT id, item_name, unit_id, hsn_code, purchase_price, purchase_discount_percent FROM mfg_items ORDER BY item_name")
    items_master = cursor.fetchall()
    cursor.execute("SELECT id, item_code, tool_name, tool_type, purchase_price, purchase_discount_percent FROM mfg_tools ORDER BY tool_name")
    tools_master = cursor.fetchall()
    cursor.execute("SELECT id, unit_name FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT id, name FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/add_purchase_invoice.html", suppliers=suppliers, items_master=items_master, tools_master=tools_master, units=units, transporters=transporters)

@manufacturing.route("/manufacturing/purchase-invoices/<int:invoice_id>")
def view_purchase_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_purchase_invoices.*, suppliers.name as supplier_name,
               mfg_purchase_orders.order_no as order_no, mfg_purchase_grn.grn_no as grn_no, transporters.name as transporter_name
        FROM mfg_purchase_invoices
        LEFT JOIN suppliers ON mfg_purchase_invoices.supplier_id = suppliers.id
        LEFT JOIN mfg_purchase_orders ON mfg_purchase_invoices.order_id = mfg_purchase_orders.id
        LEFT JOIN mfg_purchase_grn ON mfg_purchase_invoices.grn_id = mfg_purchase_grn.id
        LEFT JOIN transporters ON mfg_purchase_invoices.transporter_id = transporters.id
        WHERE mfg_purchase_invoices.id = ?
    """, (invoice_id,))
    invoice = cursor.fetchone()
    cursor.execute("SELECT * FROM mfg_purchase_invoice_items WHERE invoice_id = ?", (invoice_id,))
    items = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) as c FROM mfg_purchase_payments WHERE invoice_id = ?", (invoice_id,))
    delete_blocked = cursor.fetchone()["c"] > 0
    conn.close()
    return render_template("manufacturing/view_purchase_invoice.html", invoice=invoice, items=items, delete_blocked=delete_blocked)

def _purchase_invoice_is_untouched(cursor, invoice_id):
    cursor.execute("SELECT COUNT(*) as c FROM mfg_purchase_payments WHERE invoice_id = ?", (invoice_id,))
    return cursor.fetchone()["c"] == 0

@manufacturing.route("/manufacturing/purchase-invoices/<int:invoice_id>/edit", methods=["GET", "POST"])
def edit_purchase_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_purchase_invoices WHERE id = ?", (invoice_id,))
    invoice = cursor.fetchone()
    if not invoice:
        conn.close()
        flash("Purchase Invoice not found.", "danger")
        return redirect(url_for("manufacturing.purchase_invoices_list"))
    untouched = _purchase_invoice_is_untouched(cursor, invoice_id)

    if request.method == "POST":
        sup_invoice_no = request.form.get("sup_invoice_no")
        sup_invoice_date = request.form.get("sup_invoice_date") or None
        cr_days = request.form.get("cr_days") or 0
        due_date = request.form.get("due_date") or None
        remarks = request.form.get("remarks")

        if untouched and request.form.get("item_type[]"):
            item_types = request.form.getlist("item_type[]")
            item_ids = request.form.getlist("item_id[]")
            descriptions = request.form.getlist("description[]")
            rod_grades = request.form.getlist("rod_grade[]")
            rod_diameters = request.form.getlist("rod_diameter_mm[]")
            rod_lengths = request.form.getlist("rod_length_mm[]")
            quantities = request.form.getlist("quantity[]")
            unit_ids = request.form.getlist("unit_id[]")
            rates = request.form.getlist("rate[]")
            hsn_codes = request.form.getlist("hsn_code[]")
            tax_rates = request.form.getlist("tax_rate[]")

            items = []
            for i in range(len(item_types)):
                if not quantities[i] or float(quantities[i] or 0) <= 0:
                    continue
                quantity = float(quantities[i])
                rate = float(rates[i]) if rates[i] else 0
                tax_rate = float(tax_rates[i]) if tax_rates[i] else 18
                bef_tax_amt = round(quantity * rate, 2)
                tax_amount = round(bef_tax_amt * tax_rate / 100, 2)
                amount = round(bef_tax_amt + tax_amount, 2)
                items.append({
                    "item_type": item_types[i], "item_id": item_ids[i] or None, "description": descriptions[i],
                    "rod_grade": rod_grades[i] or None,
                    "rod_diameter_mm": float(rod_diameters[i]) if rod_diameters[i] else None,
                    "rod_length_mm": float(rod_lengths[i]) if rod_lengths[i] else None,
                    "quantity": quantity, "unit_id": unit_ids[i] or None, "rate": rate,
                    "hsn_code": hsn_codes[i] or None, "tax_rate": tax_rate,
                    "bef_tax_amt": bef_tax_amt, "tax_amount": tax_amount, "amount": amount,
                })

            if not items:
                flash("Add at least one line item with a quantity.", "danger")
                conn.close()
                return redirect(url_for("manufacturing.edit_purchase_invoice", invoice_id=invoice_id))

            total_amount = round(sum(i["amount"] for i in items), 2)
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("""
                UPDATE mfg_purchase_invoices SET sup_invoice_no=?, sup_invoice_date=?, cr_days=?, due_date=?, remarks=?, total_amount=?
                WHERE id=?
            """, (sup_invoice_no, sup_invoice_date, cr_days, due_date, remarks, total_amount, invoice_id))
            cursor.execute("DELETE FROM mfg_purchase_invoice_items WHERE invoice_id=?", (invoice_id,))
            for it in items:
                cursor.execute("""
                    INSERT INTO mfg_purchase_invoice_items
                    (invoice_id, item_type, item_id, description, rod_grade, rod_diameter_mm, rod_length_mm,
                     quantity, unit_id, rate, hsn_code, tax_rate, bef_tax_amt, tax_amount, amount)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (invoice_id, it["item_type"], it["item_id"], it["description"], it["rod_grade"], it["rod_diameter_mm"],
                      it["rod_length_mm"], it["quantity"], it["unit_id"], it["rate"], it["hsn_code"], it["tax_rate"],
                      it["bef_tax_amt"], it["tax_amount"], it["amount"]))
            conn.commit()
            conn.close()
            flash("Purchase Invoice updated successfully!", "success")
            return redirect(url_for("manufacturing.view_purchase_invoice", invoice_id=invoice_id))

        cursor.execute("""
            UPDATE mfg_purchase_invoices SET sup_invoice_no=?, sup_invoice_date=?, cr_days=?, due_date=?, remarks=?
            WHERE id=?
        """, (sup_invoice_no, sup_invoice_date, cr_days, due_date, remarks, invoice_id))
        conn.commit()
        conn.close()
        flash("Purchase Invoice updated successfully!", "success")
        return redirect(url_for("manufacturing.view_purchase_invoice", invoice_id=invoice_id))

    cursor.execute("SELECT * FROM mfg_purchase_invoice_items WHERE invoice_id = ?", (invoice_id,))
    items = cursor.fetchall()
    cursor.execute("SELECT id, item_name, unit_id, hsn_code, purchase_price, purchase_discount_percent FROM mfg_items ORDER BY item_name")
    items_master = cursor.fetchall()
    cursor.execute("SELECT id, item_code, tool_name, tool_type, purchase_price, purchase_discount_percent FROM mfg_tools ORDER BY tool_name")
    tools_master = cursor.fetchall()
    cursor.execute("SELECT id, unit_name FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/edit_purchase_invoice.html", invoice=invoice, items=items,
        items_master=items_master, tools_master=tools_master, units=units, untouched=untouched)

@manufacturing.route("/manufacturing/purchase-invoices/delete/<int:invoice_id>")
def delete_purchase_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    if not _purchase_invoice_is_untouched(cursor, invoice_id):
        conn.close()
        flash("Cannot delete this invoice - a payment has already been recorded against it.", "danger")
        return redirect(url_for("manufacturing.view_purchase_invoice", invoice_id=invoice_id))

    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute("UPDATE mfg_job_stages SET purchase_invoice_id = NULL WHERE purchase_invoice_id = ?", (invoice_id,))
    cursor.execute("UPDATE mfg_resharpen_stages SET purchase_invoice_id = NULL WHERE purchase_invoice_id = ?", (invoice_id,))
    cursor.execute("DELETE FROM mfg_purchase_invoice_items WHERE invoice_id = ?", (invoice_id,))
    cursor.execute("DELETE FROM mfg_purchase_invoices WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()
    flash("Purchase Invoice deleted successfully!", "danger")
    return redirect(url_for("manufacturing.purchase_invoices_list"))

@manufacturing.route("/manufacturing/purchase-invoices/<int:invoice_id>/print")
def print_purchase_invoice(invoice_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_purchase_invoices WHERE id = ?", (invoice_id,))
    invoice = cursor.fetchone()
    cursor.execute("SELECT * FROM suppliers WHERE id = ?", (invoice["supplier_id"],))
    party = cursor.fetchone()
    transporter = None
    if invoice["transporter_id"]:
        cursor.execute("SELECT * FROM transporters WHERE id = ?", (invoice["transporter_id"],))
        transporter = cursor.fetchone()
    cursor.execute("SELECT * FROM mfg_purchase_invoice_items WHERE invoice_id = ?", (invoice_id,))
    item_rows = cursor.fetchall()
    conn.close()

    items = [{"description": r["description"], "quantity": r["quantity"], "rate": r["rate"], "amount": r["amount"]} for r in item_rows]
    return render_template("manufacturing/print_document.html",
        doc_title="Purchase Invoice", doc_no=invoice["invoice_no"], doc_date=invoice["invoice_date"],
        party=party, items=items, show_pricing=True, show_batch_no=False, transporter=transporter,
        subtotal=round(invoice["total_amount"] - sum(i["tax_amount"] for i in item_rows), 2),
        tax_amount=round(sum(i["tax_amount"] for i in item_rows), 2),
        total=invoice["total_amount"], remarks=invoice["remarks"], back_url=f"/manufacturing/purchase-invoices/{invoice_id}")

@manufacturing.route("/manufacturing/api/purchase-grn/<int:grn_id>")
def api_mfg_purchase_grn(grn_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_purchase_grn_items WHERE grn_id = ?", (grn_id,))
    items = cursor.fetchall()
    cursor.execute("SELECT order_id FROM mfg_purchase_grn WHERE id = ?", (grn_id,))
    grn_row = cursor.fetchone()
    conn.close()
    result = []
    for item in items:
        result.append({
            "item_type": item["item_type"],
            "item_id": item["item_id"],
            "description": item["description"],
            "rod_grade": item["rod_grade"],
            "rod_diameter_mm": item["rod_diameter_mm"],
            "rod_length_mm": item["rod_length_mm"],
            "quantity": item["quantity"],
            "unit_id": item["unit_id"],
            "rate": item["rate"],
            "hsn_code": item["hsn_code"],
            "tax_rate": item["tax_rate"],
        })
    return {"items": result, "order_id": grn_row["order_id"] if grn_row else None}


# ============================================
# REPORTS - Sales & Margin
# ============================================

@manufacturing.route("/manufacturing/reports/sales")
def sales_report():
    """Unified Sales Report - covers both new-tool job-work invoices and Resharpening
    invoices in the same table/totals (by request, same treatment as Payments/Outstanding),
    with a Process filter to isolate one or the other. Loads both full result sets in
    Python and filters/sorts/paginates there (same pattern margin_report already used)
    since the two live in separate tables that can't be UNIONed by id."""
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    q = request.args.get("q", "")
    q_search = q.strip()
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")
    customer_id = request.args.get("customer_id", "")
    tool_id = request.args.get("tool_id", "")
    min_amount = request.args.get("min_amount", "")
    max_amount = request.args.get("max_amount", "")
    process = request.args.get("process", "")
    sort = request.args.get("sort", "invoice_date")
    direction = request.args.get("dir", "desc")

    conn = get_connection()
    cursor = conn.cursor()

    rows_all = []
    if process != "resharpen":
        cursor.execute("""
            SELECT mfg_invoice_items.quantity, mfg_invoice_items.rate, mfg_invoice_items.tax_rate,
                   mfg_invoice_items.bef_tax_amt, mfg_invoice_items.tax_amount, mfg_invoice_items.amount,
                   mfg_invoices.id as invoice_id, mfg_invoices.invoice_no, mfg_invoices.invoice_date,
                   mfg_invoices.customer_id, mfg_invoices.remarks as invoice_remarks,
                   customers.name as customer_name, mfg_tools.tool_name, mfg_tools.item_code, mfg_tools.id as tool_id,
                   mfg_job_batches.id as batch_id, mfg_job_batches.batch_no
            FROM mfg_invoice_items
            JOIN mfg_invoices ON mfg_invoice_items.invoice_id = mfg_invoices.id
            LEFT JOIN customers ON mfg_invoices.customer_id = customers.id
            JOIN mfg_job_batches ON mfg_invoice_items.batch_id = mfg_job_batches.id
            JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
        """)
        rows_all += [dict(r, source="new") for r in cursor.fetchall()]

    if process != "new":
        cursor.execute("""
            SELECT mfg_resharpen_invoice_items.quantity, mfg_resharpen_invoice_items.rate, mfg_resharpen_invoice_items.tax_rate,
                   mfg_resharpen_invoice_items.bef_tax_amt, mfg_resharpen_invoice_items.tax_amount, mfg_resharpen_invoice_items.amount,
                   mfg_resharpen_invoices.id as invoice_id, mfg_resharpen_invoices.invoice_no, mfg_resharpen_invoices.invoice_date,
                   mfg_resharpen_invoices.customer_id, mfg_resharpen_invoices.remarks as invoice_remarks,
                   customers.name as customer_name, mfg_tools.tool_name, mfg_tools.item_code, mfg_tools.id as tool_id,
                   mfg_resharpen_batches.id as batch_id, mfg_resharpen_batches.batch_no
            FROM mfg_resharpen_invoice_items
            JOIN mfg_resharpen_invoices ON mfg_resharpen_invoice_items.invoice_id = mfg_resharpen_invoices.id
            LEFT JOIN customers ON mfg_resharpen_invoices.customer_id = customers.id
            JOIN mfg_resharpen_batches ON mfg_resharpen_invoice_items.batch_id = mfg_resharpen_batches.id
            JOIN mfg_tools ON mfg_resharpen_batches.tool_id = mfg_tools.id
        """)
        rows_all += [dict(r, source="resharpen") for r in cursor.fetchall()]

    def matches(r):
        if q_search:
            hay = " ".join(str(r.get(f) or "") for f in ["invoice_no", "customer_name", "tool_name", "batch_no"]).lower()
            if q_search.lower() not in hay:
                return False
        if from_date and (r["invoice_date"] or "") < from_date:
            return False
        if to_date and (r["invoice_date"] or "") > to_date:
            return False
        if customer_id and str(r["customer_id"]) != str(customer_id):
            return False
        if tool_id and str(r["tool_id"]) != str(tool_id):
            return False
        if min_amount and (r["amount"] or 0) < float(min_amount):
            return False
        if max_amount and (r["amount"] or 0) > float(max_amount):
            return False
        return True

    rows_all = [r for r in rows_all if matches(r)]

    sort_keys = {"invoice_no": "invoice_no", "invoice_date": "invoice_date", "customer_name": "customer_name",
                 "tool_name": "tool_name", "quantity": "quantity", "rate": "rate", "amount": "amount"}
    sort_key = sort_keys.get(sort, "invoice_date")
    rows_all.sort(key=lambda r: (r[sort_key] is None, r[sort_key]), reverse=(direction == "desc"))

    total = len(rows_all)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    rows = rows_all[offset:offset + per_page]

    subtotal = round(sum(r["bef_tax_amt"] or 0 for r in rows_all), 2)
    tax = round(sum(r["tax_amount"] or 0 for r in rows_all), 2)
    grand_total = round(sum(r["amount"] or 0 for r in rows_all), 2)

    cursor.execute("SELECT id, name FROM customers ORDER BY name")
    customers = cursor.fetchall()
    cursor.execute("SELECT id, tool_name FROM mfg_tools ORDER BY tool_name")
    tools = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/sales_report.html", rows=rows, page=page, total_pages=total_pages, total=total,
        q=q, from_date=from_date, to_date=to_date, customer_id=customer_id, customers=customers,
        tool_id=tool_id, tools=tools, min_amount=min_amount, max_amount=max_amount, process=process, sort=sort, direction=direction,
        subtotal=subtotal, tax=tax, grand_total=grand_total)

@manufacturing.route("/manufacturing/reports/margin")
def margin_report():
    """Unified Margin Report - job-work cost = rod's proportional share + sum of job-stage
    vendor rates (same formula as batch_detail()'s "Manufacturing Cost" box); Resharpening
    cost = sum of its stage vendor rates only (no rod is ever consumed resharpening a tool).
    Both are computed the same way otherwise (revenue - cost, allocated per invoiced qty) and
    merged into one report/one set of totals, filterable by Process - by request, resharpening
    costs should show up in this report exactly like new-tool costs do."""
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    q = request.args.get("q", "")
    q_search = q.strip()
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")
    customer_id = request.args.get("customer_id", "")
    tool_id = request.args.get("tool_id", "")
    min_margin_pct = request.args.get("min_margin_pct", "")
    max_margin_pct = request.args.get("max_margin_pct", "")
    process = request.args.get("process", "")
    sort = request.args.get("sort", "invoice_date")
    direction = request.args.get("dir", "desc")

    conn = get_connection()
    cursor = conn.cursor()

    where_parts = []
    params = []
    if q_search:
        where_parts.append("(mfg_invoices.invoice_no LIKE ? OR customers.name LIKE ? OR mfg_tools.tool_name LIKE ?)")
        params += [f"%{q_search}%", f"%{q_search}%", f"%{q_search}%"]
    if from_date:
        where_parts.append("mfg_invoices.invoice_date >= ?")
        params.append(from_date)
    if to_date:
        where_parts.append("mfg_invoices.invoice_date <= ?")
        params.append(to_date)
    if customer_id:
        where_parts.append("mfg_invoices.customer_id = ?")
        params.append(customer_id)
    if tool_id:
        where_parts.append("mfg_job_batches.tool_id = ?")
        params.append(tool_id)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    rwhere_parts = []
    rparams = []
    if q_search:
        rwhere_parts.append("(mfg_resharpen_invoices.invoice_no LIKE ? OR customers.name LIKE ? OR mfg_tools.tool_name LIKE ?)")
        rparams += [f"%{q_search}%", f"%{q_search}%", f"%{q_search}%"]
    if from_date:
        rwhere_parts.append("mfg_resharpen_invoices.invoice_date >= ?")
        rparams.append(from_date)
    if to_date:
        rwhere_parts.append("mfg_resharpen_invoices.invoice_date <= ?")
        rparams.append(to_date)
    if customer_id:
        rwhere_parts.append("mfg_resharpen_invoices.customer_id = ?")
        rparams.append(customer_id)
    if tool_id:
        rwhere_parts.append("mfg_resharpen_batches.tool_id = ?")
        rparams.append(tool_id)
    rwhere = ("WHERE " + " AND ".join(rwhere_parts)) if rwhere_parts else ""

    all_rows = []
    resharpen_rows = []

    # batch's manufacturing cost isn't stored anywhere - same formula as batch_detail()'s
    # "Manufacturing Cost" box (rod's proportional share + sum of job-stage vendor rates),
    # recomputed here per line and allocated to the invoiced quantity
    if process != "resharpen":
        cursor.execute(f"""
            SELECT mfg_invoice_items.id, mfg_invoice_items.invoice_id, mfg_invoice_items.batch_id, mfg_invoice_items.quantity, mfg_invoice_items.bef_tax_amt,
                   mfg_invoice_items.rate, mfg_invoice_items.tax_rate, mfg_invoice_items.tax_amount, mfg_invoice_items.amount,
                   mfg_invoices.invoice_no, mfg_invoices.invoice_date,
                   customers.name as customer_name, mfg_tools.tool_name, mfg_tools.item_code, mfg_job_batches.batch_no,
                   mfg_job_batches.quantity as batch_quantity, mfg_job_batches.length_needed_mm,
                   mfg_job_batches.rod_id, mfg_rods.rate as rod_rate, mfg_rods.length_mm as rod_length_mm, mfg_rods.grade as rod_grade
            FROM mfg_invoice_items
            JOIN mfg_invoices ON mfg_invoice_items.invoice_id = mfg_invoices.id
            LEFT JOIN customers ON mfg_invoices.customer_id = customers.id
            JOIN mfg_job_batches ON mfg_invoice_items.batch_id = mfg_job_batches.id
            JOIN mfg_tools ON mfg_job_batches.tool_id = mfg_tools.id
            LEFT JOIN mfg_rods ON mfg_job_batches.rod_id = mfg_rods.id
            {where}
            ORDER BY mfg_invoices.invoice_date DESC, mfg_invoices.id DESC
        """, params)
        all_rows = cursor.fetchall()

    if process != "new":
        cursor.execute(f"""
            SELECT mfg_resharpen_invoice_items.id, mfg_resharpen_invoice_items.invoice_id, mfg_resharpen_invoice_items.batch_id, mfg_resharpen_invoice_items.quantity, mfg_resharpen_invoice_items.bef_tax_amt,
                   mfg_resharpen_invoice_items.rate, mfg_resharpen_invoice_items.tax_rate, mfg_resharpen_invoice_items.tax_amount, mfg_resharpen_invoice_items.amount,
                   mfg_resharpen_invoices.invoice_no, mfg_resharpen_invoices.invoice_date,
                   customers.name as customer_name, mfg_tools.tool_name, mfg_tools.item_code, mfg_resharpen_batches.batch_no,
                   mfg_resharpen_batches.quantity as batch_quantity
            FROM mfg_resharpen_invoice_items
            JOIN mfg_resharpen_invoices ON mfg_resharpen_invoice_items.invoice_id = mfg_resharpen_invoices.id
            LEFT JOIN customers ON mfg_resharpen_invoices.customer_id = customers.id
            JOIN mfg_resharpen_batches ON mfg_resharpen_invoice_items.batch_id = mfg_resharpen_batches.id
            JOIN mfg_tools ON mfg_resharpen_batches.tool_id = mfg_tools.id
            {rwhere}
            ORDER BY mfg_resharpen_invoices.invoice_date DESC, mfg_resharpen_invoices.id DESC
        """, rparams)
        resharpen_rows = cursor.fetchall()

    batch_ids = list({r["batch_id"] for r in all_rows})
    stage_costs = {}
    if batch_ids:
        placeholders = ",".join("?" * len(batch_ids))
        cursor.execute(f"""
            SELECT batch_id, COALESCE(SUM(rate), 0) as stage_cost
            FROM mfg_job_stages
            WHERE batch_id IN ({placeholders}) AND rate IS NOT NULL
            GROUP BY batch_id
        """, batch_ids)
        stage_costs = {r["batch_id"]: r["stage_cost"] for r in cursor.fetchall()}

    resharpen_batch_ids = list({r["batch_id"] for r in resharpen_rows})
    resharpen_stage_costs = {}
    if resharpen_batch_ids:
        placeholders = ",".join("?" * len(resharpen_batch_ids))
        cursor.execute(f"""
            SELECT batch_id, COALESCE(SUM(rate), 0) as stage_cost
            FROM mfg_resharpen_stages
            WHERE batch_id IN ({placeholders}) AND rate IS NOT NULL
            GROUP BY batch_id
        """, resharpen_batch_ids)
        resharpen_stage_costs = {r["batch_id"]: r["stage_cost"] for r in cursor.fetchall()}

    def line_cost(r):
        rod_cost = 0
        if r["rod_id"] and r["rod_length_mm"] and r["length_needed_mm"]:
            rod_cost = (r["length_needed_mm"] / r["rod_length_mm"]) * (r["rod_rate"] or 0)
        batch_total_cost = rod_cost + stage_costs.get(r["batch_id"], 0)
        per_unit_cost = (batch_total_cost / r["batch_quantity"]) if r["batch_quantity"] else 0
        return round(per_unit_cost * r["quantity"], 2)

    def resharpen_line_cost(r):
        # no rod ever consumed resharpening a tool - cost is purely the vendor stage rate(s)
        batch_total_cost = resharpen_stage_costs.get(r["batch_id"], 0)
        per_unit_cost = (batch_total_cost / r["batch_quantity"]) if r["batch_quantity"] else 0
        return round(per_unit_cost * r["quantity"], 2)

    report_rows_all = []
    for r in all_rows:
        cost = line_cost(r)
        revenue = round(r["bef_tax_amt"], 2)
        margin = round(revenue - cost, 2)
        margin_pct = round((margin / revenue) * 100, 1) if revenue else 0
        report_rows_all.append({
            "source": "new", "invoice_id": r["invoice_id"], "invoice_no": r["invoice_no"], "invoice_date": r["invoice_date"],
            "customer_name": r["customer_name"], "tool_name": r["tool_name"], "item_code": r["item_code"],
            "batch_id": r["batch_id"], "batch_no": r["batch_no"], "rod_grade": r["rod_grade"],
            "quantity": r["quantity"], "rate": r["rate"], "tax_rate": r["tax_rate"],
            "revenue": revenue, "tax_amount": round(r["tax_amount"], 2), "line_total": round(r["amount"], 2),
            "cost": cost, "margin": margin, "margin_pct": margin_pct,
        })
    for r in resharpen_rows:
        cost = resharpen_line_cost(r)
        revenue = round(r["bef_tax_amt"], 2)
        margin = round(revenue - cost, 2)
        margin_pct = round((margin / revenue) * 100, 1) if revenue else 0
        report_rows_all.append({
            "source": "resharpen", "invoice_id": r["invoice_id"], "invoice_no": r["invoice_no"], "invoice_date": r["invoice_date"],
            "customer_name": r["customer_name"], "tool_name": r["tool_name"], "item_code": r["item_code"],
            "batch_id": r["batch_id"], "batch_no": r["batch_no"], "rod_grade": None,
            "quantity": r["quantity"], "rate": r["rate"], "tax_rate": r["tax_rate"],
            "revenue": revenue, "tax_amount": round(r["tax_amount"], 2), "line_total": round(r["amount"], 2),
            "cost": cost, "margin": margin, "margin_pct": margin_pct,
        })

    if min_margin_pct:
        report_rows_all = [r for r in report_rows_all if r["margin_pct"] >= float(min_margin_pct)]
    if max_margin_pct:
        report_rows_all = [r for r in report_rows_all if r["margin_pct"] <= float(max_margin_pct)]

    sort_keys = {"invoice_no": "invoice_no", "invoice_date": "invoice_date", "customer_name": "customer_name",
                 "tool_name": "tool_name", "quantity": "quantity", "revenue": "revenue", "cost": "cost",
                 "margin": "margin", "margin_pct": "margin_pct"}
    sort_key = sort_keys.get(sort, "invoice_date")
    report_rows_all.sort(key=lambda r: (r[sort_key] is None, r[sort_key]), reverse=(direction == "desc"))

    total = len(report_rows_all)
    total_pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page
    rows = report_rows_all[offset:offset + per_page]

    total_revenue = round(sum(r["revenue"] for r in report_rows_all), 2)
    total_cost = round(sum(r["cost"] for r in report_rows_all), 2)
    total_margin = round(total_revenue - total_cost, 2)
    total_margin_pct = round((total_margin / total_revenue) * 100, 1) if total_revenue else 0

    cursor.execute("SELECT id, name FROM customers ORDER BY name")
    customers = cursor.fetchall()
    cursor.execute("SELECT id, tool_name FROM mfg_tools ORDER BY tool_name")
    tools = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/margin_report.html", rows=rows, page=page, total_pages=total_pages, total=total,
        q=q, from_date=from_date, to_date=to_date, customer_id=customer_id, tool_id=tool_id,
        min_margin_pct=min_margin_pct, max_margin_pct=max_margin_pct, process=process, sort=sort, direction=direction,
        customers=customers, tools=tools,
        total_revenue=total_revenue, total_cost=total_cost, total_margin=total_margin, total_margin_pct=total_margin_pct)

@manufacturing.route("/manufacturing/reports")
def reports_home():
    return render_template("manufacturing/reports_home.html")

@manufacturing.route("/manufacturing/reports/purchase")
def purchase_report():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    offset = (page - 1) * per_page
    q = request.args.get("q", "")
    q_search = q.strip()
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")
    supplier_id = request.args.get("supplier_id", "")
    item_type = request.args.get("item_type", "")
    min_amount = request.args.get("min_amount", "")
    max_amount = request.args.get("max_amount", "")
    sort = request.args.get("sort", "invoice_date")
    direction = request.args.get("dir", "desc")

    conn = get_connection()
    cursor = conn.cursor()

    where_parts = []
    params = []
    if q_search:
        where_parts.append("(mfg_purchase_invoices.invoice_no LIKE ? OR suppliers.name LIKE ? OR mfg_purchase_invoice_items.description LIKE ?)")
        params += [f"%{q_search}%", f"%{q_search}%", f"%{q_search}%"]
    if from_date:
        where_parts.append("mfg_purchase_invoices.invoice_date >= ?")
        params.append(from_date)
    if to_date:
        where_parts.append("mfg_purchase_invoices.invoice_date <= ?")
        params.append(to_date)
    if supplier_id:
        where_parts.append("mfg_purchase_invoices.supplier_id = ?")
        params.append(supplier_id)
    if item_type:
        where_parts.append("mfg_purchase_invoice_items.item_type = ?")
        params.append(item_type)
    if min_amount:
        where_parts.append("mfg_purchase_invoice_items.amount >= ?")
        params.append(float(min_amount))
    if max_amount:
        where_parts.append("mfg_purchase_invoice_items.amount <= ?")
        params.append(float(max_amount))
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    # line-item grain (one row per purchased rod/item/tool, not one row per invoice) so
    # every description/qty/rate/tax is visible directly, plus the PO/GRN it traces back to
    sort_columns = {
        "invoice_no": "mfg_purchase_invoices.invoice_no",
        "invoice_date": "mfg_purchase_invoices.invoice_date",
        "supplier_name": "supplier_name",
        "quantity": "mfg_purchase_invoice_items.quantity",
        "rate": "mfg_purchase_invoice_items.rate",
        "amount": "mfg_purchase_invoice_items.amount",
    }
    order_col = sort_columns.get(sort, "mfg_purchase_invoices.invoice_date")
    order_dir = "ASC" if direction == "asc" else "DESC"

    base_from = """
        FROM mfg_purchase_invoice_items
        JOIN mfg_purchase_invoices ON mfg_purchase_invoice_items.invoice_id = mfg_purchase_invoices.id
        LEFT JOIN suppliers ON mfg_purchase_invoices.supplier_id = suppliers.id
    """

    cursor.execute(f"""
        SELECT mfg_purchase_invoice_items.*, mfg_purchase_invoices.invoice_no, mfg_purchase_invoices.invoice_date,
               mfg_purchase_invoices.status, mfg_purchase_invoices.sup_invoice_no,
               suppliers.name as supplier_name,
               mfg_purchase_orders.order_no, mfg_purchase_grn.grn_no
        {base_from}
        LEFT JOIN mfg_purchase_orders ON mfg_purchase_invoices.order_id = mfg_purchase_orders.id
        LEFT JOIN mfg_purchase_grn ON mfg_purchase_invoices.grn_id = mfg_purchase_grn.id
        {where}
        ORDER BY {order_col} {order_dir}, mfg_purchase_invoice_items.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    rows = cursor.fetchall()

    cursor.execute(f"SELECT COUNT(*) {base_from} {where}", params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    cursor.execute(f"""
        SELECT COALESCE(SUM(mfg_purchase_invoice_items.bef_tax_amt), 0) as subtotal,
               COALESCE(SUM(mfg_purchase_invoice_items.tax_amount), 0) as tax,
               COALESCE(SUM(mfg_purchase_invoice_items.amount), 0) as grand_total
        {base_from}
        {where}
    """, params)
    totals = cursor.fetchone()

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/purchase_report.html", rows=rows, page=page, total_pages=total_pages, total=total,
        q=q, from_date=from_date, to_date=to_date, supplier_id=supplier_id, item_type=item_type,
        min_amount=min_amount, max_amount=max_amount, sort=sort, direction=direction, suppliers=suppliers,
        subtotal=round(totals["subtotal"], 2), tax=round(totals["tax"], 2), grand_total=round(totals["grand_total"], 2))

@manufacturing.route("/manufacturing/reports/stock")
def stock_report():
    q = request.args.get("q", "")
    q_search = q.strip()

    conn = get_connection()
    cursor = conn.cursor()

    where = ""
    params = []
    if q_search:
        where = "WHERE grade LIKE ?"
        params = [f"%{q_search}%"]
    cursor.execute(f"""
        SELECT grade,
               COUNT(*) as rod_count,
               COALESCE(SUM(remaining_length_mm), 0) as total_remaining_mm,
               COALESCE(SUM(CASE WHEN length_mm > 0 THEN remaining_length_mm / length_mm * rate ELSE 0 END), 0) as est_value
        FROM mfg_rods
        {where}
        GROUP BY grade
        HAVING total_remaining_mm > 0
        ORDER BY grade
    """, params)
    rod_summary = cursor.fetchall()

    # full rod-by-rod detail (not just the per-grade rollup above) so every individual
    # rod - its date, diameter, supplier, rate, and remaining length - is visible
    where_rods = "WHERE mfg_rods.remaining_length_mm > 0"
    params_rods = []
    if q_search:
        where_rods += " AND mfg_rods.grade LIKE ?"
        params_rods.append(f"%{q_search}%")
    cursor.execute(f"""
        SELECT mfg_rods.*, suppliers.name as supplier_name,
               CASE WHEN mfg_rods.length_mm > 0 THEN mfg_rods.remaining_length_mm / mfg_rods.length_mm * mfg_rods.rate ELSE 0 END as est_value
        FROM mfg_rods
        LEFT JOIN suppliers ON mfg_rods.supplier_id = suppliers.id
        {where_rods}
        ORDER BY mfg_rods.grade, mfg_rods.rod_date DESC
    """, params_rods)
    rod_detail = cursor.fetchall()
    rod_detail_by_grade = {}
    for r in rod_detail:
        rod_detail_by_grade.setdefault(r["grade"] or "Rod", []).append(r)

    where2 = ""
    params2 = []
    if q_search:
        where2 = "WHERE (tool_name LIKE ? OR item_code LIKE ?)"
        params2 = [f"%{q_search}%", f"%{q_search}%"]
    cursor.execute(f"SELECT id, item_code, tool_name, tool_type FROM mfg_tools {where2} ORDER BY tool_name", params2)
    tools = cursor.fetchall()
    # every tool with any activity at all (not just ones with finished stock in hand) -
    # a stock report should show the full picture (at-vendor, ready-to-send, etc), not
    # just what's sitting finished
    tool_summary = []
    for t in tools:
        s = compute_tool_stock(cursor, t["id"])
        has_activity = (s["not_started"] or s["finished_in_hand"] or s["delivered"]
                        or any(s["stage_qty"].values()) or any(s["ready_qty"].values()))
        if has_activity:
            tool_summary.append({"tool": t, **s})

    total_rod_value = round(sum(r["est_value"] for r in rod_summary), 2)
    total_rod_length = round(sum(r["total_remaining_mm"] for r in rod_summary), 2)
    total_finished_qty = round(sum(t["finished_in_hand"] for t in tool_summary), 2)

    conn.close()
    return render_template("manufacturing/stock_report.html", rod_summary=rod_summary, rod_detail_by_grade=rod_detail_by_grade,
        tool_summary=tool_summary, q=q,
        total_rod_value=total_rod_value, total_rod_length=total_rod_length, total_finished_qty=total_finished_qty)


@manufacturing.route("/manufacturing/api/supplier-grns/<int:supplier_id>")
def api_mfg_supplier_grns(supplier_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, grn_no FROM mfg_purchase_grn
        WHERE supplier_id = ?
        ORDER BY grn_date DESC
    """, (supplier_id,))
    grns = cursor.fetchall()
    conn.close()
    return {"grns": [{"id": g["id"], "grn_no": g["grn_no"]} for g in grns]}


# ============================================
# PURCHASE RETURN (stock OUT - unlike PO/GRN, a rod return acts on stock that
# already exists, so its row picks a real mfg_rods entry instead of free-typing
# grade/diameter/length, and its remaining_length_mm is decremented directly)
# ============================================

def generate_return_no(return_date, cursor):
    date_part = return_date.replace("-", "")
    cursor.execute("SELECT return_no FROM mfg_purchase_returns WHERE return_no LIKE ?", (f"MPR-{date_part}-%",))
    existing_numbers = []
    for row in cursor.fetchall():
        try:
            existing_numbers.append(int(row["return_no"].split("-")[-1]))
        except ValueError:
            pass
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"MPR-{date_part}-{str(next_number).zfill(4)}"

@manufacturing.route("/manufacturing/purchase-returns")
def purchase_returns_list():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    offset = (page - 1) * per_page
    q = request.args.get("q", "")
    q_search = q.strip()
    supplier_id = request.args.get("supplier_id", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")

    conn = get_connection()
    cursor = conn.cursor()

    where_parts = []
    params = []
    if q_search:
        where_parts.append("(mfg_purchase_returns.return_no LIKE ? OR suppliers.name LIKE ?)")
        params += [f"%{q_search}%", f"%{q_search}%"]
    if supplier_id:
        where_parts.append("mfg_purchase_returns.supplier_id = ?")
        params.append(supplier_id)
    if from_date:
        where_parts.append("mfg_purchase_returns.return_date >= ?")
        params.append(from_date)
    if to_date:
        where_parts.append("mfg_purchase_returns.return_date <= ?")
        params.append(to_date)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    cursor.execute(f"""
        SELECT mfg_purchase_returns.*, suppliers.name as supplier_name
        FROM mfg_purchase_returns
        LEFT JOIN suppliers ON mfg_purchase_returns.supplier_id = suppliers.id
        {where}
        ORDER BY mfg_purchase_returns.return_date DESC, mfg_purchase_returns.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    returns = cursor.fetchall()

    cursor.execute(f"""
        SELECT COUNT(*) FROM mfg_purchase_returns
        LEFT JOIN suppliers ON mfg_purchase_returns.supplier_id = suppliers.id
        {where}
    """, params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/purchase_returns.html", returns=returns, page=page, total_pages=total_pages, total=total,
        q=q, supplier_id=supplier_id, from_date=from_date, to_date=to_date, suppliers=suppliers)

@manufacturing.route("/manufacturing/purchase-returns/add", methods=["GET", "POST"])
def add_purchase_return():
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        return_date = request.form["return_date"]
        supplier_id = request.form.get("supplier_id") or None
        transporter_id = request.form.get("transporter_id") or None
        reason = request.form.get("reason")
        remarks = request.form.get("remarks")

        item_types = request.form.getlist("item_type[]")
        item_ids = request.form.getlist("item_id[]")
        descriptions = request.form.getlist("description[]")
        rod_grades = request.form.getlist("rod_grade[]")
        rod_diameters = request.form.getlist("rod_diameter_mm[]")
        rod_lengths = request.form.getlist("rod_length_mm[]")
        quantities = request.form.getlist("quantity[]")
        unit_ids = request.form.getlist("unit_id[]")
        rates = request.form.getlist("rate[]")
        hsn_codes = request.form.getlist("hsn_code[]")
        tax_rates = request.form.getlist("tax_rate[]")

        items = []
        for i in range(len(item_types)):
            if not quantities[i] or float(quantities[i] or 0) <= 0:
                continue
            quantity = float(quantities[i])
            rate = float(rates[i]) if rates[i] else 0
            tax_rate = float(tax_rates[i]) if tax_rates[i] else 18
            bef_tax_amt = round(quantity * rate, 2)
            tax_amount = round(bef_tax_amt * tax_rate / 100, 2)
            amount = round(bef_tax_amt + tax_amount, 2)
            items.append({
                "item_type": item_types[i],
                "item_id": item_ids[i] or None,
                "description": descriptions[i],
                "rod_grade": rod_grades[i] or None,
                "rod_diameter_mm": float(rod_diameters[i]) if rod_diameters[i] else None,
                "rod_length_mm": float(rod_lengths[i]) if rod_lengths[i] else None,
                "quantity": quantity,
                "unit_id": unit_ids[i] or None,
                "rate": rate,
                "hsn_code": hsn_codes[i] or None,
                "tax_rate": tax_rate,
                "bef_tax_amt": bef_tax_amt,
                "tax_amount": tax_amount,
                "amount": amount,
            })

        if not items:
            flash("Add at least one line item with a quantity.", "danger")
            conn.close()
            return redirect(url_for("manufacturing.add_purchase_return"))

        # validate rod returns don't exceed what's actually left on that rod
        for it in items:
            if it["item_type"] == "rod" and it["item_id"]:
                cursor.execute("SELECT remaining_length_mm, grade FROM mfg_rods WHERE id = ?", (it["item_id"],))
                rod_row = cursor.fetchone()
                remaining = rod_row["remaining_length_mm"] if rod_row else 0
                if it["quantity"] > remaining:
                    flash(f"Cannot return {it['quantity']:.1f}mm of {rod_row['grade'] if rod_row else 'that rod'} - only {remaining:.1f}mm is left in stock.", "danger")
                    conn.close()
                    return redirect(url_for("manufacturing.add_purchase_return"))

        total_amount = round(sum(i["amount"] for i in items), 2)
        cursor.execute("BEGIN IMMEDIATE")
        return_no = generate_return_no(return_date, cursor)
        cursor.execute("""
            INSERT INTO mfg_purchase_returns (return_no, return_date, supplier_id, transporter_id, reason, remarks, total_amount)
            VALUES (?,?,?,?,?,?,?)
        """, (return_no, return_date, supplier_id, transporter_id, reason, remarks, total_amount))
        return_id = cursor.lastrowid

        for it in items:
            cursor.execute("""
                INSERT INTO mfg_purchase_return_items
                (return_id, item_type, item_id, description, rod_grade, rod_diameter_mm, rod_length_mm,
                 quantity, unit_id, rate, hsn_code, tax_rate, bef_tax_amt, tax_amount, amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (return_id, it["item_type"], it["item_id"], it["description"], it["rod_grade"], it["rod_diameter_mm"],
                  it["rod_length_mm"], it["quantity"], it["unit_id"], it["rate"], it["hsn_code"], it["tax_rate"],
                  it["bef_tax_amt"], it["tax_amount"], it["amount"]))

            if it["item_type"] == "rod" and it["item_id"]:
                cursor.execute("UPDATE mfg_rods SET remaining_length_mm = remaining_length_mm - ? WHERE id = ?",
                               (it["quantity"], it["item_id"]))
                cursor.execute("""
                    INSERT INTO mfg_stock_movements (item_type, item_id, movement_type, quantity, source_type, source_id, source_no, movement_date)
                    VALUES (?,?,?,?,?,?,?,?)
                """, ("rod", it["item_id"], "out", it["quantity"], "purchase_return", return_id, return_no, return_date))
            else:
                cursor.execute("""
                    INSERT INTO mfg_stock_movements (item_type, item_id, movement_type, quantity, source_type, source_id, source_no, movement_date)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (it["item_type"], it["item_id"], "out", it["quantity"], "purchase_return", return_id, return_no, return_date))

        conn.commit()
        conn.close()
        flash(f"Purchase Return {return_no} created - stock updated!", "success")
        return redirect(url_for("manufacturing.view_purchase_return", return_id=return_id))

    cursor.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cursor.fetchall()
    cursor.execute("SELECT id, item_name, unit_id, hsn_code, purchase_price, purchase_discount_percent FROM mfg_items ORDER BY item_name")
    items_master = cursor.fetchall()
    cursor.execute("SELECT id, item_code, tool_name, tool_type, purchase_price, purchase_discount_percent FROM mfg_tools ORDER BY tool_name")
    tools_master = cursor.fetchall()
    cursor.execute("SELECT id, rod_date, grade, diameter_mm, remaining_length_mm, rate FROM mfg_rods WHERE remaining_length_mm > 0 ORDER BY rod_date DESC")
    rods_master = cursor.fetchall()
    cursor.execute("SELECT id, unit_name FROM units ORDER BY unit_name")
    units = cursor.fetchall()
    cursor.execute("SELECT id, name FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/add_purchase_return.html", suppliers=suppliers, items_master=items_master, tools_master=tools_master, rods_master=rods_master, units=units, transporters=transporters)

@manufacturing.route("/manufacturing/purchase-returns/<int:return_id>")
def view_purchase_return(return_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mfg_purchase_returns.*, suppliers.name as supplier_name, transporters.name as transporter_name
        FROM mfg_purchase_returns
        LEFT JOIN suppliers ON mfg_purchase_returns.supplier_id = suppliers.id
        LEFT JOIN transporters ON mfg_purchase_returns.transporter_id = transporters.id
        WHERE mfg_purchase_returns.id = ?
    """, (return_id,))
    ret = cursor.fetchone()
    cursor.execute("SELECT * FROM mfg_purchase_return_items WHERE return_id = ?", (return_id,))
    items = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/view_purchase_return.html", ret=ret, items=items)

@manufacturing.route("/manufacturing/purchase-returns/<int:return_id>/edit", methods=["GET", "POST"])
def edit_purchase_return(return_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_purchase_returns WHERE id = ?", (return_id,))
    ret = cursor.fetchone()
    if not ret:
        conn.close()
        flash("Purchase Return not found.", "danger")
        return redirect(url_for("manufacturing.purchase_returns_list"))

    if request.method == "POST":
        transporter_id = request.form.get("transporter_id") or None
        reason = request.form.get("reason")
        remarks = request.form.get("remarks")
        cursor.execute("UPDATE mfg_purchase_returns SET transporter_id=?, reason=?, remarks=? WHERE id=?",
                       (transporter_id, reason, remarks, return_id))
        conn.commit()
        conn.close()
        flash("Purchase Return updated successfully!", "success")
        return redirect(url_for("manufacturing.view_purchase_return", return_id=return_id))

    cursor.execute("SELECT id, name FROM transporters ORDER BY name")
    transporters = cursor.fetchall()
    conn.close()
    return render_template("manufacturing/edit_purchase_return.html", ret=ret, transporters=transporters)

@manufacturing.route("/manufacturing/purchase-returns/delete/<int:return_id>")
def delete_purchase_return(return_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_purchase_returns WHERE id = ?", (return_id,))
    ret = cursor.fetchone()
    if not ret:
        conn.close()
        flash("Purchase Return not found.", "danger")
        return redirect(url_for("manufacturing.purchase_returns_list"))

    cursor.execute("SELECT * FROM mfg_purchase_return_items WHERE return_id = ?", (return_id,))
    items = cursor.fetchall()

    cursor.execute("BEGIN IMMEDIATE")
    for it in items:
        if it["item_type"] == "rod" and it["item_id"]:
            cursor.execute("UPDATE mfg_rods SET remaining_length_mm = remaining_length_mm + ? WHERE id = ?",
                           (it["quantity"], it["item_id"]))
    cursor.execute("DELETE FROM mfg_stock_movements WHERE source_type='purchase_return' AND source_id=?", (return_id,))
    cursor.execute("DELETE FROM mfg_purchase_return_items WHERE return_id = ?", (return_id,))
    cursor.execute("DELETE FROM mfg_purchase_returns WHERE id = ?", (return_id,))
    conn.commit()
    conn.close()
    flash("Purchase Return deleted successfully! Returned stock has been restored.", "danger")
    return redirect(url_for("manufacturing.purchase_returns_list"))

@manufacturing.route("/manufacturing/purchase-returns/<int:return_id>/print")
def print_purchase_return(return_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mfg_purchase_returns WHERE id = ?", (return_id,))
    ret = cursor.fetchone()
    cursor.execute("SELECT * FROM suppliers WHERE id = ?", (ret["supplier_id"],))
    party = cursor.fetchone()
    transporter = None
    if ret["transporter_id"]:
        cursor.execute("SELECT * FROM transporters WHERE id = ?", (ret["transporter_id"],))
        transporter = cursor.fetchone()
    cursor.execute("SELECT * FROM mfg_purchase_return_items WHERE return_id = ?", (return_id,))
    item_rows = cursor.fetchall()
    conn.close()

    items = [{"description": r["description"], "quantity": r["quantity"], "rate": r["rate"], "amount": r["amount"]} for r in item_rows]
    return render_template("manufacturing/print_document.html",
        doc_title="Purchase Return", doc_no=ret["return_no"], doc_date=ret["return_date"],
        party=party, items=items, show_pricing=True, show_batch_no=False, transporter=transporter,
        subtotal=round(ret["total_amount"] - sum(i["tax_amount"] for i in item_rows), 2),
        tax_amount=round(sum(i["tax_amount"] for i in item_rows), 2),
        total=ret["total_amount"], remarks=ret["remarks"], back_url=f"/manufacturing/purchase-returns/{return_id}")
