from flask import Blueprint
from flask import render_template, request, redirect, url_for, flash
from database import get_connection
stock = Blueprint("stock", __name__)
from modules.auth import require_system
stock.before_request(require_system("distribution"))
from modules.permissions import enforce_permissions
stock.before_request(enforce_permissions)


def add_stock_movement(cursor, product_id, movement_type, quantity, source_type, source_id, source_no, movement_date, rate=0):
    amount = quantity * rate
    cursor.execute("""
        INSERT INTO stock_movements (product_id, movement_type, quantity, source_type, source_id, source_no, movement_date, rate, amount)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (product_id, movement_type, quantity, source_type, source_id, source_no, movement_date, rate, round(amount, 2)))

def get_current_stock(cursor, product_id):
    cursor.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN movement_type = 'IN' THEN quantity ELSE 0 END), 0) -
            COALESCE(SUM(CASE WHEN movement_type = 'OUT' THEN quantity ELSE 0 END), 0)
        FROM stock_movements
        WHERE product_id = ?
    """, (product_id,))
    result = cursor.fetchone()[0]
    return result if result else 0

@stock.route("/stock")
def stock_list():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("search", "")
    per_page = 20
    offset = (page - 1) * per_page
    conn = get_connection()
    cursor = conn.cursor()
    search_term = "%" + search + "%"
    cursor.execute("""
        SELECT products.*,
               categories.category_name,
               COALESCE((SELECT SUM(quantity) FROM stock_movements WHERE product_id = products.id AND movement_type='IN'), 0) as total_received,
               COALESCE((SELECT SUM(quantity) FROM stock_movements WHERE product_id = products.id AND movement_type='OUT'), 0) as total_issued,
               COALESCE((SELECT SUM(CASE WHEN movement_type='IN' THEN quantity ELSE -quantity END)
                         FROM stock_movements WHERE product_id = products.id), 0) as current_stock
        FROM products
        LEFT JOIN categories ON products.category_id = categories.id
        WHERE products.product_name LIKE ?
        ORDER BY products.product_name
        LIMIT ? OFFSET ?
    """, (search_term, per_page, offset))
    products = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) FROM products WHERE product_name LIKE ?", (search_term,))
    total = cursor.fetchone()[0]
    total_pages = (total + per_page - 1) // per_page
    conn.close()
    return render_template("stock/stock_list.html", products=products, page=page, total_pages=total_pages, search=search)

@stock.route("/stock/ledger/<int:product_id>")
def stock_ledger(product_id):
    module_filter = request.args.get("module", "")
    sort_by = request.args.get("sort", "date_asc")
    from_date = request.args.get("from_date","")
    to_date = request.args.get("to_date", "")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    query = "SELECT * FROM stock_movements WHERE product_id = ?"
    params = [product_id]
    if module_filter:
        query += " AND source_type = ?"
        params.append(module_filter)
    if from_date:
        query += " AND movement_date >= ?"
        params.append(from_date)
    if to_date:
        query += " AND movement_date <= ?"
        params.append(to_date)
    sort_options = {
        "date_asc": "movement_date ASC, id ASC",
        "date_desc": "movement_date DESC, id DESC",
        "amount_desc": "amount DESC",
        "amount_asc": "amount ASC"
    }
    order_by = sort_options.get(sort_by, "movement_date ASC, id ASC")
    query += " ORDER BY " + order_by
    cursor.execute(query, params)
    movements = cursor.fetchall()
    conn.close()
    module_names = {
        "opening": "Opening Stock",
        "grn": "Purchase GRN",
        "challan": "Sales Challan",
        "sales_invoice": "Sales Invoice",
        "sales_return": "Sales Return",
        "purchase_return": "Purchase Return"
    }
    return render_template("stock/stock_ledger.html", product=product, movements=movements, module_names=module_names, module_filter=module_filter, sort_by=sort_by, from_date=from_date, to_date=to_date)
                        
@stock.route("/api/products-with-stock")
def api_products_with_stock():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT products.id,
               products.product_name,
               products.unit_id,
               products.hsn_id,
               products.tax_rate,
               products.sale_price,
               products.purchase_price,
               categories.category_name,
               units.unit_name,
               hsn.hsn_code,
               COALESCE((SELECT SUM(CASE WHEN movement_type = 'IN' THEN quantity ELSE -quantity END)
                         FROM stock_movements WHERE product_id = products.id), 0) as current_stock
        FROM Products
        LEFT JOIN categories ON products.category_id = categories.id
        LEFT JOIN units ON products.unit_id = units.id
        LEFT JOIN hsn ON products.hsn_id = hsn.id
        ORDER BY products.product_name
    """)
    products = cursor.fetchall()
    conn.close()
    result = []
    for p in products:
        result.append({
            "id": p["id"],
            "name": p["product_name"],
            "unit_id": p["unit_id"],
            "hsn_id": p["hsn_id"],
            "tax_rate": p["tax_rate"],
            "sale_price": p["sale_price"],
            "purchase_price": p["purchase_price"],
            "category": p["category_name"],
            "unit": p["unit_name"],
            "hsn": p["hsn_code"],
            "stock": p["current_stock"]
        })
    return {"products": result}