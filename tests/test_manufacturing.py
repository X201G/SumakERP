import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module
import database
import modules.document_database as document_database


@pytest.fixture
def manufacturing_client(tmp_path, monkeypatch):
    previous_database = database.DATABASE
    previous_testing = app_module.app.config.get("TESTING")
    database.close_postgres_pool()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database.DATABASE = str(tmp_path / "manufacturing_test.db")
    database.create_tables()

    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session.update(
            user_id=2,
            username="manufacturing_admin",
            full_name="Manufacturing Admin",
            role="admin",
            system="manufacturing",
        )
    yield client

    app_module.app.config["TESTING"] = previous_testing
    database.close_postgres_pool()
    database.DATABASE = previous_database


def scalar(sql, parameters=()):
    connection = database.get_connection()
    try:
        return connection.execute(sql, parameters).fetchone()[0]
    finally:
        connection.close()


def create_parties_and_tool():
    connection = database.get_connection()
    try:
        customer_id = connection.execute(
            "INSERT INTO customers (name) VALUES (?)", ("Manufacturing Customer",)
        ).lastrowid
        supplier_id = connection.execute(
            "INSERT INTO suppliers (name) VALUES (?)", ("Manufacturing Supplier",)
        ).lastrowid
        transporter_id = connection.execute(
            "INSERT INTO transporters (name) VALUES (?)",
            ("Manufacturing Transporter",),
        ).lastrowid
        tool_id = connection.execute(
            """INSERT INTO mfg_tools
               (item_code, tool_name, tool_type, overall_length_mm, customer_id)
               VALUES (?, ?, ?, ?, ?)""",
            ("TEST0001", "Manufacturing Test Tool", "Drill", 10, customer_id),
        ).lastrowid
        connection.commit()
        return customer_id, supplier_id, transporter_id, tool_id
    finally:
        connection.close()


def test_migration_defines_all_existing_manufacturing_tables_and_permissions(
    manufacturing_client,
):
    migration = (
        ROOT / "migrations" / "postgres" / "0007_manufacturing.sql"
    ).read_text()
    migrated_tables = set(
        re.findall(r"CREATE TABLE\s+(mfg_[a-z0-9_]+|permissions)\s*\(", migration)
    )
    connection = database.get_connection()
    try:
        sqlite_tables = {
            row["name"]
            for row in connection.execute(
                """SELECT name FROM sqlite_master WHERE type = 'table'
                   AND (name LIKE 'mfg\\_%' ESCAPE '\\' OR name = 'permissions')"""
            )
        }
    finally:
        connection.close()

    assert len(migrated_tables - {"permissions"}) == 31
    assert migrated_tables == sqlite_tables
    assert "CREATE UNIQUE INDEX idx_mfg_brands_type_name" in migration
    assert "CREATE UNIQUE INDEX idx_mfg_tools_item_code" in migration
    assert "UNIQUE (user_id, section)" in migration
    assert "REFERENCES customers(id)" in migration
    assert "REFERENCES suppliers(id)" in migration
    assert "REFERENCES transporters(id)" in migration
    assert "REFERENCES units(id)" in migration


