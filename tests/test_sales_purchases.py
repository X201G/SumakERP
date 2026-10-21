import sys
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module
import database
import modules.document_database as document_database


@pytest.fixture
def document_client(tmp_path, monkeypatch):
    previous_database = database.DATABASE
    previous_testing = app_module.app.config.get("TESTING")
    database.close_postgres_pool()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database.DATABASE = str(tmp_path / "sales_purchases_test.db")
    database.create_tables()

    connection = database.get_connection()
    try:
        connection.execute(
            "DELETE FROM users WHERE username IN (?, ?)",
            ("document_admin", "document_other"),
        )
        admin_id = connection.execute(
            """INSERT INTO users (username, password_hash, full_name, role, system)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "document_admin",
                generate_password_hash("document-password"),
                "Document Admin",
                "admin",
                "distribution",
            ),
        ).lastrowid
        other_id = connection.execute(
            """INSERT INTO users (username, password_hash, full_name, role, system)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "document_other",
                generate_password_hash("other-password"),
                "Other System",
                "admin",
                "manufacturing",
            ),
        ).lastrowid
        customer_id = connection.execute(
            "INSERT INTO customers (name) VALUES (?)", ("Test Customer",)
        ).lastrowid
        supplier_id = connection.execute(
            "INSERT INTO suppliers (name) VALUES (?)", ("Test Supplier",)
        ).lastrowid
        transporter_id = connection.execute(
            "INSERT INTO transporters (name) VALUES (?)", ("Test Transporter",)
        ).lastrowid
        unit_id = connection.execute(
            "INSERT INTO units (unit_name) VALUES (?)", ("Each",)
        ).lastrowid
        hsn_id = connection.execute(
            "INSERT INTO hsn (hsn_code) VALUES (?)", ("1234",)
        ).lastrowid
        product_id = connection.execute(
            """INSERT INTO products
               (product_name, unit_id, hsn_id, sale_price, purchase_price)
               VALUES (?, ?, ?, ?, ?)""",
            ("Test Product", unit_id, hsn_id, 12.5, 5.0),
        ).lastrowid
        connection.commit()
    finally:
        connection.close()

    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session.update(
            user_id=admin_id,
            username="document_admin",
            full_name="Document Admin",
            role="admin",
            system="distribution",
        )

    yield {
        "client": client,
        "customer_id": customer_id,
        "supplier_id": supplier_id,
        "transporter_id": transporter_id,
        "product_id": product_id,
        "unit_id": unit_id,
        "hsn_id": hsn_id,
        "other_id": other_id,
    }

    app_module.app.config["TESTING"] = previous_testing
    database.close_postgres_pool()
    database.DATABASE = previous_database


def product_lines(product_id, unit_id, hsn_id, quantity="2", rate="10"):
    return {
        "product_id[]": [str(product_id)],
        "unit_id[]": [str(unit_id)],
        "hsn_id[]": [str(hsn_id)],
        "quantity[]": [quantity],
        "rate[]": [rate],
        "discount[]": ["10"],
        "tax_rate[]": ["5"],
    }


def document_counts():
    connection = database.get_connection()
    try:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "sales_invoices",
                "sales_invoice_items",
                "sales_challans",
                "sales_challan_items",
                "sales_returns",
                "sales_return_items",
                "purchase_invoices",
                "purchase_invoice_items",
                "purchase_grn",
                "purchase_grn_items",
                "purchase_returns",
                "purchase_return_items",
                "stock_movements",
            )
        }
    finally:
        connection.close()


