from datetime import date

from flask import Blueprint, abort, render_template, request

from database import get_connection, is_postgres_configured
from modules.auth import require_system

stock = Blueprint("stock", __name__)
stock.before_request(require_system("distribution"))


def _placeholder():
    return "%s" if is_postgres_configured() else "?"


def add_stock_movement(
    cursor,
    product_id,
    movement_type,
    quantity,
    source_type,
    source_id,
    source_no,
    movement_date,
    rate=0,
):
    amount = quantity * rate
    placeholder = _placeholder()
    cursor.execute(
        f"""
        INSERT INTO stock_movements (
            product_id, movement_type, quantity, source_type, source_id,
            source_no, movement_date, rate, amount
        ) VALUES ({", ".join([placeholder] * 9)})
        """,
        (
            product_id,
            movement_type,
            quantity,
            source_type,
            source_id,
            source_no,
            movement_date,
            rate,
            round(amount, 2),
        ),
    )


def get_current_stock(cursor, product_id):
    placeholder = _placeholder()
    cursor.execute(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN movement_type = 'IN' THEN quantity ELSE 0 END), 0) -
            COALESCE(SUM(CASE WHEN movement_type = 'OUT' THEN quantity ELSE 0 END), 0)
        FROM stock_movements
        WHERE product_id = {placeholder}
        """,
        (product_id,),
    )
    result = cursor.fetchone()[0]
    return result if result else 0


@stock.route("/stock")
def stock_list():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("search", "")
    per_page = 20
    offset = (page - 1) * per_page
    placeholder = _placeholder()
    search_term = f"%{search}%"
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT products.*,
                   categories.category_name,
                   COALESCE((SELECT SUM(quantity) FROM stock_movements
                             WHERE product_id = products.id AND movement_type = 'IN'), 0) as total_received,
                   COALESCE((SELECT SUM(quantity) FROM stock_movements
                             WHERE product_id = products.id AND movement_type = 'OUT'), 0) as total_issued,
                   COALESCE((SELECT SUM(CASE WHEN movement_type = 'IN' THEN quantity ELSE -quantity END)
                             FROM stock_movements WHERE product_id = products.id), 0) as current_stock
            FROM products
            LEFT JOIN categories ON products.category_id = categories.id
            WHERE LOWER(products.product_name) LIKE LOWER({placeholder})
            ORDER BY LOWER(products.product_name), products.id
            LIMIT {placeholder} OFFSET {placeholder}
            """,
            (search_term, per_page, offset),
        )
        products = cursor.fetchall()
        cursor.execute(
            f"SELECT COUNT(*) FROM products WHERE LOWER(product_name) LIKE LOWER({placeholder})",
            (search_term,),
        )
        total = cursor.fetchone()[0]
        total_pages = (total + per_page - 1) // per_page
    finally:
        conn.close()

    return render_template(
        "stock/stock_list.html",
        products=products,
        page=page,
        total_pages=total_pages,
        search=search,
    )


@stock.route("/stock/ledger/<int:product_id>")
def stock_ledger(product_id):
    module_filter = request.args.get("module", "")
    sort_by = request.args.get("sort", "date_asc")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")
    postgres = is_postgres_configured()
    placeholder = "%s" if postgres else "?"
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM products WHERE id = {placeholder}", (product_id,)
        )
        product = cursor.fetchone()
        if product is None:
            abort(404)

        query = f"SELECT * FROM stock_movements WHERE product_id = {placeholder}"
        params = [product_id]
        if module_filter:
            query += f" AND source_type = {placeholder}"
            params.append(module_filter)
        for field, value, operator in (
            ("movement_date", from_date, ">="),
            ("movement_date", to_date, "<="),
        ):
            if value:
                if postgres:
                    try:
                        value = date.fromisoformat(value)
                    except ValueError:
                        abort(400)
                query += f" AND {field} {operator} {placeholder}"
                params.append(value)

        sort_options = {
            "date_asc": "movement_date ASC, id ASC",
            "date_desc": "movement_date DESC, id DESC",
            "amount_desc": "amount DESC",
            "amount_asc": "amount ASC",
        }
        query += " ORDER BY " + sort_options.get(sort_by, "movement_date ASC, id ASC")
        cursor.execute(query, params)
        movements = cursor.fetchall()
    finally:
        conn.close()

    module_names = {
        "opening": "Opening Stock",
        "grn": "Purchase GRN",
        "challan": "Sales Challan",
        "sales_invoice": "Sales Invoice",
        "sales_return": "Sales Return",
        "purchase_return": "Purchase Return",
    }
    return render_template(
        "stock/stock_ledger.html",
        product=product,
        movements=movements,
        module_names=module_names,
        module_filter=module_filter,
        sort_by=sort_by,
        from_date=from_date,
        to_date=to_date,
    )


@stock.route("/api/products-with-stock")
def api_products_with_stock():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
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
            FROM products
            LEFT JOIN categories ON products.category_id = categories.id
            LEFT JOIN units ON products.unit_id = units.id
            LEFT JOIN hsn ON products.hsn_id = hsn.id
            ORDER BY LOWER(products.product_name), products.id
            """
        )
        products = cursor.fetchall()
    finally:
        conn.close()

    return {
        "products": [
            {
                "id": product["id"],
                "name": product["product_name"],
                "unit_id": product["unit_id"],
                "hsn_id": product["hsn_id"],
                "tax_rate": product["tax_rate"],
                "sale_price": product["sale_price"],
                "purchase_price": product["purchase_price"],
                "category": product["category_name"],
                "unit": product["unit_name"],
                "hsn": product["hsn_code"],
                "stock": product["current_stock"],
            }
            for product in products
        ]
    }