def test_masters_tools_and_stocked_rods_keep_existing_numbering_and_stock(
    manufacturing_client,
):
    client = manufacturing_client
    customer_id, _, _, _ = create_parties_and_tool()

    assert client.post(
        "/manufacturing/brands/add",
        data={"tool_type": "Drill", "name": "Typhon"},
    ).status_code == 302
    assert client.post(
        "/manufacturing/brands/add",
        data={"tool_type": "Drill", "name": "Typhon"},
    ).status_code == 302
    assert scalar("SELECT COUNT(*) FROM mfg_brands WHERE name = ?", ("Typhon",)) == 1
    brand_id = scalar(
        "SELECT id FROM mfg_brands WHERE tool_type = ? AND name = ?",
        ("Drill", "Typhon"),
    )
    tool_form = {
        "tool_name": "Typhon Drill 10mm",
        "tool_type": "Drill",
        "diameter_mm": "10.5",
        "overall_length_mm": "20",
        "flute_length_mm": "8",
        "shank_diameter_mm": "6",
        "customer_id": str(customer_id),
        "brand_id": str(brand_id),
        "purchase_price": "20.50",
        "purchase_discount_percent": "5",
        "sales_price": "35.25",
        "sales_discount_percent": "2.5",
    }
    assert client.post("/manufacturing/tools/add", data=tool_form).status_code == 302
    assert client.post(
        "/manufacturing/tools/add",
        data={**tool_form, "tool_name": "Typhon Drill 12mm"},
    ).status_code == 302
    connection = database.get_connection()
    try:
        tools = connection.execute(
            """SELECT item_code, diameter_mm, overall_length_mm,
                      purchase_price, sales_price
               FROM mfg_tools WHERE brand_id = ? ORDER BY id""",
            (brand_id,),
        ).fetchall()
    finally:
        connection.close()
    assert [row["item_code"] for row in tools] == ["DRTY0001", "DRTY0002"]
    assert tools[0]["diameter_mm"] == 10.5
    assert tools[0]["overall_length_mm"] == 20
    assert tools[0]["purchase_price"] == 20.5
    assert tools[0]["sales_price"] == 35.25
    assert client.get("/manufacturing/tools?q=Typhon").status_code == 200
    assert client.get(f"/manufacturing/brands/delete/{brand_id}").status_code == 302
    assert scalar(
        "SELECT COUNT(*) FROM mfg_brands WHERE id = ?", (brand_id,)
    ) == 1

    assert client.post(
        "/manufacturing/item-categories/add", data={"name": "Consumables"}
    ).status_code == 302
    category_id = scalar(
        "SELECT id FROM mfg_item_categories WHERE name = ?", ("Consumables",)
    )
    assert client.post(
        "/manufacturing/items/add",
        data={
            "item_name": "Grinding Oil",
            "category_id": str(category_id),
            "hsn_code": "OIL-01",
            "tax_rate": "18.5",
            "purchase_price": "7.25",
            "purchase_discount_percent": "1.5",
            "sales_price": "10.75",
        },
    ).status_code == 302
    assert client.get("/manufacturing/items?q=Grinding").status_code == 200
    assert scalar(
        "SELECT tax_rate FROM mfg_items WHERE item_name = ?", ("Grinding Oil",)
    ) == 18.5

    assert client.post(
        "/manufacturing/rods/add",
        data={
            "rod_date": "2026-10-19",
            "grade": "K20",
            "diameter_mm": "10.5",
            "length_mm": "330",
            "weight_kg": "0.25",
            "quantity": "1",
            "purchase_price": "100",
            "purchase_discount_percent": "10",
        },
    ).status_code == 302
    connection = database.get_connection()
    try:
        rod = connection.execute(
            "SELECT id, rate, remaining_length_mm FROM mfg_rods WHERE grade = ?",
            ("K20",),
        ).fetchone()
        movement = connection.execute(
            """SELECT item_type, item_id, movement_type, quantity, source_type
               FROM mfg_stock_movements WHERE item_type = 'rod' AND item_id = ?""",
            (rod["id"],),
        ).fetchone()
    finally:
        connection.close()
    assert rod["rate"] == 90
    assert rod["remaining_length_mm"] == 330
    assert tuple(movement) == ("rod", rod["id"], "in", 330, "purchase")
    assert client.get("/manufacturing/rods?q=K20").status_code == 200
    assert client.post(
        f"/manufacturing/rods/edit/{rod['id']}",
        data={"grade": "K20-A", "diameter_mm": "10.75", "purchase_price": "110"},
    ).status_code == 302
    assert scalar(
        "SELECT diameter_mm FROM mfg_rods WHERE id = ?", (rod["id"],)
    ) == 10.75
    assert client.get(f"/manufacturing/rods/delete/{rod['id']}").status_code == 302