def test_sales_invoice_challan_and_return_post_stock_atomically(document_client):
    client = document_client["client"]
    common = {
        "customer_id": str(document_client["customer_id"]),
        "transporter_id": str(document_client["transporter_id"]),
        "po_no": "CUSTOMER-PO-1",
        "po_date": "2026-10-10",
    }
    response = client.post(
        "/sales-challans/add",
        data={
            **common,
            "challan_date": "2026-10-10",
            **product_lines(
                document_client["product_id"],
                document_client["unit_id"],
                document_client["hsn_id"],
            ),
        },
    )
    assert response.status_code == 302

    connection = database.get_connection()
    try:
        challan = connection.execute(
            "SELECT id, challan_no, total_amount FROM sales_challans"
        ).fetchone()
        challan_item = connection.execute(
            "SELECT id, amount FROM sales_challan_items"
        ).fetchone()
        movement = connection.execute(
            """SELECT movement_type, quantity, amount, source_id
               FROM stock_movements WHERE source_type = 'challan'"""
        ).fetchone()
    finally:
        connection.close()
    assert challan["id"] > 0
    assert challan_item["amount"] == pytest.approx(18.9)
    assert challan["total_amount"] == pytest.approx(18.9)
    assert tuple(movement) == ("OUT", 2.0, 20.0, challan["id"])

    invoice_response = client.post(
        "/sales-invoices/add",
        data={
            **common,
            "invoice_date": "2026-10-11",
            "cr_days": "0",
            "due_date": "2026-10-11",
            **product_lines(
                document_client["product_id"],
                document_client["unit_id"],
                document_client["hsn_id"],
            ),
            "source_type[]": ["challan"],
            "source_item_id[]": [str(challan_item["id"])],
        },
    )
    assert invoice_response.status_code == 302
    connection = database.get_connection()
    try:
        invoice = connection.execute(
            "SELECT id, invoice_no, total_amount FROM sales_invoices"
        ).fetchone()
        billed_qty = connection.execute(
            "SELECT billed_qty FROM sales_challan_items WHERE id = ?",
            (challan_item["id"],),
        ).fetchone()[0]
        invoice_movement_count = connection.execute(
            "SELECT COUNT(*) FROM stock_movements WHERE source_type = 'sales_invoice'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert invoice["id"] > 0
    assert invoice["total_amount"] == pytest.approx(18.9)
    assert billed_qty == pytest.approx(2)
    assert invoice_movement_count == 0

    return_response = client.post(
        "/sales-returns/add",
        data={
            **common,
            "invoice_id": str(invoice["id"]),
            "return_date": "2026-10-12",
            "reason": "Damaged",
            **product_lines(
                document_client["product_id"],
                document_client["unit_id"],
                document_client["hsn_id"],
            ),
        },
    )
    assert return_response.status_code == 302
    connection = database.get_connection()
    try:
        movements = connection.execute(
            """SELECT movement_type, source_type
               FROM stock_movements ORDER BY id"""
        ).fetchall()
    finally:
        connection.close()
    assert [tuple(row) for row in movements] == [
        ("OUT", "challan"),
        ("IN", "sales_return"),
    ]


def test_purchase_invoice_grn_and_return_post_correct_movement_directions(
    document_client,
):
    client = document_client["client"]
    common = {
        "supplier_id": str(document_client["supplier_id"]),
        "transporter_id": str(document_client["transporter_id"]),
    }
    invoice = client.post(
        "/purchase-invoices/add",
        data={
            **common,
            "invoice_date": "2026-10-13",
            "sup_invoice_no": "SUP-100",
            "sup_invoice_date": "2026-10-12",
            "cr_days": "0",
            "due_date": "2026-10-13",
            **product_lines(
                document_client["product_id"],
                document_client["unit_id"],
                document_client["hsn_id"],
                quantity="4",
                rate="7.5",
            ),
        },
    )
    assert invoice.status_code == 302

    grn = client.post(
        "/purchase-grn/add",
        data={
            **common,
            "grn_date": "2026-10-14",
            **product_lines(
                document_client["product_id"],
                document_client["unit_id"],
                document_client["hsn_id"],
                quantity="4",
                rate="7.5",
            ),
        },
    )
    assert grn.status_code == 302
    connection = database.get_connection()
    try:
        invoice_row = connection.execute(
            "SELECT id, total_amount FROM purchase_invoices"
        ).fetchone()
        grn_row = connection.execute(
            "SELECT id, grn_no, total_amount FROM purchase_grn"
        ).fetchone()
        grn_item = connection.execute(
            "SELECT id, amount FROM purchase_grn_items"
        ).fetchone()
        movement = connection.execute(
            """SELECT movement_type, quantity, amount, source_id
               FROM stock_movements WHERE source_type = 'grn'"""
        ).fetchone()
    finally:
        connection.close()
    assert invoice_row["id"] > 0
    assert invoice_row["total_amount"] == pytest.approx(28.35)
    assert grn_row["id"] > 0
    assert grn_row["total_amount"] == pytest.approx(28.35)
    assert grn_item["amount"] == pytest.approx(28.35)
    assert tuple(movement) == ("IN", 4.0, 30.0, grn_row["id"])

    purchase_return = client.post(
        "/purchase-returns/add",
        data={
            **common,
            "invoice_id": str(invoice_row["id"]),
            "return_date": "2026-10-15",
            "reason": "Excess",
            **product_lines(
                document_client["product_id"],
                document_client["unit_id"],
                document_client["hsn_id"],
                quantity="1",
                rate="7.5",
            ),
        },
    )
    assert purchase_return.status_code == 302
    connection = database.get_connection()
    try:
        movements = connection.execute(
            "SELECT movement_type, source_type FROM stock_movements ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    assert [tuple(row) for row in movements] == [
        ("IN", "grn"),
        ("OUT", "purchase_return"),
    ]


def test_repeated_document_posts_create_one_movement_per_document_line(document_client):
    client = document_client["client"]
    data = {
        "supplier_id": str(document_client["supplier_id"]),
        "grn_date": "2026-10-16",
        **product_lines(
            document_client["product_id"],
            document_client["unit_id"],
            document_client["hsn_id"],
            quantity="3",
            rate="2",
        ),
    }
    assert client.post("/purchase-grn/add", data=data).status_code == 302
    assert client.post("/purchase-grn/add", data=data).status_code == 302
    counts = document_counts()
    assert counts["purchase_grn"] == 2
    assert counts["purchase_grn_items"] == 2
    assert counts["stock_movements"] == 2


def test_invalid_line_rolls_back_header_and_releases_connection(document_client):
    client = document_client["client"]
    with pytest.raises(ValueError):
        client.post(
            "/sales-invoices/add",
            data={
                "customer_id": str(document_client["customer_id"]),
                "invoice_date": "2026-10-17",
                "product_id[]": [str(document_client["product_id"])],
                "unit_id[]": [str(document_client["unit_id"])],
                "hsn_id[]": [str(document_client["hsn_id"])],
                "quantity[]": ["invalid"],
                "rate[]": ["5"],
                "discount[]": ["0"],
                "tax_rate[]": ["18"],
            },
        )
    counts = document_counts()
    assert counts["sales_invoices"] == 0
    assert counts["sales_invoice_items"] == 0
    assert counts["stock_movements"] == 0
    connection = database.get_connection()
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM document_number_counters"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    finally:
        connection.close()

    response = client.post(
        "/sales-invoices/add",
        data={
            "customer_id": str(document_client["customer_id"]),
            "invoice_date": "2026-10-17",
        },
    )
    assert response.status_code == 302
    connection = database.get_connection()
    try:
        number = connection.execute(
            "SELECT invoice_no FROM sales_invoices"
        ).fetchone()["invoice_no"]
        assert number == "SI-20261017-0001"
    finally:
        connection.close()


def test_sales_invoice_number_is_not_reused_after_deletion(document_client):
    client = document_client["client"]
    data = {
        "customer_id": str(document_client["customer_id"]),
        "invoice_date": "2026-10-20",
    }
    assert client.post("/sales-invoices/add", data=data).status_code == 302

    connection = database.get_connection()
    try:
        first = connection.execute(
            "SELECT id, invoice_no FROM sales_invoices"
        ).fetchone()
        assert first["invoice_no"] == "SI-20261020-0001"
        connection.execute("DELETE FROM sales_invoices WHERE id = ?", (first["id"],))
        connection.commit()
    finally:
        connection.close()

    assert client.post("/sales-invoices/add", data=data).status_code == 302
    connection = database.get_connection()
    try:
        number = connection.execute(
            "SELECT invoice_no FROM sales_invoices"
        ).fetchone()["invoice_no"]
        assert number == "SI-20261020-0002"
    finally:
        connection.close()


def test_document_number_allocation_is_serialized_for_concurrent_requests(
    document_client,
):
    def allocate(_):
        connection = database.get_connection()
        try:
            number = document_database.next_document_number(
                connection.cursor(),
                "sales_invoice",
                "global",
                "SI-20261020-",
                "SI-",
            )
            connection.commit()
            return number
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=5) as executor:
        numbers = list(executor.map(allocate, range(10)))

    assert sorted(numbers) == [
        f"SI-20261020-{index:04d}" for index in range(1, 11)
    ]


