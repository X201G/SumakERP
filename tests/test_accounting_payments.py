import sys
from datetime import date
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module
import database
import modules.accounting as accounting_module
import modules.document_database as document_database


@pytest.fixture
def payment_client(tmp_path, monkeypatch):
    previous_database = database.DATABASE
    previous_testing = app_module.app.config.get("TESTING")
    database.close_postgres_pool()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database.DATABASE = str(tmp_path / "payments_test.db")
    database.create_tables()
    connection = database.get_connection()
    try:
        user_id = connection.execute(
            """INSERT INTO users (username, password_hash, full_name, role, system)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "payment_admin",
                generate_password_hash("payment-password"),
                "Payment Admin",
                "admin",
                "distribution",
            ),
        ).lastrowid
        other_user_id = connection.execute(
            """INSERT INTO users (username, password_hash, full_name, role, system)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "payment_other",
                generate_password_hash("other-password"),
                "Other Admin",
                "admin",
                "manufacturing",
            ),
        ).lastrowid
        customer_id = connection.execute(
            "INSERT INTO customers (name) VALUES (?)", ("Customer One",)
        ).lastrowid
        supplier_id = connection.execute(
            "INSERT INTO suppliers (name) VALUES (?)", ("Supplier One",)
        ).lastrowid
        invoice_id = connection.execute(
            """INSERT INTO sales_invoices
               (invoice_no, invoice_date, customer_id, total_amount)
               VALUES (?, ?, ?, ?)""",
            ("SI-20261018-0001", "2026-10-18", customer_id, 100),
        ).lastrowid
        purchase_invoice_id = connection.execute(
            """INSERT INTO purchase_invoices
               (invoice_no, invoice_date, supplier_id, total_amount)
               VALUES (?, ?, ?, ?)""",
            ("PI-20261018-0001", "2026-10-18", supplier_id, 80),
        ).lastrowid
        connection.commit()
    finally:
        connection.close()

    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session.update(
            user_id=user_id,
            username="payment_admin",
            full_name="Payment Admin",
            role="admin",
            system="distribution",
        )
    yield {
        "client": client,
        "customer_id": customer_id,
        "supplier_id": supplier_id,
        "invoice_id": invoice_id,
        "purchase_invoice_id": purchase_invoice_id,
        "other_user_id": other_user_id,
    }
    app_module.app.config["TESTING"] = previous_testing
    database.close_postgres_pool()
    database.DATABASE = previous_database


def test_sales_payment_create_list_edit_invoice_status_and_delete(payment_client):
    client = payment_client["client"]
    response = client.post(
        "/sales-payments/add",
        data={
            "invoice_id": str(payment_client["invoice_id"]),
            "customer_id": str(payment_client["customer_id"]),
            "payment_date": "2026-10-19",
            "amount": "35.25",
            "payment_mode": "bank",
            "reference_no": "RCPT-001",
            "remarks": "First installment",
        },
    )
    assert response.status_code == 302

    invoices = client.get(
        f"/api/customer-invoices/{payment_client['customer_id']}"
    ).json["invoices"]
    assert invoices[0]["paid"] == pytest.approx(35.25)
    assert invoices[0]["due"] == pytest.approx(64.75)
    assert invoices[0]["status"] == "partial"

    listing = client.get(
        f"/sales-payments?customer_id={payment_client['customer_id']}"
        "&from_date=2026-10-19&to_date=2026-10-19"
    )
    assert listing.status_code == 200
    assert b"RCPT-001" in listing.data
    assert b"35.25" in listing.data

    connection = database.get_connection()
    try:
        payment_id = connection.execute(
            "SELECT id FROM sales_payments"
        ).fetchone()["id"]
    finally:
        connection.close()

    assert client.post(
        f"/sales-payments/edit/{payment_id}",
        data={
            "payment_date": "2026-10-20",
            "amount": "100",
            "payment_mode": "cash",
            "reference_no": "RCPT-EDITED",
        },
    ).status_code == 302
    invoices = client.get(
        f"/api/customer-invoices/{payment_client['customer_id']}"
    ).json["invoices"]
    assert invoices[0]["status"] == "paid"
    assert client.get("/outstanding?view=invoice").status_code == 200
    assert client.get("/statement/receivable/1").status_code == 200
    assert client.get("/daybook?from_date=2026-10-20&to_date=2026-10-20").status_code == 200

    assert client.get(f"/sales-payments/delete/{payment_id}").status_code == 302
    invoices = client.get(
        f"/api/customer-invoices/{payment_client['customer_id']}"
    ).json["invoices"]
    assert invoices[0]["paid"] == 0
    assert invoices[0]["status"] == "unpaid"