def test_job_batch_reservation_stages_and_deletion_restriction(manufacturing_client):
    client = manufacturing_client
    customer_id, _, _, tool_id = create_parties_and_tool()
    connection = database.get_connection()
    try:
        rod_id = connection.execute(
            """INSERT INTO mfg_rods
               (rod_date, grade, diameter_mm, length_mm, remaining_length_mm)
               VALUES (?, ?, ?, ?, ?)""",
            ("2026-10-19", "K20", 10, 330, 330),
        ).lastrowid
        connection.commit()
    finally:
        connection.close()

    assert client.post(
        "/manufacturing/batches/add",
        data={
            "batch_date": "2026-10-19",
            "tool_id": str(tool_id),
            "rod_id": str(rod_id),
            "grade": "K20",
            "customer_id": str(customer_id),
            "quantity": "2",
        },
    ).status_code == 302
    connection = database.get_connection()
    try:
        batch = connection.execute(
            "SELECT id, batch_no, length_needed_mm FROM mfg_job_batches"
        ).fetchone()
        rod = connection.execute(
            "SELECT remaining_length_mm FROM mfg_rods WHERE id = ?", (rod_id,)
        ).fetchone()
        reserve_movement = connection.execute(
            """SELECT quantity, source_type, source_id FROM mfg_stock_movements
               WHERE source_type = 'batch_reserve'"""
        ).fetchone()
    finally:
        connection.close()
    assert batch["batch_no"] == "JW-20261019-0001"
    assert batch["length_needed_mm"] == 28
    assert rod["remaining_length_mm"] == 302
    assert tuple(reserve_movement) == (28, "batch_reserve", batch["id"])

    stages = ["blank_making", "tool_manufacturing", "coating"]
    assert client.post(
        f"/manufacturing/batches/{batch['id']}/send",
        data={
            "stages": stages,
            "dispatch_date": "2026-10-19",
            "quantity_sent": "2",
        },
    ).status_code == 302
    stage_id = scalar(
        "SELECT id FROM mfg_job_stages WHERE batch_id = ?", (batch["id"],)
    )
    assert client.post(
        f"/manufacturing/batches/{batch['id']}/receive/{stage_id}",
        data={"receive_date": "2026-10-19", "quantity_received": "2"},
    ).status_code == 302
    assert scalar(
        """SELECT COUNT(*) FROM mfg_stock_movements
           WHERE item_type = 'tool' AND item_id = ?
             AND source_type = 'batch_complete'""",
        (tool_id,),
    ) == 1

    restricted = client.get(
        f"/manufacturing/batches/delete/{batch['id']}", follow_redirects=True
    )
    assert restricted.status_code == 200
    assert scalar(
        "SELECT COUNT(*) FROM mfg_job_batches WHERE id = ?", (batch["id"],)
    ) == 1