def test_document_number_series_and_business_scopes_are_separate(document_client):
    def allocate(document_type, scope, prefix, existing_prefix):
        connection = database.get_connection()
        try:
            number = document_database.next_document_number(
                connection.cursor(),
                document_type,
                scope,
                prefix,
                existing_prefix,
            )
            connection.commit()
            return number
        finally:
            connection.close()

    sales_number = allocate("sales_invoice", "global", "SI-20261020-", "SI-")
    purchase_number = allocate(
        "purchase_invoice", "global", "PI-20261020-", "PI-"
    )
    first_order = allocate(
        "purchase_order", "20261020", "PO-20261020-", "PO-20261020-"
    )
    next_order = allocate(
        "purchase_order", "20261020", "PO-20261020-", "PO-20261020-"
    )
    next_day_order = allocate(
        "purchase_order", "20261021", "PO-20261021-", "PO-20261021-"
    )

    assert sales_number == "SI-20261020-0001"
    assert purchase_number == "PI-20261020-0001"
    assert first_order == "PO-20261020-0001"
    assert next_order == "PO-20261020-0002"
    assert next_day_order == "PO-20261021-0001"


def test_fresh_sqlite_schema_includes_distribution_workflow_columns(document_client):
    connection = database.get_connection()
    try:
        schema_columns = {}
        for table in (
            "stock_movements",
            "sales_invoice_items",
            "sales_order_items",
            "sales_challan_items",
        ):
            schema_columns[table] = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
        assert {"rate", "amount"}.issubset(schema_columns["stock_movements"])
        assert {"source_type", "source_item_id"}.issubset(
            schema_columns["sales_invoice_items"]
        )
        assert "billed_qty" in schema_columns["sales_order_items"]
        assert "billed_qty" in schema_columns["sales_challan_items"]
    finally:
        connection.close()