def test_purchase_on_account_payments_affect_supplier_reports(payment_client):
    client = payment_client["client"]
    assert client.post(
        "/purchase-payments/add",
        data={
            "invoice_id": "",
            "supplier_id": str(payment_client["supplier_id"]),
            "payment_date": "2026-10-19",
            "amount": "20",
            "payment_mode": "upi",
            "reference_no": "PAY-001",
            "remarks": "On-account payment",
        },
    ).status_code == 302
    invoices = client.get(
        f"/api/supplier-invoices/{payment_client['supplier_id']}"
    ).json["invoices"]
    assert invoices[0]["paid"] == 0
    assert invoices[0]["due"] == 80

    assert client.get("/purchase-payments?supplier_id=1").status_code == 200
    assert client.get("/outstanding?type=payable&view=party").status_code == 200
    assert client.get("/statement/payable/1").status_code == 200
    daybook = client.get(
        "/daybook?from_date=2026-10-19&to_date=2026-10-19"
    )
    assert daybook.status_code == 200
    assert b"PAY-001" in daybook.data

    connection = database.get_connection()
    try:
        payment_id = connection.execute(
            "SELECT id FROM purchase_payments"
        ).fetchone()["id"]
    finally:
        connection.close()
    assert client.post(
        f"/purchase-payments/edit/{payment_id}",
        data={
            "payment_date": "2026-10-20",
            "amount": "25",
            "payment_mode": "bank",
            "reference_no": "PAY-EDITED",
        },
    ).status_code == 302
    assert client.get(f"/purchase-payments/delete/{payment_id}").status_code == 302


def test_payment_routes_require_distribution_system(payment_client):
    client = payment_client["client"]
    with client.session_transaction() as session:
        session["system"] = "manufacturing"
    for path in ("/sales-payments", "/purchase-payments", "/daybook"):
        response = client.get(path)
        assert response.status_code == 302
        assert "system=distribution" in response.location


def test_payment_date_and_numeric_postgres_parameter_conversion(monkeypatch):
    class FakeCursor:
        def __init__(self):
            self.statement = ""
            self.parameters = ()

        def execute(self, statement, parameters=()):
            self.statement = statement
            self.parameters = parameters

    monkeypatch.setattr(document_database, "is_postgres_configured", lambda: True)
    cursor = document_database._DocumentCursor(FakeCursor())
    cursor.execute(
        """SELECT * FROM sales_payments
           WHERE payment_date BETWEEN ? AND ? AND customer_id = ?""",
        ("2026-10-01", "2026-10-19", "12"),
    )
    assert cursor._cursor.parameters == (
        date(2026, 10, 1),
        date(2026, 10, 19),
        12,
    )


def test_postgres_date_values_keep_iso_api_and_ageing_behavior():
    due_date = date(2026, 10, 18)
    assert accounting_module._iso_date(due_date) == "2026-10-18"
    assert accounting_module.ageing_bucket(due_date, "2026-11-01") == "0-30"


def test_connection_cleanup_on_accounting_query_failure(monkeypatch, payment_client):
    from flask import g

    class BrokenCursor:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("payment query failed")

    class TrackedConnection:
        closed = False

        def cursor(self):
            return BrokenCursor()

        def close(self):
            self.closed = True

    connection = TrackedConnection()
    def tracked_get_connection():
        g.setdefault("_document_database_connections", []).append(connection)
        return connection

    monkeypatch.setattr(accounting_module, "get_connection", tracked_get_connection)
    with pytest.raises(RuntimeError, match="payment query failed"):
        payment_client["client"].get("/sales-payments")
    assert connection.closed