def test_manufacturing_payments_update_status_and_resharpen_keeps_separate_tables(
    manufacturing_client, monkeypatch
):
    client = manufacturing_client
    customer_id, supplier_id, _, tool_id = create_parties_and_tool()
    connection = database.get_connection()
    try:
        invoice_id = connection.execute(
            """INSERT INTO mfg_purchase_invoices
               (invoice_no, invoice_date, supplier_id, total_amount)
               VALUES (?, ?, ?, ?)""",
            ("MPI-20261019-0001", "2026-10-19", supplier_id, 100),
        ).lastrowid
        sales_invoice_id = connection.execute(
            """INSERT INTO mfg_invoices
               (invoice_no, invoice_date, customer_id, total_amount)
               VALUES (?, ?, ?, ?)""",
            ("MI-20261019-0001", "2026-10-19", customer_id, 80),
        ).lastrowid
        connection.commit()
    finally:
        connection.close()

    statements = []
    original_execute = document_database._DocumentCursor.execute

    def recording_execute(cursor, statement, parameters=None):
        statements.append(statement)
        return original_execute(cursor, statement, parameters)

    monkeypatch.setattr(
        document_database._DocumentCursor, "execute", recording_execute
    )
    assert client.post(
        "/manufacturing/purchase-payments/add",
        data={
            "invoice_id": str(invoice_id),
            "supplier_id": str(supplier_id),
            "payment_date": "2026-10-19",
            "amount": "100.50",
        },
    ).status_code == 302
    assert statements[0].strip().upper() == "BEGIN IMMEDIATE"
    assert scalar(
        "SELECT status FROM mfg_purchase_invoices WHERE id = ?", (invoice_id,)
    ) == "paid"
    assert client.get("/manufacturing/purchase-payments").status_code == 200
    payment_id = scalar(
        "SELECT id FROM mfg_purchase_payments WHERE invoice_id = ?", (invoice_id,)
    )

    statements.clear()
    assert client.post(
        f"/manufacturing/purchase-payments/edit/{payment_id}",
        data={
            "payment_date": "2026-10-20",
            "amount": "25.00",
            "payment_mode": "bank",
        },
    ).status_code == 302
    assert statements[0].strip().upper() == "BEGIN IMMEDIATE"
    assert scalar(
        "SELECT status FROM mfg_purchase_invoices WHERE id = ?", (invoice_id,)
    ) == "partial"

    statements.clear()
    assert client.get(
        f"/manufacturing/purchase-payments/delete/{payment_id}"
    ).status_code == 302
    assert statements[0].strip().upper() == "BEGIN IMMEDIATE"
    assert scalar(
        "SELECT status FROM mfg_purchase_invoices WHERE id = ?", (invoice_id,)
    ) == "unpaid"

    assert client.post(
        "/manufacturing/sales-payments/add",
        data={
            "invoice_id": str(sales_invoice_id),
            "customer_id": str(customer_id),
            "payment_date": "2026-10-19",
            "amount": "25.75",
            "source": "new",
        },
    ).status_code == 302
    assert scalar(
        "SELECT amount FROM mfg_sales_payments WHERE invoice_id = ?",
        (sales_invoice_id,),
    ) == 25.75
    assert client.post(
        "/manufacturing/sales-payments/add",
        data={
            "customer_id": str(customer_id),
            "payment_date": "2026-10-19",
            "amount": "15.25",
            "source": "resharpen",
        },
    ).status_code == 302
    assert scalar(
        "SELECT amount FROM mfg_resharpen_sales_payments WHERE customer_id = ?",
        (customer_id,),
    ) == 15.25
    assert scalar("SELECT COUNT(*) FROM mfg_sales_payments") == 1
    assert client.get("/manufacturing/sales-payments").status_code == 200
    assert client.post(
        "/manufacturing/resharpen/batches/add",
        data={
            "batch_date": "2026-10-19",
            "tool_id": str(tool_id),
            "customer_id": str(customer_id),
            "quantity": "1",
        },
    ).status_code == 302
    batch = scalar("SELECT batch_no FROM mfg_resharpen_batches")
    challan = scalar("SELECT challan_no FROM mfg_resharpen_challans")
    assert batch == "RS-20261019-0001"
    assert challan == "RSIC-20261019-0001"
    assert client.post(
        "/manufacturing/resharpen/batches/add",
        data={
            "batch_date": "2026-10-19",
            "tool_id": str(tool_id),
            "customer_id": str(customer_id),
            "quantity": "1",
        },
    ).status_code == 302
    assert scalar(
        "SELECT batch_no FROM mfg_resharpen_batches ORDER BY id DESC"
    ) == "RS-20261019-0002"
    assert scalar(
        "SELECT challan_no FROM mfg_resharpen_challans ORDER BY id DESC"
    ) == "RSIC-20261019-0002"
    assert scalar(
        "SELECT COUNT(*) FROM mfg_resharpen_challan_items"
    ) == 2
    assert scalar("SELECT COUNT(*) FROM mfg_resharpen_batches") == 2
    assert client.get("/manufacturing/resharpen/batches").status_code == 200


