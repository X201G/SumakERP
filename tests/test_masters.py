import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module
import database


@pytest.fixture
def masters_client(tmp_path, monkeypatch):
    previous_database = database.DATABASE
    previous_testing = app_module.app.config.get("TESTING")
    database.close_postgres_pool()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database.DATABASE = str(tmp_path / "masters_test.db")
    database.create_tables()

    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session.update(
            user_id=100,
            username="masters_admin",
            full_name="Masters Admin",
            role="admin",
            system="distribution",
        )

    yield client

    app_module.app.config["TESTING"] = previous_testing
    database.close_postgres_pool()
    database.DATABASE = previous_database


def add_product_references():
    connection = database.get_connection()
    try:
        category_id = connection.execute(
            "INSERT INTO categories (category_name) VALUES (?)",
            ("Master Test Category",),
        ).lastrowid
        subcategory_id = connection.execute(
            """INSERT INTO subcategories (subcategory_name, category_id)
               VALUES (?, ?)""",
            ("Master Test Subcategory", category_id),
        ).lastrowid
        unit_id = connection.execute(
            "INSERT INTO units (unit_name) VALUES (?)", ("Master Test Unit",)
        ).lastrowid
        hsn_id = connection.execute(
            "INSERT INTO hsn (hsn_code) VALUES (?)", ("MASTER-TEST-HSN",)
        ).lastrowid
        supplier_id = connection.execute(
            "INSERT INTO suppliers (name) VALUES (?)", ("Master Test Supplier",)
        ).lastrowid
        connection.commit()
        return category_id, subcategory_id, unit_id, hsn_id, supplier_id
    finally:
        connection.close()


def read_product(product_id):
    connection = database.get_connection()
    try:
        return connection.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    finally:
        connection.close()


def test_taxonomy_and_hsn_crud_and_uniqueness(masters_client):
    client = masters_client
    assert client.post("/units/add", data={"unit_name": "Box"}).status_code == 302
    connection = database.get_connection()
    unit_id = connection.execute(
        "SELECT id FROM units WHERE unit_name = ?", ("Box",)
    ).fetchone()["id"]
    connection.close()
    assert client.get(f"/units/edit/{unit_id}").status_code == 200
    assert client.post(
        f"/units/edit/{unit_id}", data={"unit_name": "Carton"}
    ).status_code == 302
    assert client.get("/units").status_code == 200
    assert client.get(f"/units/delete/{unit_id}").status_code == 302

    assert client.post(
        "/categories/add", data={"category_name": "Hardware"}
    ).status_code == 302
    connection = database.get_connection()
    category_id = connection.execute(
        "SELECT id FROM categories WHERE category_name = ?", ("Hardware",)
    ).fetchone()["id"]
    connection.close()
    assert client.post(
        "/categories/add", data={"category_name": "Hardware"}
    ).status_code == 302
    assert client.post(
        f"/categories/edit/{category_id}", data={"category_name": "Fasteners"}
    ).status_code == 302

    assert client.post(
        "/subcategories/add",
        data={"subcategory_name": "Bolts", "category_id": str(category_id)},
    ).status_code == 302
    connection = database.get_connection()
    subcategory_id = connection.execute(
        "SELECT id FROM subcategories WHERE subcategory_name = ?", ("Bolts",)
    ).fetchone()["id"]
    connection.close()
    assert client.post(
        f"/subcategories/edit/{subcategory_id}",
        data={"subcategory_name": "Screws"},
    ).status_code == 302
    assert client.get("/subcategories").status_code == 200
    assert client.get(f"/subcategories/delete/{subcategory_id}").status_code == 302
    assert client.get(f"/categories/delete/{category_id}").status_code == 302

    assert client.post(
        "/hsn/add",
        data={"hsn_code": "H-1", "description": "Hardware", "gst_rate": "12.5"},
    ).status_code == 302
    connection = database.get_connection()
    hsn_id = connection.execute(
        "SELECT id FROM hsn WHERE hsn_code = ?", ("H-1",)
    ).fetchone()["id"]
    connection.close()
    assert client.post(
        f"/hsn/edit/{hsn_id}",
        data={"hsn_code": "H-2", "description": "Updated", "gst_rate": ""},
    ).status_code == 302
    connection = database.get_connection()
    hsn = connection.execute(
        "SELECT hsn_code, description, gst_rate FROM hsn WHERE id = ?", (hsn_id,)
    ).fetchone()
    connection.close()
    assert tuple(hsn) == ("H-2", "Updated", 18.0)
    assert client.get("/hsn").status_code == 200
    assert client.get(f"/hsn/delete/{hsn_id}").status_code == 302