def test_create_tables_upgrades_legacy_distribution_columns_without_losing_rows(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "legacy_distribution.db"
    legacy = sqlite3.connect(database_path)
    legacy.executescript(
        """
        CREATE TABLE stock_movements (
            id INTEGER PRIMARY KEY,
            product_id INTEGER,
            movement_type TEXT,
            quantity REAL,
            source_type TEXT,
            source_id INTEGER,
            source_no TEXT,
            movement_date DATE,
            created_at DATETIME
        );
        INSERT INTO stock_movements
            (id, product_id, movement_type, quantity, source_no)
        VALUES (91, 7, 'IN', 3, 'legacy-opening');
        CREATE TABLE sales_invoice_items (
            id INTEGER PRIMARY KEY,
            invoice_id INTEGER,
            product_id INTEGER,
            quantity REAL
        );
        INSERT INTO sales_invoice_items (id, invoice_id, product_id, quantity)
        VALUES (92, 8, 7, 3);
        CREATE TABLE sales_order_items (
            id INTEGER PRIMARY KEY,
            order_id INTEGER,
            product_id INTEGER,
            quantity REAL
        );
        CREATE TABLE sales_challan_items (
            id INTEGER PRIMARY KEY,
            challan_id INTEGER,
            product_id INTEGER,
            quantity REAL
        );
        """
    )
    legacy.commit()
    legacy.close()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    previous_database = database.DATABASE
    database.DATABASE = str(database_path)
    try:
        database.create_tables()
        connection = database.get_connection()
        try:
            movement = connection.execute(
                """SELECT product_id, movement_type, quantity, source_no, rate, amount
                   FROM stock_movements WHERE id = 91"""
            ).fetchone()
            assert tuple(movement) == (7, "IN", 3.0, "legacy-opening", 0.0, 0.0)
            invoice_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(sales_invoice_items)"
                )
            }
            assert {"source_type", "source_item_id"}.issubset(invoice_columns)
            order_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(sales_order_items)"
                )
            }
            challan_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(sales_challan_items)"
                )
            }
            assert "billed_qty" in order_columns
            assert "billed_qty" in challan_columns
            invoice = connection.execute(
                "SELECT invoice_id, product_id, quantity FROM sales_invoice_items WHERE id = 92"
            ).fetchone()
            assert tuple(invoice) == (8, 7, 3.0)
        finally:
            connection.close()
    finally:
        database.DATABASE = previous_database


