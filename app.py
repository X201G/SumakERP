from flask import Flask, render_template
from database import create_tables, get_connection
from modules.auth import auth, system_required

app = Flask(__name__)
app.secret_key = "sumak_erp_2026"
app.register_blueprint(auth)
from modules.masters import masters
app.register_blueprint(masters)
from modules.purchases import purchases
app.register_blueprint(purchases)
from modules.sales import sales
app.register_blueprint(sales)
from modules.stock import stock
app.register_blueprint(stock)
from modules.accounting import accounting
app.register_blueprint(accounting)
from modules.manufacturing import manufacturing
app.register_blueprint(manufacturing)
from modules.manufacturing_accounting import manufacturing_accounting
app.register_blueprint(manufacturing_accounting)
from modules.resharpening import resharpening
app.register_blueprint(resharpening)
from modules.permissions import has_permission
app.jinja_env.globals["has_perm"] = has_permission


@app.route("/")
@system_required("distribution")
def index():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM products")
    total_products = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM customers")
    total_customers = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM suppliers")
    total_suppliers = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM sales_invoices")
    total_sales_invoices = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM purchase_orders")
    total_purchase_orders = cursor.fetchone()[0]
    conn.close()
    return render_template("dashboard.html",
        total_products=total_products,
        total_customers=total_customers,
        total_suppliers=total_suppliers,
        total_sales_invoices=total_sales_invoices,
        total_purchase_orders=total_purchase_orders)

if __name__ == "__main__":
    create_tables()
    app.run(debug=True)