def test_invoice_availability_checks_are_serialized_before_reading(
    manufacturing_client, monkeypatch
):
    client = manufacturing_client
    customer_id, _, _, tool_id = create_parties_and_tool()
    connection = database.get_connection()
    try:
        batch_id = connection.execute(
            """INSERT INTO mfg_job_batches
               (batch_no, batch_date, tool_id, customer_id, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            ("JB-20261019-0001", "2026-10-19", tool_id, customer_id, 1),
        ).lastrowid
        challan_id = connection.execute(
            """INSERT INTO mfg_challans
               (challan_no, challan_date, challan_type, party_type, party_id)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "DC-20261019-0001",
                "2026-10-19",
                "customer_delivery",
                "customer",
                customer_id,
            ),
        ).lastrowid
        connection.execute(
            """INSERT INTO mfg_challan_items (challan_id, batch_id, quantity)
               VALUES (?, ?, ?)""",
            (challan_id, batch_id, 1),
        )
        resharpen_batch_id = connection.execute(
            """INSERT INTO mfg_resharpen_batches
               (batch_no, batch_date, tool_id, customer_id, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            ("RS-20261019-0001", "2026-10-19", tool_id, customer_id, 1),
        ).lastrowid
        resharpen_challan_id = connection.execute(
            """INSERT INTO mfg_resharpen_challans
               (challan_no, challan_date, challan_type, party_type, party_id)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "RSDC-20261019-0001",
                "2026-10-19",
                "customer_delivery",
                "customer",
                customer_id,
            ),
        ).lastrowid
        connection.execute(
            """INSERT INTO mfg_resharpen_challan_items
               (challan_id, batch_id, quantity) VALUES (?, ?, ?)""",
            (resharpen_challan_id, resharpen_batch_id, 1),
        )
        connection.commit()
    finally:
        connection.close()

    statements = []
    original_execute = document_database._DocumentCursor.execute

    def recording_execute(cursor, statement, parameters=None):
        statements.append(statement)
        return original_execute(cursor, statement, parameters)

    monkeypatch.setattr(
        document_database._DocumentCursor, "execute", recording_execute
    )
    invoice_data = {
        "invoice_date": "2026-10-20",
        "quantity": "1",
        "rate": "10",
        "tax_rate": "0",
    }
    assert client.post(
        f"/manufacturing/batches/{batch_id}/invoice/new", data=invoice_data
    ).status_code == 302
    assert client.post(
        f"/manufacturing/resharpen/batches/{resharpen_batch_id}/invoice/new",
        data=invoice_data,
    ).status_code == 302

    for table, availability_query in (
        ("mfg_invoices", "mfg_challan_items.quantity"),
        ("mfg_resharpen_invoices", "mfg_resharpen_challan_items.quantity"),
    ):
        read_index = next(
            index
            for index, statement in enumerate(statements)
            if availability_query in statement
        )
        transaction_lock = max(
            index
            for index, statement in enumerate(statements)
            if statement.strip().upper() == "BEGIN IMMEDIATE" and index < read_index
        )
        assert transaction_lock < read_index
        assert scalar(f"SELECT COUNT(*) FROM {table}") == 1