def test_document_blueprints_remain_distribution_scoped(document_client):
    client = document_client["client"]
    with client.session_transaction() as session:
        session["system"] = "manufacturing"
    for path in (
        "/sales-invoices",
        "/sales-challans",
        "/sales-returns",
        "/purchase-grn",
        "/purchase-returns",
    ):
        response = client.get(path)
        assert response.status_code == 302
        assert b"system=distribution" in response.location.encode()


def test_postgres_cursor_binds_dates_numbers_generated_ids_and_case_insensitive_like(
    monkeypatch,
):
    class FakePostgresCursor:
        def __init__(self):
            self.statement = None
            self.parameters = None

        def execute(self, statement, parameters=()):
            self.statement = statement
            self.parameters = parameters

        def fetchone(self):
            return (57,)

    monkeypatch.setattr(document_database, "is_postgres_configured", lambda: True)
    native_cursor = FakePostgresCursor()
    cursor = document_database._DocumentCursor(native_cursor)
    cursor.execute(
        """INSERT INTO sales_invoices
           (invoice_no, invoice_date, customer_id, transporter_id, cr_days, due_date)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("SI-20261019-0001", "2026-10-19", "12", "", "30", ""),
    )
    assert native_cursor.statement.endswith("RETURNING id")
    assert native_cursor.parameters == (
        "SI-20261019-0001",
        date(2026, 10, 19),
        12,
        None,
        30,
        None,
    )
    assert cursor.lastrowid == 57

    cursor.execute(
        "SELECT id FROM sales_invoices WHERE invoice_no LIKE ? LIMIT ? OFFSET ?",
        ("%si-%", 20, 0),
    )
    assert "invoice_no ILIKE %s" in native_cursor.statement
    assert native_cursor.parameters == ("%si-%", 20, 0)

    cursor.execute(
        """INSERT INTO stock_movements
           (product_id, movement_type, quantity, source_type, source_id,
            source_no, movement_date, rate, amount)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (12, "OUT", 2.5, "sales_invoice", 57, "SI-1", "2026-10-19", 4, 10),
    )
    assert native_cursor.parameters == (
        12,
        "OUT",
        2.5,
        "sales_invoice",
        57,
        "SI-1",
        date(2026, 10, 19),
        4,
        10,
    )
