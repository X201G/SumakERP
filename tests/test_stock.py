import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module
import database
import modules.stock as stock_module


@pytest.fixture
def stock_client(tmp_path, monkeypatch):
    previous_database = database.DATABASE
    previous_testing = app_module.app.config.get("TESTING")
    database.close_postgres_pool()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database.DATABASE = str(tmp_path / "stock_test.db")
    database.create_tables()
    connection = database.get_connection()
    try:
        user_id = connection.execute(
            """INSERT INTO users (username, password_hash, full_name, role, system)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "stock_admin",
                generate_password_hash("stock-password"),
                "Stock Admin",
                "admin",
                "distribution",
            ),
        ).lastrowid
        category_id = connection.execute(
            "INSERT INTO categories (category_name) VALUES (?)", ("Components",)
        ).lastrowid
        unit_id = connection.execute(
            "INSERT INTO units (unit_name) VALUES (?)", ("Each",)
        ).lastrowid
        hsn_id = connection.execute(
            "INSERT INTO hsn (hsn_code) VALUES (?)", ("1234",)
        ).lastrowid
        product_id = connection.execute(
            """INSERT INTO products
               (product_name, category_id, unit_id, hsn_id, sale_price, purchase_price)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("Alpha Widget", category_id, unit_id, hsn_id, 12.5, 5.0),
        ).lastrowid
        connection.commit()
    finally:
        connection.close()

    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session.update(
            user_id=user_id,
            username="stock_admin",
            full_name="Stock Admin",
            role="admin",
            system="distribution",
        )
    yield client, product_id
    app_module.app.config["TESTING"] = previous_testing
    database.close_postgres_pool()
    database.DATABASE = previous_database


def add_movement(
    product_id,
    movement_type,
    quantity,
    source_no,
    movement_date,
    rate,
    source_type="opening",
):
    connection = database.get_connection()
    try:
        stock_module.add_stock_movement(
            connection.cursor(),
            product_id,
            movement_type,
            quantity,
            source_type,
            None,
            source_no,
            movement_date,
            rate,
        )
        connection.commit()
    finally:
        connection.close()


def test_stock_list_filters_case_insensitively_and_paginates(stock_client):
    client, product_id = stock_client
    connection = database.get_connection()
    try:
        connection.executemany(
            "INSERT INTO products (product_name) VALUES (?)",
            [(f"Part {number:02d}",) for number in range(21)],
        )
        connection.executemany(
            "INSERT INTO products (product_name) VALUES (?)",
            [("Beta Fixture",), ("Zulu Fixture",)],
        )
        connection.commit()
    finally:
        connection.close()

    response = client.get("/stock?search=aLpHa")
    assert response.status_code == 200
    assert b"Alpha Widget" in response.data
    assert b"Components" in response.data

    first_page = client.get("/stock?page=1")
    second_page = client.get("/stock?page=2")
    assert first_page.status_code == 200
    assert second_page.status_code == 200
    assert b"Page 1 of 2" in first_page.data
    assert b"Page 2 of 2" in second_page.data
    assert first_page.data.index(b"Alpha Widget") < first_page.data.index(b"Beta Fixture")
    assert first_page.data.index(b"Beta Fixture") < first_page.data.index(b"Part 00")
    assert product_id is not None


def test_stock_list_ledger_and_api_calculate_movements(stock_client):
    client, product_id = stock_client
    add_movement(product_id, "IN", 10, "IN-1", "2024-01-01", 5)
    add_movement(
        product_id,
        "OUT",
        3,
        "OUT-1",
        "2024-01-02",
        5,
        source_type="sales_invoice",
    )
    add_movement(product_id, "IN", 8, "IN-2", "2024-01-03", 4)

    listing = client.get("/stock?search=widget")
    assert listing.status_code == 200
    assert b">18.0<" in listing.data
    assert b">3.0<" in listing.data
    assert b"15.0" in listing.data

    ledger = client.get(
        f"/stock/ledger/{product_id}?from_date=2024-01-02"
        "&to_date=2024-01-03&sort=date_desc"
    )
    assert ledger.status_code == 200
    assert b"IN-2" in ledger.data
    assert b"OUT-1" in ledger.data
    assert b"IN-1" not in ledger.data
    opening_ledger = client.get(
        f"/stock/ledger/{product_id}?module=opening&from_date=2024-01-02"
        "&to_date=2024-01-03"
    )
    assert opening_ledger.status_code == 200
    assert b"IN-2" in opening_ledger.data
    assert b"OUT-1" not in opening_ledger.data
    amount_sorted = client.get(
        f"/stock/ledger/{product_id}?sort=amount_desc"
    )
    assert amount_sorted.data.index(b"IN-1") < amount_sorted.data.index(b"IN-2")
    assert amount_sorted.data.index(b"IN-2") < amount_sorted.data.index(b"OUT-1")

    api_response = client.get("/api/products-with-stock")
    assert api_response.status_code == 200
    product = next(
        row for row in api_response.json["products"] if row["id"] == product_id
    )
    assert product["stock"] == 15.0
    assert product["category"] == "Components"
    assert product["unit"] == "Each"
    assert product["hsn"] == "1234"


def test_empty_ledger_and_invalid_product(stock_client):
    client, product_id = stock_client
    assert client.get(f"/stock/ledger/{product_id}").status_code == 200
    assert client.get("/stock/ledger/999999").status_code == 404


def test_distribution_authorization_is_required(stock_client):
    client, _ = stock_client
    with client.session_transaction() as session:
        session["system"] = "manufacturing"
    response = client.get("/stock")
    assert response.status_code == 302
    assert b"system=distribution" in response.data


@pytest.mark.parametrize(
    "path",
    ("/stock", "/stock/ledger/1", "/api/products-with-stock"),
)
def test_query_failure_releases_connection(stock_client, monkeypatch, path):
    client, _ = stock_client

    class BrokenCursor:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("query failed")

    class TrackedConnection:
        closed = False

        def cursor(self):
            return BrokenCursor()

        def close(self):
            self.closed = True

    connection = TrackedConnection()
    monkeypatch.setattr(stock_module, "get_connection", lambda: connection)
    with pytest.raises(RuntimeError, match="query failed"):
        client.get(path)
    assert connection.closed


def test_render_failure_happens_after_connection_release(
    stock_client, monkeypatch
):
    client, _ = stock_client
    original_connection = database.get_connection()

    class TrackedConnection:
        closed = False

        def __getattr__(self, name):
            return getattr(original_connection, name)

        def close(self):
            self.closed = True
            original_connection.close()

    connection = TrackedConnection()
    monkeypatch.setattr(stock_module, "get_connection", lambda: connection)

    def fail_render(*_args, **_kwargs):
        raise RuntimeError("render failed")

    monkeypatch.setattr(stock_module, "render_template", fail_render)
    with pytest.raises(RuntimeError, match="render failed"):
        client.get("/stock")
    assert connection.closed


def test_stock_movement_foreign_key_restricts_product_deletion(stock_client):
    client, product_id = stock_client
    add_movement(product_id, "IN", 1, "IN-1", "2024-01-01", 2)
    connection = database.get_connection()
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(database.sqlite3.IntegrityError):
            connection.execute("DELETE FROM products WHERE id = ?", (product_id,))
        connection.rollback()
        count = connection.execute(
            "SELECT COUNT(*) FROM products WHERE id = ?", (product_id,)
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 1
