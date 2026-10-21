import os
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database
from postgres_migrations import (
    UnsafeMigrationTargetError,
    apply_postgres_migrations,
    get_test_database_url,
)


def postgres_url_for_schema(database_url, schema):
    parsed = urlsplit(database_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "options"
    ]
    query.append(("options", f"-c search_path={schema}"))
    return urlunsplit(parsed._replace(query=urlencode(query, quote_via=quote)))


class PostgreSQLMastersIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("TEST_DATABASE_URL", "").strip():
            raise unittest.SkipTest(
                "Set a guarded TEST_DATABASE_URL to run PostgreSQL integration tests."
            )
        if os.environ.get("ALLOW_TEST_DATABASE_MIGRATIONS", "").strip() != "YES":
            raise unittest.SkipTest(
                "Set ALLOW_TEST_DATABASE_MIGRATIONS=YES to run PostgreSQL integration tests."
            )
        try:
            import psycopg
        except ImportError:
            raise RuntimeError(
                "PostgreSQL masters integration tests require Psycopg 3."
            ) from None
        from psycopg import sql

        cls.psycopg = psycopg
        cls.sql = sql
        cls.previous_database_url = os.environ.get("DATABASE_URL")
        try:
            cls.test_database_url = get_test_database_url()
        except UnsafeMigrationTargetError as error:
            raise RuntimeError(
                f"PostgreSQL masters integration tests cannot start: {error}"
            ) from None

        cls.schema = "sumak_masters_test_" + uuid4().hex
        schema_created = False
        database.close_postgres_pool()
        try:
            with psycopg.connect(cls.test_database_url, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema))
                )
            schema_created = True
            cls.scoped_database_url = postgres_url_for_schema(
                cls.test_database_url, cls.schema
            )
            with psycopg.connect(cls.scoped_database_url) as connection:
                context = connection.execute(
                    "SELECT current_database(), current_schema()"
                ).fetchone()
                if context[1] != cls.schema:
                    raise AssertionError(
                        "Scoped masters test connection has an unexpected schema."
                    )
                apply_postgres_migrations(connection, schema=cls.schema)

            os.environ["DATABASE_URL"] = cls.scoped_database_url
            database.close_postgres_pool()
            cls.app_module = __import__("app")
            cls.app = cls.app_module.app
            cls.previous_app_testing = cls.app.config.get("TESTING")
            cls.app.config["TESTING"] = True
        except Exception as error:
            database.close_postgres_pool()
            if cls.previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = cls.previous_database_url
            if schema_created:
                try:
                    with psycopg.connect(
                        cls.test_database_url, autocommit=True
                    ) as admin:
                        admin.execute(
                            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                                sql.Identifier(cls.schema)
                            )
                        )
                except psycopg.Error:
                    raise RuntimeError(
                        "PostgreSQL masters setup failed and schema cleanup failed; "
                        "connection details were withheld."
                    ) from None
            if isinstance(error, psycopg.Error):
                raise RuntimeError(
                    "Unable to prepare the guarded PostgreSQL masters test schema; "
                    "connection details were withheld."
                ) from None
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            with cls.psycopg.connect(
                cls.test_database_url, autocommit=True
            ) as admin:
                admin.execute(
                    cls.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        cls.sql.Identifier(cls.schema)
                    )
                )
        except cls.psycopg.Error:
            raise RuntimeError(
                "Unable to clean up the PostgreSQL masters test schema; "
                "connection details were withheld."
            ) from None
        finally:
            cls.app.config["TESTING"] = cls.previous_app_testing
            database.close_postgres_pool()
            if cls.previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = cls.previous_database_url

    def setUp(self):
        database.close_postgres_pool()
        os.environ["DATABASE_URL"] = self.scoped_database_url
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session.update(
                user_id=11,
                username="masters_test_admin",
                full_name="Masters Test Admin",
                role="admin",
                system="distribution",
            )

    def execute(self, statement, parameters=()):
        connection = database.get_connection()
        try:
            return connection.execute(statement, parameters).fetchall()
        finally:
            connection.close()

    def add_product_references(self, suffix):
        connection = database.get_connection()
        try:
            category_id = connection.execute(
                "INSERT INTO categories (category_name) VALUES (%s) RETURNING id",
                (f"Master Category {suffix}",),
            ).fetchone()[0]
            subcategory_id = connection.execute(
                """INSERT INTO subcategories (subcategory_name, category_id)
                   VALUES (%s, %s) RETURNING id""",
                (f"Master Subcategory {suffix}", category_id),
            ).fetchone()[0]
            unit_id = connection.execute(
                "INSERT INTO units (unit_name) VALUES (%s) RETURNING id",
                (f"Master Unit {suffix}",),
            ).fetchone()[0]
            hsn_id = connection.execute(
                "INSERT INTO hsn (hsn_code) VALUES (%s) RETURNING id",
                (f"MASTER-{suffix}",),
            ).fetchone()[0]
            supplier_id = connection.execute(
                "INSERT INTO suppliers (name) VALUES (%s) RETURNING id",
                (f"Master Supplier {suffix}",),
            ).fetchone()[0]
            connection.commit()
            return category_id, subcategory_id, unit_id, hsn_id, supplier_id
        finally:
            connection.close()

    def test_core_master_routes_create_update_list_and_delete(self):
        assert self.client.post(
            "/units/add", data={"unit_name": "PostgreSQL Box"}
        ).status_code == 302
        unit_id = self.execute(
            "SELECT id FROM units WHERE unit_name = %s", ("PostgreSQL Box",)
        )[0][0]
        assert self.client.post(
            f"/units/edit/{unit_id}", data={"unit_name": "PostgreSQL Carton"}
        ).status_code == 302
        assert self.client.get("/units").status_code == 200
        assert self.client.get(f"/units/delete/{unit_id}").status_code == 302

        assert self.client.post(
            "/categories/add", data={"category_name": "PostgreSQL Hardware"}
        ).status_code == 302
        category_id = self.execute(
            "SELECT id FROM categories WHERE category_name = %s",
            ("PostgreSQL Hardware",),
        )[0][0]
        assert self.client.post(
            f"/categories/edit/{category_id}",
            data={"category_name": "PostgreSQL Fasteners"},
        ).status_code == 302
        assert self.client.get("/categories").status_code == 200
        assert self.client.get(f"/categories/delete/{category_id}").status_code == 302

        assert self.client.post(
            "/subcategories/add",
            data={"subcategory_name": "PostgreSQL Bolt", "category_id": ""},
        ).status_code == 302
        subcategory_id = self.execute(
            "SELECT id FROM subcategories WHERE subcategory_name = %s",
            ("PostgreSQL Bolt",),
        )[0][0]
        assert self.client.post(
            f"/subcategories/edit/{subcategory_id}",
            data={"subcategory_name": "PostgreSQL Screw"},
        ).status_code == 302
        assert self.client.get("/subcategories").status_code == 200
        assert self.client.get(
            f"/subcategories/delete/{subcategory_id}"
        ).status_code == 302

        assert self.client.post(
            "/hsn/add",
            data={
                "hsn_code": "POSTGRES-HSN-1",
                "description": "Integration item",
                "gst_rate": "5.5",
            },
        ).status_code == 302
        hsn_id = self.execute(
            "SELECT id FROM hsn WHERE hsn_code = %s", ("POSTGRES-HSN-1",)
        )[0][0]
        assert self.client.post(
            f"/hsn/edit/{hsn_id}",
            data={
                "hsn_code": "POSTGRES-HSN-2",
                "description": "Updated item",
                "gst_rate": "12",
            },
        ).status_code == 302
        assert self.client.get("/hsn").status_code == 200
        assert self.client.get(f"/hsn/delete/{hsn_id}").status_code == 302

        assert self.client.post(
            "/customers/add", data={"name": "PostgreSQL Customer"}
        ).status_code == 302
        customer_id = self.execute(
            "SELECT id FROM customers WHERE name = %s", ("PostgreSQL Customer",)
        )[0][0]
        assert self.client.post(
            f"/customers/edit/{customer_id}",
            data={"name": "Updated PostgreSQL Customer", "city": "Pune"},
        ).status_code == 302
        assert self.client.get("/customers?page=-1").status_code == 200
        assert self.client.get(f"/customers/delete/{customer_id}").status_code == 302

        assert self.client.post(
            "/suppliers/add", data={"name": "PostgreSQL Supplier"}
        ).status_code == 302
        supplier_id = self.execute(
            "SELECT id FROM suppliers WHERE name = %s", ("PostgreSQL Supplier",)
        )[0][0]
        assert self.client.get("/suppliers").status_code == 200
        assert self.client.get(f"/suppliers/delete/{supplier_id}").status_code == 302

        assert self.client.post(
            "/transporters/add", data={"name": "PostgreSQL Transporter"}
        ).status_code == 302
        transporter_id = self.execute(
            "SELECT id FROM transporters WHERE name = %s",
            ("PostgreSQL Transporter",),
        )[0][0]
        assert self.client.get("/transporters").status_code == 200
        assert self.client.get(
            f"/transporters/delete/{transporter_id}"
        ).status_code == 302

    def test_product_numbers_foreign_keys_and_deletion_restrictions(self):
        refs = self.add_product_references("product")
        category_id, subcategory_id, unit_id, hsn_id, supplier_id = refs
        response = self.client.post(
            "/products/add",
            data={
                "product_name": "PostgreSQL Product",
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
        self.assertEqual(response.status_code, 302)
        product_id = self.execute(
            "SELECT id FROM products WHERE product_name = %s",
            ("PostgreSQL Product",),
        )[0][0]
        product = self.execute(
            """SELECT category_id, subcategory_id, unit_id, hsn_id, supplier_id,
                      tax_rate, purchase_price, purchase_discount, sale_price,
                      sale_discount
               FROM products WHERE id = %s""",
            (product_id,),
        )[0]
        self.assertEqual(
            tuple(product),
            (category_id, subcategory_id, unit_id, hsn_id, supplier_id,
             5.5, 12.75, 2.25, 18.5, 1.5),
        )
        self.assertEqual(self.client.get("/products").status_code, 200)

        invalid_reference = self.client.post(
            "/products/add",
            data={
                "product_name": "PostgreSQL Invalid Product",
                "category_id": "999999999",
                "unit_id": "",
                "subcategory_id": "",
                "hsn_id": "",
                "supplier_id": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(invalid_reference.status_code, 200)
        self.assertIn(b"conflicts with existing data", invalid_reference.data.lower())

        connection = database.get_connection()
        try:
            connection.execute(
                """INSERT INTO stock_movements
                   (product_id, movement_type, quantity) VALUES (%s, %s, %s)""",
                (product_id, "in", 1),
            )
            connection.commit()
        finally:
            connection.close()
        restricted_delete = self.client.get(
            f"/products/delete/{product_id}", follow_redirects=True
        )
        self.assertEqual(restricted_delete.status_code, 200)
        self.assertIn(b"still referenced", restricted_delete.data.lower())
        self.assertEqual(
            self.execute("SELECT id FROM products WHERE id = %s", (product_id,))[0][0],
            product_id,
        )

        connection = database.get_connection()
        try:
            connection.execute(
                "DELETE FROM stock_movements WHERE product_id = %s", (product_id,)
            )
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(
            self.client.get(f"/products/delete/{product_id}").status_code, 302
        )
        self.assertEqual(
            self.execute("SELECT id FROM products WHERE id = %s", (product_id,)), []
        )

    def test_unique_validation_rollback_and_connection_reuse(self):
        self.assertEqual(
            self.client.post(
                "/categories/add",
                data={"category_name": "PostgreSQL Unique Category"},
            ).status_code,
            302,
        )
        duplicate = self.client.post(
            "/categories/add",
            data={"category_name": "PostgreSQL Unique Category"},
            follow_redirects=True,
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertIn(b"conflicts with existing data", duplicate.data.lower())

        invalid_number = self.client.post(
            "/hsn/add",
            data={"hsn_code": "POSTGRES-INVALID-RATE", "gst_rate": "bad"},
            follow_redirects=True,
        )
        self.assertEqual(invalid_number.status_code, 200)
        self.assertIn(b"valid number", invalid_number.data.lower())
        self.assertEqual(self.client.get("/products").status_code, 200)

    def test_distribution_authorization_is_preserved(self):
        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.get("/units")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

        with self.client.session_transaction() as session:
            session.update(
                user_id=12,
                username="manufacturing_user",
                role="admin",
                system="manufacturing",
            )
        response = self.client.get("/products")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)


if __name__ == "__main__":
    unittest.main()