def test_customer_supplier_and_transporter_crud(masters_client):
    client = masters_client
    assert client.post(
        "/customers/add",
        data={"name": "Customer A", "phone": "123", "email": "a@example.test"},
    ).status_code == 302
    connection = database.get_connection()
    customer_id = connection.execute(
        "SELECT id FROM customers WHERE name = ?", ("Customer A",)
    ).fetchone()["id"]
    connection.close()
    assert client.get("/customers?page=-2").status_code == 200
    assert client.post(
        f"/customers/edit/{customer_id}",
        data={"name": "Customer B", "city": "Pune"},
    ).status_code == 302
    connection = database.get_connection()
    customer = connection.execute(
        "SELECT name, city FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    connection.close()
    assert tuple(customer) == ("Customer B", "Pune")
    assert client.get(f"/customers/delete/{customer_id}").status_code == 302

    assert client.post(
        "/suppliers/add", data={"name": "Supplier A"}
    ).status_code == 302
    assert client.get("/suppliers").status_code == 200
    connection = database.get_connection()
    supplier_id = connection.execute(
        "SELECT id FROM suppliers WHERE name = ?", ("Supplier A",)
    ).fetchone()["id"]
    connection.close()
    assert client.get(f"/suppliers/delete/{supplier_id}").status_code == 302

    assert client.post(
        "/transporters/add", data={"name": "Transporter A"}
    ).status_code == 302
    assert client.get("/transporters").status_code == 200
    connection = database.get_connection()
    transporter_id = connection.execute(
        "SELECT id FROM transporters WHERE name = ?", ("Transporter A",)
    ).fetchone()["id"]
    connection.close()
    assert client.get(f"/transporters/delete/{transporter_id}").status_code == 302


def test_product_numeric_fields_and_empty_references_are_sqlite_compatible(
    masters_client,
):
    client = masters_client
    category_id, subcategory_id, unit_id, hsn_id, supplier_id = (
        add_product_references()
    )
    response = client.post(
        "/products/add",
        data={
            "product_name": "Steel Bolt",
            "category_id": str(category_id),
            "subcategory_id": str(subcategory_id),
            "unit_id": str(unit_id),
            "hsn_id": str(hsn_id),
            "supplier_id": str(supplier_id),
            "tax_rate": "5.5",
            "purchase_price": "12.75",
            "purchase_discount": "2.25",
            "sale_price": "18.5",
            "sale_discount": "1.5",
        },
    )
    assert response.status_code == 302
    connection = database.get_connection()
    product_id = connection.execute(
        "SELECT id FROM products WHERE product_name = ?", ("Steel Bolt",)
    ).fetchone()["id"]
    connection.close()
    product = read_product(product_id)
    assert product["category_id"] == category_id
    assert product["subcategory_id"] == subcategory_id
    assert product["unit_id"] == unit_id
    assert product["hsn_id"] == hsn_id
    assert product["supplier_id"] == supplier_id
    assert product["tax_rate"] == 5.5
    assert product["purchase_price"] == 12.75
    assert product["purchase_discount"] == 2.25
    assert product["sale_price"] == 18.5
    assert product["sale_discount"] == 1.5
    assert client.get("/products?page=-1").status_code == 200
    assert client.get(f"/products/delete/{product_id}").status_code == 302
    assert read_product(product_id) is None

    assert client.post(
        "/products/add",
        data={
            "product_name": "Unlinked Product",
            "category_id": "",
            "subcategory_id": "",
            "unit_id": "",
            "hsn_id": "",
            "supplier_id": "",
        },
    ).status_code == 302
    connection = database.get_connection()
    unlinked = connection.execute(
        "SELECT category_id, subcategory_id, unit_id, hsn_id, supplier_id, tax_rate "
        "FROM products WHERE product_name = ?",
        ("Unlinked Product",),
    ).fetchone()
    connection.close()
    assert tuple(unlinked) == (None, None, None, None, None, 18.0)


def test_invalid_values_foreign_keys_deletion_restriction_and_connection_reuse(
    masters_client,
):
    client = masters_client
    invalid_number = client.post(
        "/hsn/add",
        data={"hsn_code": "UNIQUE-HSN", "gst_rate": "not-a-number"},
        follow_redirects=True,
    )
    assert invalid_number.status_code == 200
    assert b"valid number" in invalid_number.get_data().lower()

    assert client.post(
        "/hsn/add", data={"hsn_code": "UNIQUE-HSN", "gst_rate": "18"}
    ).status_code == 302
    duplicate = client.post(
        "/hsn/add", data={"hsn_code": "UNIQUE-HSN", "gst_rate": "18"},
        follow_redirects=True,
    )
    assert duplicate.status_code == 200
    assert b"conflicts with existing data" in duplicate.get_data().lower()

    before = database.get_connection()
    products_before = before.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    before.close()
    invalid_reference = client.post(
        "/products/add",
        data={
            "product_name": "Invalid Reference",
            "category_id": "999999",
            "unit_id": "",
            "subcategory_id": "",
            "hsn_id": "",
            "supplier_id": "",
        },
        follow_redirects=True,
    )
    assert invalid_reference.status_code == 200
    assert b"conflicts with existing data" in invalid_reference.get_data().lower()
    after = database.get_connection()
    assert after.execute("SELECT COUNT(*) FROM products").fetchone()[0] == products_before
    after.close()

    _, _, unit_id, _, _ = add_product_references()
    product_response = client.post(
        "/products/add",
        data={
            "product_name": "Referenced Product",
            "unit_id": str(unit_id),
            "category_id": "",
            "subcategory_id": "",
            "hsn_id": "",
            "supplier_id": "",
        },
    )
    assert product_response.status_code == 302
    connection = database.get_connection()
    product_id = connection.execute(
        "SELECT id FROM products WHERE product_name = ?", ("Referenced Product",)
    ).fetchone()["id"]
    connection.execute(
        """INSERT INTO stock_movements (product_id, movement_type, quantity)
           VALUES (?, ?, ?)""",
        (product_id, "in", 1),
    )
    connection.commit()
    connection.close()

    restricted_delete = client.get(
        f"/products/delete/{product_id}", follow_redirects=True
    )
    assert restricted_delete.status_code == 200
    assert b"still referenced" in restricted_delete.get_data().lower()
    assert read_product(product_id) is not None

    connection = database.get_connection()
    connection.execute("DELETE FROM stock_movements WHERE product_id = ?", (product_id,))
    connection.commit()
    connection.close()
    assert client.get(f"/products/delete/{product_id}").status_code == 302
    assert client.get("/products").status_code == 200


def test_masters_routes_require_distribution_login(masters_client):
    client = masters_client
    with client.session_transaction() as session:
        session.clear()
    response = client.get("/units")
    assert response.status_code == 302
    assert "/login" in response.location

    with client.session_transaction() as session:
        session.update(
            user_id=101,
            username="manufacturing_user",
            role="admin",
            system="manufacturing",
        )
    response = client.get("/products")
    assert response.status_code == 302
    assert "/login" in response.location
