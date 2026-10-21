import os
import secrets

from flask import Flask, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from database import create_tables, get_connection, is_postgres_configured
from modules.auth import auth, system_required


def _get_secret_key(environ=None):
    environ = os.environ if environ is None else environ
    secret_key = environ.get("FLASK_SECRET_KEY", "").strip()
    if secret_key:
        return secret_key
    if environ.get("APP_ENV", "").strip().lower() == "production":
        raise RuntimeError("FLASK_SECRET_KEY must be set when APP_ENV=production")
    return secrets.token_hex(32)


def _configure_production(app, environ=None):
    environ = os.environ if environ is None else environ
    if environ.get("APP_ENV", "").strip().lower() != "production":
        return

    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PREFERRED_URL_SCHEME="https",
    )
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


app = Flask(__name__)
app.secret_key = _get_secret_key()
_configure_production(app)
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
from modules.hiring import hiring
app.register_blueprint(hiring)
from modules.delivery import delivery
app.register_blueprint(delivery)
from modules.permissions import has_permission
app.jinja_env.globals["has_perm"] = has_permission


def _run_development_server(environ=None):
    environ = os.environ if environ is None else environ
    if (
        environ.get("APP_ENV", "").strip().lower() == "production"
        or is_postgres_configured()
    ):
        raise RuntimeError(
            "Do not start production or PostgreSQL with `python app.py`; "
            "use a WSGI server and the explicit migration command."
        )
    create_tables()
    app.run(debug=False)


@app.route("/service-worker.js")
def delivery_service_worker():
    response = send_from_directory(
        app.static_folder,
        "delivery-service-worker.js",
        mimetype="application/javascript",
    )
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/")
@system_required("distribution")
def index():
    conn = get_connection()
    try:
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
    finally:
        conn.close()
    return render_template("dashboard.html",
        total_products=total_products,
        total_customers=total_customers,
        total_suppliers=total_suppliers,
        total_sales_invoices=total_sales_invoices,
        total_purchase_orders=total_purchase_orders)


@app.route("/flow")
@system_required("manufacturing")
def flow():
    conn = get_connection()
    try:
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
    finally:
        conn.close()

    return render_template("flow.html", counts=counts)

if __name__ == "__main__":
    _run_development_server()