def test_job_batch_validation_does_not_overreserve_rod(manufacturing_client):
    client = manufacturing_client
    customer_id, _, _, tool_id = create_parties_and_tool()
    connection = database.get_connection()
    try:
        rod_id = connection.execute(
            """INSERT INTO mfg_rods
               (rod_date, grade, diameter_mm, length_mm, remaining_length_mm)
               VALUES (?, ?, ?, ?, ?)""",
            ("2026-10-19", "K20", 10, 30, 30),
        ).lastrowid
        connection.commit()
    finally:
        connection.close()

    payload = {
        "batch_date": "2026-10-19",
        "tool_id": str(tool_id),
        "rod_id": str(rod_id),
        "grade": "K20",
        "customer_id": str(customer_id),
        "quantity": "2",
        "override_reason": "Regression test",
    }
    assert client.post("/manufacturing/batches/add", data=payload).status_code == 302
    assert client.post("/manufacturing/batches/add", data=payload).status_code == 302
    assert scalar("SELECT COUNT(*) FROM mfg_job_batches") == 1
    assert scalar(
        "SELECT remaining_length_mm FROM mfg_rods WHERE id = ?", (rod_id,)
    ) == 2
    assert scalar(
        """SELECT COUNT(*) FROM mfg_stock_movements
           WHERE item_type = 'rod' AND item_id = ?
             AND source_type = 'batch_reserve'""",
        (rod_id,),
    ) == 1


def test_rolled_back_manufacturing_write_is_not_visible_after_connection_reuse(
    manufacturing_client,
):
    connection = database.get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO mfg_item_categories (name) VALUES (?)",
            ("Rolled Back Category",),
        )
        connection.rollback()
        assert connection.execute(
            "SELECT COUNT(*) FROM mfg_item_categories"
        ).fetchone()[0] == 0
    finally:
        connection.close()

    assert scalar(
        "SELECT COUNT(*) FROM mfg_item_categories WHERE name = ?",
        ("Rolled Back Category",),
    ) == 0


