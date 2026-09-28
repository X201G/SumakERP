from flask import Flask, redirect, render_template, request, session, url_for
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
from modules.projects import projects
app.register_blueprint(projects)
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


@app.route("/flow")
def flow():
    if not session.get("user_id"):
        return redirect(url_for("auth.login", next=request.path))

    conn = get_connection()
    cursor = conn.cursor()

    counts = {
        "leads": 0,
        "leads_not_converted": 0,
        "orders": 0,
        "pre_production": 0,
        "production": 0,
        "post_production": 0,
        "packaging": 0,
        "delivery": 0,
    }
    for stage, table in (
        ("orders", "mfg_customer_orders"),
        ("pre_production", "mfg_job_batches"),
        ("production", "mfg_job_stages"),
        ("post_production", "mfg_challans"),
    ):
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        counts[stage] = cursor.fetchone()[0]
    conn.close()

    return render_template("flow.html", counts=counts)

if __name__ == "__main__":
    create_tables()
    app.run(debug=True)