def test_manufacturing_foreign_keys_reject_missing_shared_references(
    manufacturing_client,
):
    connection = database.get_connection()
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO mfg_tools
                   (tool_name, tool_type, customer_id)
                   VALUES (?, ?, ?)""",
                ("Orphan Tool", "Drill", 987654),
            )
        connection.rollback()
    finally:
        connection.close()


def test_permission_lookup_and_distribution_system_restrictions(
    manufacturing_client,
):
    client = manufacturing_client
    connection = database.get_connection()
    try:
        user_id = connection.execute(
            """INSERT INTO users (username, password_hash, full_name, role, system)
               VALUES (?, ?, ?, ?, ?)""",
            ("manufacturing_viewer", "unused", "Viewer", "staff", "manufacturing"),
        ).lastrowid
        connection.execute(
            """INSERT INTO permissions (user_id, section, can_view)
               VALUES (?, ?, ?)""",
            (user_id, "mfg_rods", 1),
        )
        connection.commit()
    finally:
        connection.close()

    with client.session_transaction() as session:
        session.update(
            user_id=user_id,
            username="manufacturing_viewer",
            role="staff",
            system="manufacturing",
        )
    assert client.get("/manufacturing/rods").status_code == 200
    denied = client.post(
        "/manufacturing/rods/add",
        data={
            "rod_date": "2026-10-19",
            "diameter_mm": "10",
            "quantity": "1",
        },
    )
    assert denied.status_code == 302
    assert "/manufacturing/no-access" in denied.location
    assert scalar("SELECT COUNT(*) FROM mfg_rods") == 0

    with client.session_transaction() as session:
        session["system"] = "distribution"
    system_denied = client.get("/manufacturing/rods")
    assert system_denied.status_code == 302
    assert "/login" in system_denied.location


def test_manufacturing_user_duplicate_is_reported_once(manufacturing_client):
    client = manufacturing_client
    form_data = {
        "username": "manufacturing_duplicate",
        "full_name": "Duplicate User",
        "role": "staff",
        "password": "test-password",
    }
    assert client.post(
        "/manufacturing/users/add", data=form_data
    ).status_code == 302
    assert client.post(
        "/manufacturing/users/add", data=form_data
    ).status_code == 302
    assert scalar(
        "SELECT COUNT(*) FROM users WHERE username = ? AND system = ?",
        ("manufacturing_duplicate", "manufacturing"),
    ) == 1


def test_resharpening_active_stage_check_is_serialized(manufacturing_client, monkeypatch):
    import modules.resharpening as resharpening_module

    customer_id, _, _, tool_id = create_parties_and_tool()
    connection = database.get_connection()
    batch_id = connection.execute(
        """INSERT INTO mfg_resharpen_batches
           (batch_no, batch_date, tool_id, customer_id, quantity)
           VALUES (?, ?, ?, ?, ?)""",
        ("RS-20261020-0001", "2026-10-20", tool_id, customer_id, 3),
    ).lastrowid
    connection.commit()
    connection.close()

    statements_by_request = []
    original_get_connection = resharpening_module.get_connection

    def traced_connection():
        connection = original_get_connection()
        statements = []
        connection.set_trace_callback(statements.append)
        statements_by_request.append(statements)
        return connection

    monkeypatch.setattr(
        resharpening_module, "get_connection", traced_connection
    )
    clients = [app_module.app.test_client(), app_module.app.test_client()]
    for client in clients:
        with client.session_transaction() as session:
            session.update(
                user_id=2,
                username="manufacturing_admin",
                role="admin",
                system="manufacturing",
            )

    def send(client):
        return client.post(
            f"/manufacturing/resharpen/batches/{batch_id}/send",
            data={
                "stages": ["resharpening"],
                "dispatch_date": "2026-10-20",
                "quantity_sent": "1",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(send, clients))

    assert all(response.status_code == 302 for response in responses)
    assert len(statements_by_request) == 2
    for statements in statements_by_request:
        lock_index = next(
            index
            for index, statement in enumerate(statements)
            if statement.strip().upper() == "BEGIN IMMEDIATE"
        )
        state_read_index = next(
            index
            for index, statement in enumerate(statements)
            if "SELECT quantity FROM mfg_resharpen_batches" in statement
        )
        assert lock_index < state_read_index
    assert scalar(
        "SELECT COUNT(*) FROM mfg_resharpen_stages WHERE batch_id = ?",
        (batch_id,),
    ) == 1


def test_document_adapter_converts_manufacturing_values_and_sql(monkeypatch):
    class Cursor:
        def __init__(self):
            self.statement = None
            self.parameters = None

        def execute(self, statement, parameters=()):
            self.statement = statement
            self.parameters = parameters

        def fetchone(self):
            return (83,)

    monkeypatch.setattr(document_database, "is_postgres_configured", lambda: True)
    raw_cursor = Cursor()
    cursor = document_database._DocumentCursor(raw_cursor)
    cursor.execute(
        """INSERT INTO mfg_job_batches
           (batch_no, batch_date, tool_id, quantity, length_needed_mm)
           VALUES (?, ?, ?, ?, ?)""",
        ("JW-20261019-0001", "2026-10-19", "7", "3", "42.5"),
    )
    assert raw_cursor.statement.endswith("RETURNING id")
    assert raw_cursor.parameters == (
        "JW-20261019-0001",
        date(2026, 10, 19),
        7,
        3,
        42.5,
    )
    assert cursor.lastrowid == 83

    cursor.execute(
        "UPDATE mfg_purchase_order_items SET received_qty = received_qty + ?",
        ("1.5",),
    )
    assert raw_cursor.parameters == (1.5,)

    cursor.execute("SELECT GROUP_CONCAT(stages) FROM mfg_job_stages")
    assert "STRING_AGG((stages)::text, ',')" in raw_cursor.statement
    cursor.execute("BEGIN IMMEDIATE")
    assert raw_cursor.statement == "SELECT pg_advisory_xact_lock(%s)"

    monkeypatch.setattr(document_database, "is_postgres_configured", lambda: False)
    raw_cursor = Cursor()
    cursor = document_database._DocumentCursor(raw_cursor)
    cursor.execute("BEGIN IMMEDIATE")
    assert raw_cursor.statement == "BEGIN IMMEDIATE"
