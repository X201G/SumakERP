import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import database
from postgres_migrations import (
    MIGRATIONS_DIR,
    MigrationChecksumError,
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
    return urlunsplit(
        parsed._replace(query=urlencode(query, quote_via=quote))
    )


class TestPostgresMigrationTargetGuard(unittest.TestCase):
    def test_requires_an_explicit_test_database_url(self):
        with self.assertRaises(UnsafeMigrationTargetError):
            get_test_database_url(
                {
                    "DATABASE_URL": "postgresql://prod.example/erp",
                    "ALLOW_TEST_DATABASE_MIGRATIONS": "YES",
                }
            )

    def test_requires_explicit_migration_opt_in(self):
        with self.assertRaises(UnsafeMigrationTargetError):
            get_test_database_url(
                {"TEST_DATABASE_URL": "postgresql://localhost/sumak_test"}
            )

    def test_requires_a_non_production_marker_or_host_allowlist(self):
        env = {
            "TEST_DATABASE_URL": "postgresql://localhost/postgres",
            "ALLOW_TEST_DATABASE_MIGRATIONS": "YES",
        }
        with self.assertRaises(UnsafeMigrationTargetError):
            get_test_database_url(env)

        env["TEST_DATABASE_HOST_ALLOWLIST"] = "localhost"
        self.assertEqual(get_test_database_url(env), env["TEST_DATABASE_URL"])

    def test_rejects_production_like_targets_even_with_test_database_name(self):
        with self.assertRaises(UnsafeMigrationTargetError):
            get_test_database_url(
                {
                    "TEST_DATABASE_URL": "postgresql://db-prod.example/sumak_test",
                    "ALLOW_TEST_DATABASE_MIGRATIONS": "YES",
                    "TEST_DATABASE_HOST_ALLOWLIST": "db-prod.example",
                }
            )

    def test_rejects_non_postgresql_urls(self):
        with self.assertRaises(UnsafeMigrationTargetError):
            get_test_database_url(
                {
                    "TEST_DATABASE_URL": "sqlite:///sumak_test.db",
                    "ALLOW_TEST_DATABASE_MIGRATIONS": "YES",
                }
            )

    def test_accepts_named_disposable_database(self):
        test_url = "postgresql://localhost/sumak_test"
        self.assertEqual(
            get_test_database_url(
                {
                    "TEST_DATABASE_URL": test_url,
                    "ALLOW_TEST_DATABASE_MIGRATIONS": "YES",
                }
            ),
            test_url,
        )

    def test_schema_scoped_url_remains_valid_for_application_and_psycopg(self):
        from psycopg.conninfo import conninfo_to_dict

        base_url = (
            "postgresql://localhost:5432/sumak_test"
            "?sslmode=require&application_name=sumakerp-tests"
        )
        schema = "sumak_test_schema"
        scoped_url = postgres_url_for_schema(base_url, schema)
        parsed = urlsplit(scoped_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))

        self.assertEqual(parsed.scheme, "postgresql")
        self.assertEqual(parsed.hostname, "localhost")
        self.assertEqual(parsed.port, 5432)
        self.assertEqual(parsed.path, "/sumak_test")
        self.assertEqual(query["sslmode"], "require")
        self.assertEqual(query["application_name"], "sumakerp-tests")
        self.assertEqual(query["options"], f"-c search_path={schema}")

        with patch.dict(os.environ, {"DATABASE_URL": scoped_url}):
            self.assertEqual(database._postgres_url_from_environment(), scoped_url)

        psycopg_options = conninfo_to_dict(scoped_url)
        self.assertEqual(psycopg_options["host"], "localhost")
        self.assertEqual(psycopg_options["dbname"], "sumak_test")
        self.assertEqual(
            psycopg_options["options"], f"-c search_path={schema}"
        )


class PostgreSQLHiringIntegrationTests(unittest.TestCase):
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
                "PostgreSQL integration tests require the Psycopg dependency."
            ) from None
        from psycopg import sql

        cls.psycopg = psycopg
        cls.previous_database_url = os.environ.get("DATABASE_URL")
        try:
            cls.test_database_url = get_test_database_url()
        except UnsafeMigrationTargetError as error:
            raise RuntimeError(
                f"PostgreSQL integration tests cannot start: {error}"
            ) from None

        cls.schema = "sumak_hiring_test_" + uuid4().hex
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
                    """SELECT current_database(), current_schema(),
                              current_setting('search_path')"""
                ).fetchone()
                if context[1] != cls.schema:
                    raise AssertionError(
                        "Scoped hiring test connection has unexpected schema context: "
                        f"database={context[0]!r}, schema={context[1]!r}, "
                        f"search_path={context[2]!r}"
                    )
                apply_postgres_migrations(connection, schema=cls.schema)
            cls._configure_application_database()
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
                        "PostgreSQL integration setup failed and temporary schema "
                        "cleanup also failed; connection details were withheld."
                    ) from None
            if isinstance(error, psycopg.Error):
                raise RuntimeError(
                    "Unable to prepare the guarded PostgreSQL integration database; "
                    "connection details were withheld."
                ) from None
            raise

    @classmethod
    def _configure_application_database(cls):
        os.environ["DATABASE_URL"] = cls.scoped_database_url
        database.close_postgres_pool()
        connection = database.get_connection()
        try:
            context = connection.execute(
                """SELECT current_database(), current_schema(),
                          current_setting('search_path')"""
            ).fetchone()
            if context[1] != cls.schema:
                raise AssertionError(
                    "Application database pool has unexpected test schema context: "
                    f"database={context[0]!r}, schema={context[1]!r}, "
                    f"search_path={context[2]!r}"
                )
        finally:
            connection.close()

    @classmethod
    def tearDownClass(cls):
        import psycopg
        from psycopg import sql

        try:
            with psycopg.connect(cls.test_database_url, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(cls.schema)
                    )
                )
        except psycopg.Error:
            raise RuntimeError(
                "Unable to clean up the PostgreSQL integration schema; "
                "connection details were withheld."
            ) from None
        finally:
            database.close_postgres_pool()
            if cls.previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = cls.previous_database_url

    def setUp(self):
        self._configure_application_database()
        import app

        self.app = app.app
        self.previous_app_testing = self.app.config.get("TESTING")
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        connection = database.get_connection()
        try:
            connection.execute(
                """TRUNCATE TABLE
                   sales_payments, purchase_payments,
                   sales_return_items, sales_returns, sales_challan_items,
                   sales_challans, proforma_invoice_items, proforma_invoices,
                   sales_order_items, sales_orders, sales_quotation_items,
                   sales_quotations, sales_invoice_items, sales_invoices,
                   purchase_return_items, purchase_returns, purchase_grn_items,
                   purchase_grn, purchase_invoice_items, purchase_invoices,
                   purchase_order_items, purchase_orders, stock_movements,
                   products, suppliers, hsn, units, subcategories, categories,
                   customers, transporters RESTART IDENTITY CASCADE"""
            )
            connection.execute("TRUNCATE TABLE users RESTART IDENTITY CASCADE")
            user_id = connection.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, role, system)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                ("test_hiring_admin", "not-used", "Hiring Admin", "admin", "hiring"),
            ).fetchone()[0]
            self.projects_admin_id = connection.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, role, system)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                ("test_projects_admin", "not-used", "Projects Admin", "admin", "projects"),
            ).fetchone()[0]
            self.project_owner_id = connection.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, role, system)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                ("test_project_owner", "not-used", "Project Owner", "user", "projects"),
            ).fetchone()[0]
            self.project_peer_id = connection.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, role, system)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                ("test_project_peer", "not-used", "Project Peer", "user", "projects"),
            ).fetchone()[0]
            self.project_external_id = connection.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, role, system)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                ("test_project_external", "not-used", "Project External", "external", "projects"),
            ).fetchone()[0]
            self.dispatcher_id = connection.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, role, system)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                ("test_dispatcher", "not-used", "Test Dispatcher", "dispatcher", "delivery"),
            ).fetchone()[0]
            self.driver_id = connection.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, role, system)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                ("test_driver", "not-used", "Test Driver", "driver", "delivery"),
            ).fetchone()[0]
            self.other_driver_id = connection.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, role, system)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                ("test_other_driver", "not-used", "Other Driver", "driver", "delivery"),
            ).fetchone()[0]
            self.stock_admin_id = connection.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, role, system)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                ("test_stock_admin", "not-used", "Stock Admin", "admin", "distribution"),
            ).fetchone()[0]
            self.job_id = connection.execute(
                """INSERT INTO hiring_jobs
                   (title, status, created_by)
                   VALUES (%s, %s, %s) RETURNING id""",
                ("PostgreSQL Hiring Test", "open", user_id),
            ).fetchone()[0]
            connection.commit()
        finally:
            connection.close()

    def tearDown(self):
        self.app.config["TESTING"] = self.previous_app_testing

    def application_data(self, **overrides):
        data = {
            "full_name": "PostgreSQL Applicant",
            "email": "candidate@example.test",
            "phone": "1234567890",
            "resume_url": "https://example.test/resume",
            "cover_note": "Interested in the role.",
            "privacy_consent": "yes",
        }
        data.update(overrides)
        return data

    def submit_application(self, job_id=None, **overrides):
        selected_job_id = self.job_id if job_id is None else job_id
        return self.client.post(
            f"/careers/{selected_job_id}/apply",
            data=self.application_data(**overrides),
        )

    def login_as(self, user_id, username, role, system):
        with self.client.session_transaction() as session:
            session.update(
                user_id=user_id,
                username=username,
                full_name=username,
                role=role,
                system=system,
            )

    def create_delivery_route(self, **overrides):
        data = {
            "title": "Staging delivery route",
            "vehicle": "Van 42",
            "route_date": "2026-10-20",
            "driver_id": str(self.driver_id),
            "stop_count": "2",
            "stops-0-customer_name": "First stop",
            "stops-0-customer_phone": "555-0101",
            "stops-0-address": "1 Example Road",
            "stops-1-customer_name": "Second stop",
            "stops-1-customer_phone": "555-0102",
            "stops-1-maps_url": "https://maps.example/second",
        }
        data.update(overrides)
        return self.client.post("/delivery/routes/new", data=data)

    def create_project(self, name, **overrides):
        data = {
            "name": name,
            "description": "A scoped PostgreSQL project",
            "client": "Test client",
            "priority": "normal",
            "start_date": "2026-10-01",
            "due_date": "2026-11-30",
        }
        data.update(overrides)
        return self.client.post("/projects/new", data=data)

    def create_stock_product(self, connection, name="Widget Alpha"):
        category_id = connection.execute(
            "INSERT INTO categories (category_name) VALUES (%s) RETURNING id",
            ("Stock Components",),
        ).fetchone()[0]
        subcategory_id = connection.execute(
            """INSERT INTO subcategories (category_id, subcategory_name)
               VALUES (%s, %s) RETURNING id""",
            (category_id, "Stock Parts"),
        ).fetchone()[0]
        unit_id = connection.execute(
            "INSERT INTO units (unit_name) VALUES (%s) RETURNING id", ("Each",)
        ).fetchone()[0]
        hsn_id = connection.execute(
            """INSERT INTO hsn (hsn_code, description)
               VALUES (%s, %s) RETURNING id""",
            ("9988", "Stock integration test"),
        ).fetchone()[0]
        supplier_id = connection.execute(
            "INSERT INTO suppliers (name) VALUES (%s) RETURNING id",
            ("Stock Supplier",),
        ).fetchone()[0]
        product_id = connection.execute(
            """INSERT INTO products
               (product_name, category_id, subcategory_id, unit_id, hsn_id,
                supplier_id, sale_price, purchase_price)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                name,
                category_id,
                subcategory_id,
                unit_id,
                hsn_id,
                supplier_id,
                12.5,
                5.0,
            ),
        ).fetchone()[0]
        return product_id

    def create_distribution_masters(self, connection):
        customer_id = connection.execute(
            "INSERT INTO customers (name) VALUES (%s) RETURNING id",
            ("Document Customer",),
        ).fetchone()[0]
        transporter_id = connection.execute(
            "INSERT INTO transporters (name) VALUES (%s) RETURNING id",
            ("Document Transporter",),
        ).fetchone()[0]
        supplier_id = connection.execute(
            "INSERT INTO suppliers (name) VALUES (%s) RETURNING id",
            ("Document Supplier",),
        ).fetchone()[0]
        product_id = self.create_stock_product(connection, "Document Widget")
        return customer_id, transporter_id, supplier_id, product_id

    def test_migrations_apply_to_an_empty_schema(self):
        from psycopg import sql

        empty_schema = "sumak_empty_test_" + uuid4().hex
        try:
            with self.psycopg.connect(self.test_database_url, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(empty_schema))
                )
            scoped_url = postgres_url_for_schema(
                self.test_database_url, empty_schema
            )
            with self.psycopg.connect(scoped_url) as connection:
                context = connection.execute(
                    """SELECT current_database(), current_schema(),
                              current_setting('search_path'),
                              to_regclass('public.sumak_schema_migrations') IS NOT NULL"""
                ).fetchone()
                self.assertEqual(
                    context[1],
                    empty_schema,
                    msg=(
                        "Unexpected scoped connection context: "
                        f"database={context[0]!r}, schema={context[1]!r}, "
                        f"search_path={context[2]!r}, "
                        f"public_ledger_exists={context[3]!r}"
                    ),
                )
                applied = apply_postgres_migrations(
                    connection, schema=empty_schema
                )
                self.assertEqual(
                    applied,
                    [
                        "0001",
                        "0002",
                        "0003",
                        "0004",
                        "0005",
                        "0006",
                        "0007",
                        "0008",
                    ],
                    msg=(
                        f"Migration result for context {context!r}: "
                        f"public ledger present={context[3]!r}"
                    ),
                )
                self.assertEqual(
                    apply_postgres_migrations(connection, schema=empty_schema), []
                )
                tables = connection.execute(
                    """SELECT COUNT(*) FROM information_schema.tables
                       WHERE table_schema = %s
                         AND table_name IN (
                             'users', 'hiring_jobs', 'hiring_candidates',
                             'delivery_routes', 'delivery_route_stops',
                             'delivery_notifications', 'delivery_push_subscriptions',
                             'projects', 'project_user_shares', 'project_tasks',
                             'project_task_notes', 'categories', 'subcategories',
                             'units', 'hsn', 'suppliers', 'products',
                             'stock_movements', 'customers', 'transporters',
                             'purchase_orders', 'purchase_order_items',
                             'purchase_invoices', 'purchase_invoice_items',
                             'purchase_grn', 'purchase_grn_items',
                             'purchase_returns', 'purchase_return_items',
                             'sales_invoices', 'sales_invoice_items',
                             'sales_quotations', 'sales_quotation_items',
                             'sales_orders', 'sales_order_items',
                             'proforma_invoices', 'proforma_invoice_items',
                             'sales_challans', 'sales_challan_items',
                             'sales_returns', 'sales_return_items',
                             'sales_payments', 'purchase_payments',
                             'document_number_counters'
                         )""",
                    (empty_schema,),
                ).fetchone()[0]
                self.assertEqual(tables, 43)
                ledger_count = connection.execute(
                    """SELECT COUNT(*) FROM information_schema.tables
                       WHERE table_schema = %s
                         AND table_name = 'sumak_schema_migrations'""",
                    (empty_schema,),
                ).fetchone()[0]
                self.assertEqual(ledger_count, 1)
                indexes = {
                    row[0]
                    for row in connection.execute(
                        "SELECT indexname FROM pg_indexes WHERE schemaname = %s",
                        (empty_schema,),
                    ).fetchall()
                }
                self.assertTrue(
                    {
                        "idx_delivery_routes_driver_date",
                        "idx_delivery_route_stops_route",
                        "idx_delivery_notifications_user_read",
                        "idx_delivery_push_subscriptions_user",
                        "idx_projects_stage",
                        "idx_project_user_shares_user",
                        "idx_project_tasks_project",
                        "idx_project_task_notes_task",
                        "idx_stock_movements_product_date_id",
                        "idx_stock_movements_product_source_date",
                        "idx_purchase_orders_supplier_date",
                        "idx_purchase_order_items_order",
                        "idx_purchase_grn_items_grn",
                        "idx_purchase_return_items_return",
                        "idx_sales_invoices_customer_date",
                        "idx_sales_invoice_items_source",
                        "idx_sales_challans_customer_date",
                        "idx_sales_challan_items_challan",
                        "idx_sales_returns_customer_date",
                        "idx_sales_return_items_return",
                        "idx_sales_payments_customer_date",
                        "idx_sales_payments_payment_date",
                        "idx_sales_payments_invoice",
                        "idx_purchase_payments_supplier_date",
                        "idx_purchase_payments_payment_date",
                        "idx_purchase_payments_invoice",
                    }.issubset(indexes)
                )
                constraints = {
                    row[0]
                    for row in connection.execute(
                        """SELECT constraint_name
                           FROM information_schema.table_constraints
                           WHERE table_schema = %s
                             AND table_name IN (
                                 'delivery_routes', 'delivery_route_stops',
                                 'delivery_notifications',
                                 'delivery_push_subscriptions', 'projects',
                                 'project_user_shares', 'project_tasks',
                                 'project_task_notes', 'categories', 'subcategories',
                                 'units', 'hsn', 'suppliers', 'products',
                                 'stock_movements', 'purchase_orders',
                                 'purchase_order_items', 'purchase_invoices',
                                 'purchase_invoice_items', 'purchase_grn',
                                 'purchase_grn_items', 'purchase_returns',
                                 'purchase_return_items', 'sales_invoices',
                                 'sales_invoice_items', 'sales_challans',
                                 'sales_challan_items', 'sales_returns',
                                 'sales_return_items', 'sales_payments',
                                 'purchase_payments'
                             )""",
                        (empty_schema,),
                    ).fetchall()
                }
                self.assertIn("delivery_route_stops_route_id_stop_order_key", constraints)
                self.assertIn("delivery_push_subscriptions_endpoint_key", constraints)
                self.assertIn("project_user_shares_pkey", constraints)
                self.assertIn("projects_pkey", constraints)
                self.assertTrue(
                    {
                        "projects_owner_id_fkey",
                        "projects_created_by_fkey",
                        "project_user_shares_project_id_fkey",
                        "project_user_shares_user_id_fkey",
                        "project_user_shares_shared_by_fkey",
                        "project_tasks_project_id_fkey",
                        "project_tasks_assigned_to_fkey",
                        "project_tasks_created_by_fkey",
                        "project_task_notes_task_id_fkey",
                        "project_task_notes_created_by_fkey",
                    }.issubset(constraints)
                )
                self.assertTrue(
                    {
                        "categories_pkey",
                        "categories_category_name_key",
                        "subcategories_category_id_fkey",
                        "products_category_id_fkey",
                        "products_subcategory_id_fkey",
                        "products_unit_id_fkey",
                        "products_hsn_id_fkey",
                        "products_supplier_id_fkey",
                        "stock_movements_product_id_fkey",
                        "purchase_order_items_order_id_fkey",
                        "purchase_order_items_product_id_fkey",
                        "purchase_invoices_supplier_id_fkey",
                        "purchase_grn_invoice_id_fkey",
                        "purchase_return_items_return_id_fkey",
                        "sales_invoices_customer_id_fkey",
                        "sales_invoice_items_invoice_id_fkey",
                        "sales_orders_customer_id_fkey",
                        "sales_challans_customer_id_fkey",
                        "sales_challan_items_challan_id_fkey",
                        "sales_returns_invoice_id_fkey",
                        "sales_return_items_return_id_fkey",
                        "sales_payments_invoice_id_fkey",
                        "sales_payments_customer_id_fkey",
                        "purchase_payments_invoice_id_fkey",
                        "purchase_payments_supplier_id_fkey",
                    }.issubset(constraints)
                )
                default_product_id = connection.execute(
                    "INSERT INTO products (product_name) VALUES ('defaults') RETURNING id"
                ).fetchone()[0]
                defaults = connection.execute(
                    """SELECT tax_rate, sale_price, purchase_price
                       FROM products WHERE id = %s""",
                    (default_product_id,),
                ).fetchone()
                self.assertEqual(tuple(defaults), (18.0, 0.0, 0.0))
                movement_defaults = connection.execute(
                    """INSERT INTO stock_movements
                       (product_id, movement_type, quantity)
                       VALUES (%s, %s, %s) RETURNING rate, amount""",
                    (default_product_id, "IN", 1),
                ).fetchone()
                self.assertEqual(tuple(movement_defaults), (0.0, 0.0))
                customer_id = connection.execute(
                    "INSERT INTO customers (name) VALUES ('default customer') RETURNING id"
                ).fetchone()[0]
                purchase_order_id = connection.execute(
                    """INSERT INTO purchase_orders (order_no, order_date)
                       VALUES (%s, %s) RETURNING id, status, total_amount""",
                    ("PO-DEFAULT", date(2026, 10, 19)),
                ).fetchone()
                self.assertEqual(tuple(purchase_order_id[1:]), ("pending", 0.0))
                sales_order_id = connection.execute(
                    """INSERT INTO sales_orders (order_no, order_date, customer_id)
                       VALUES (%s, %s, %s) RETURNING id, status, total_amount""",
                    ("SO-DEFAULT", date(2026, 10, 19), customer_id),
                ).fetchone()
                self.assertEqual(tuple(sales_order_id[1:]), ("open", 0.0))
                billed_quantity = connection.execute(
                    """INSERT INTO sales_order_items (order_id, product_id)
                       VALUES (%s, %s) RETURNING billed_qty""",
                    (sales_order_id[0], default_product_id),
                ).fetchone()[0]
                self.assertEqual(billed_quantity, 0.0)
                payment_defaults = connection.execute(
                    """SELECT column_default FROM information_schema.columns
                       WHERE table_schema = %s
                         AND table_name = 'sales_payments'
                         AND column_name = 'payment_mode'""",
                    (empty_schema,),
                ).fetchone()[0]
                self.assertEqual(payment_defaults, "'cash'::text")
                payment_required_columns = {
                    row[0]
                    for row in connection.execute(
                        """SELECT column_name
                           FROM information_schema.columns
                           WHERE table_schema = %s
                             AND table_name = 'sales_payments'
                             AND is_nullable = 'NO'""",
                        (empty_schema,),
                    ).fetchall()
                }
                self.assertTrue(
                    {"id", "customer_id", "payment_date", "amount"}.issubset(
                        payment_required_columns
                    )
                )
                purchase_payment_defaults = connection.execute(
                    """SELECT column_default FROM information_schema.columns
                       WHERE table_schema = %s
                         AND table_name = 'purchase_payments'
                         AND column_name = 'payment_mode'""",
                    (empty_schema,),
                ).fetchone()[0]
                self.assertEqual(purchase_payment_defaults, "'cash'::text")
        finally:
            with self.psycopg.connect(self.test_database_url, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(empty_schema)
                    )
                )

    def test_applied_migration_checksum_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for source in MIGRATIONS_DIR.glob("*.sql"):
                copied = Path(temp_dir) / source.name
                content = source.read_bytes()
                if source.name == "0001_hiring_application_slice.sql":
                    content += b"\n-- altered migration\n"
                copied.write_bytes(content)
            with self.psycopg.connect(self.scoped_database_url) as connection:
                with self.assertRaises(MigrationChecksumError):
                    apply_postgres_migrations(
                        connection, temp_dir, schema=self.schema
                    )

    def test_delivery_route_workflow_and_authorization(self):
        import modules.delivery as delivery_module
        import modules.projects as projects_module
        from flask import session

        subscription = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/staging-example",
            "keys": {"p256dh": "test-p256dh", "auth": "test-auth"},
        }
        self.login_as(self.driver_id, "test_driver", "driver", "delivery")
        self.assertEqual(
            self.client.post("/delivery/push/subscriptions", json=subscription).status_code,
            200,
        )

        self.login_as(self.dispatcher_id, "test_dispatcher", "dispatcher", "delivery")
        with patch.dict(
            os.environ,
            {
                "DELIVERY_VAPID_PUBLIC_KEY": "test-public-key",
                "DELIVERY_VAPID_PRIVATE_KEY": "test-private-key",
                "DELIVERY_VAPID_SUBJECT": "mailto:test@example.test",
            },
        ), patch.object(delivery_module, "webpush") as webpush:
            response = self.create_delivery_route()

        self.assertEqual(response.status_code, 302)
        route_id = int(response.headers["Location"].rsplit("/", 1)[1])
        webpush.assert_called_once()

        dispatcher_home = self.client.get("/delivery")
        self.assertEqual(dispatcher_home.status_code, 200)
        self.assertIn(b"Driver: Test Driver", dispatcher_home.data)
        self.assertIn(b"2 customers", dispatcher_home.data)

        connection = database.get_connection()
        try:
            route = connection.execute(
                "SELECT driver_id, status FROM delivery_routes WHERE id = %s",
                (route_id,),
            ).fetchone()
            stops = connection.execute(
                """SELECT stop_order, customer_name FROM delivery_route_stops
                   WHERE route_id = %s ORDER BY stop_order""",
                (route_id,),
            ).fetchall()
            notification = connection.execute(
                """SELECT user_id, message, read_at FROM delivery_notifications
                   WHERE route_id = %s""",
                (route_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(tuple(route), (self.driver_id, "assigned"))
        self.assertEqual(
            [tuple(stop) for stop in stops],
            [(1, "First stop"), (2, "Second stop")],
        )
        self.assertEqual(notification["user_id"], self.driver_id)
        self.assertIsNone(notification["read_at"])

        self.login_as(self.driver_id, "test_driver", "driver", "delivery")
        driver_home = self.client.get("/delivery")
        self.assertEqual(driver_home.status_code, 200)
        self.assertIn(b"2 customers", driver_home.data)
        self.assertEqual(
            self.client.get(f"/delivery/routes/{route_id}").status_code, 200
        )
        connection = database.get_connection()
        try:
            read_at = connection.execute(
                "SELECT read_at FROM delivery_notifications WHERE route_id = %s",
                (route_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertIsNotNone(read_at)

        self.login_as(
            self.other_driver_id, "test_other_driver", "driver", "delivery"
        )
        self.assertEqual(
            self.client.get(f"/delivery/routes/{route_id}").status_code, 404
        )
        self.assertEqual(
            self.client.post(f"/delivery/routes/{route_id}/complete").status_code,
            404,
        )
        connection = database.get_connection()
        try:
            status = connection.execute(
                "SELECT status FROM delivery_routes WHERE id = %s", (route_id,)
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(status, "assigned")

        self.login_as(self.driver_id, "test_driver", "driver", "delivery")
        self.assertEqual(
            self.client.post(f"/delivery/routes/{route_id}/complete").status_code,
            302,
        )
        connection = database.get_connection()
        try:
            status = connection.execute(
                "SELECT status FROM delivery_routes WHERE id = %s", (route_id,)
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(status, "completed")

        connection = database.get_connection()
        try:
            connection.execute(
                """INSERT INTO projects (name, created_by)
                   VALUES (%s, %s) RETURNING id""",
                ("zeta owned", self.driver_id),
            ).fetchone()[0]
            shared_alpha_id = connection.execute(
                """INSERT INTO projects (name, created_by)
                   VALUES (%s, %s) RETURNING id""",
                ("Alpha shared", self.dispatcher_id),
            ).fetchone()[0]
            shared_beta_id = connection.execute(
                """INSERT INTO projects (name, created_by)
                   VALUES (%s, %s) RETURNING id""",
                ("beta shared", self.dispatcher_id),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO projects (name, created_by)
                   VALUES (%s, %s)""",
                ("Hidden private", self.other_driver_id),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO project_user_shares
                       (project_id, user_id, shared_by)
                       VALUES (%s, %s, %s)""",
                    [
                        (shared_alpha_id, self.driver_id, self.dispatcher_id),
                        (shared_beta_id, self.driver_id, self.dispatcher_id),
                    ],
                )
            connection.commit()
        finally:
            connection.close()

        with self.app.test_request_context("/delivery"):
            session.update(user_id=self.driver_id, role="driver")
            sidebar = projects_module.inject_sidebar_projects()["sidebar_projects"]
        self.assertEqual(
            [project["name"] for project in sidebar],
            ["Alpha shared", "beta shared", "zeta owned"],
        )
        with self.app.test_request_context("/delivery"):
            session.update(user_id=self.driver_id, role="admin")
            admin_sidebar = projects_module.inject_sidebar_projects()[
                "sidebar_projects"
            ]
        self.assertEqual(
            [project["name"] for project in admin_sidebar],
            ["Alpha shared", "beta shared", "Hidden private", "zeta owned"],
        )

    def test_projects_visibility_crud_tasks_notes_and_delivery_rendering(self):
        self.login_as(
            self.project_owner_id,
            "test_project_owner",
            "user",
            "projects",
        )
        invalid = self.create_project("Invalid project", due_date="not-a-date")
        self.assertEqual(invalid.status_code, 200)
        self.assertIn(b"Enter valid project dates.", invalid.data)
        connection = database.get_connection()
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
                0,
            )
        finally:
            connection.close()

        created = self.create_project("zeta project")
        self.assertEqual(created.status_code, 302)
        zeta_id = int(created.headers["Location"].rsplit("/", 1)[1])
        alpha = self.create_project("Alpha project")
        self.assertEqual(alpha.status_code, 302)
        alpha_id = int(alpha.headers["Location"].rsplit("/", 1)[1])
        detail = self.client.get(f"/projects/{zeta_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Created ", detail.data)

        updated = self.client.post(
            f"/projects/{zeta_id}/update",
            json={
                "name": "zeta project revised",
                "description": "Updated details",
                "client": "Test client",
                "stage": "planning",
                "priority": "high",
                "start_date": "2026-10-02",
                "due_date": "2026-12-01",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertTrue(updated.json["saved"])

        private_project = self.create_project("Private project")
        self.assertEqual(private_project.status_code, 302)
        private_id = int(private_project.headers["Location"].rsplit("/", 1)[1])

        self.login_as(
            self.project_peer_id,
            "test_project_peer",
            "user",
            "projects",
        )
        board = self.client.get("/projects")
        self.assertNotIn(b"Private project", board.data)
        self.assertNotIn(b"zeta project revised", board.data)
        self.assertEqual(self.client.get(f"/projects/{private_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/projects/{zeta_id}").status_code, 404)
        self.assertEqual(
            self.client.post(
                f"/projects/{zeta_id}/update",
                data={"name": "Unauthorized change"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(f"/projects/{zeta_id}/delete").status_code, 404
        )
        self.login_as(
            self.project_external_id,
            "test_project_external",
            "external",
            "projects",
        )
        self.assertEqual(self.client.get(f"/projects/{private_id}").status_code, 404)

        self.login_as(
            self.project_owner_id,
            "test_project_owner",
            "user",
            "projects",
        )
        for project_id in (zeta_id, alpha_id):
            self.assertEqual(
                self.client.post(
                    f"/projects/{project_id}/share",
                    data={"user_ids": [str(self.project_peer_id)]},
                ).status_code,
                302,
            )

        self.login_as(
            self.project_peer_id,
            "test_project_peer",
            "user",
            "projects",
        )
        peer_detail = self.client.get(f"/projects/{zeta_id}")
        self.assertEqual(peer_detail.status_code, 200)
        alpha_position = peer_detail.data.index(b">Alpha project</a>")
        zeta_position = peer_detail.data.index(b">zeta project revised</a>")
        self.assertLess(alpha_position, zeta_position)
        self.assertEqual(self.client.get(f"/projects/{private_id}").status_code, 404)

        task_response = self.client.post(
            f"/projects/{zeta_id}/tasks",
            data={
                "title": "Project integration task",
                "details": "Initial task details",
                "due_date": "2026-11-10",
                "stage": "awaiting_quotation",
            },
        )
        self.assertEqual(task_response.status_code, 302)
        connection = database.get_connection()
        try:
            task = connection.execute(
                "SELECT id, stage FROM project_tasks WHERE project_id = %s",
                (zeta_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(task["stage"], "awaiting_quotation")
        task_id = task["id"]
        updated_task = self.client.post(
            f"/projects/{zeta_id}/items/{task_id}",
            data={
                "title": "Updated project task",
                "details": "Updated task details",
                "stage": "completed",
                "due_date": "2026-11-12",
            },
        )
        self.assertEqual(updated_task.status_code, 302)
        note_response = self.client.post(
            f"/projects/{zeta_id}/items/{task_id}/notes",
            data={"note": "A scoped project note"},
        )
        self.assertEqual(note_response.status_code, 302)
        task_detail = self.client.get(f"/projects/{zeta_id}/items/{task_id}")
        self.assertEqual(task_detail.status_code, 200)
        self.assertIn(b"A scoped project note", task_detail.data)

        self.login_as(
            self.projects_admin_id,
            "test_projects_admin",
            "admin",
            "projects",
        )
        admin_board = self.client.get("/projects")
        self.assertIn(b"Private project", admin_board.data)
        self.assertIn(b"zeta project revised", admin_board.data)

        self.login_as(
            self.project_owner_id,
            "test_project_owner",
            "user",
            "projects",
        )
        deleted_task = self.client.post(
            f"/projects/{zeta_id}/items/{task_id}/delete"
        )
        self.assertEqual(deleted_task.status_code, 302)
        connection = database.get_connection()
        try:
            task_count = connection.execute(
                "SELECT COUNT(*) FROM project_tasks WHERE id = %s", (task_id,)
            ).fetchone()[0]
            note_count = connection.execute(
                "SELECT COUNT(*) FROM project_task_notes WHERE task_id = %s",
                (task_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual((task_count, note_count), (0, 0))
        self.assertEqual(
            self.client.post(f"/projects/{alpha_id}/delete").status_code, 302
        )
        self.assertEqual(
            self.client.post(f"/projects/{zeta_id}/delete").status_code, 302
        )
        connection = database.get_connection()
        try:
            remaining = connection.execute(
                """SELECT
                       (SELECT COUNT(*) FROM projects WHERE id = %s),
                       (SELECT COUNT(*) FROM project_user_shares WHERE project_id = %s),
                       (SELECT COUNT(*) FROM project_tasks WHERE project_id = %s)""",
                (zeta_id, zeta_id, zeta_id),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(tuple(remaining), (0, 0, 0))
        self.assertEqual(self.client.get("/delivery").status_code, 302)
        self.login_as(self.dispatcher_id, "test_dispatcher", "dispatcher", "delivery")
        delivery_page = self.client.get("/delivery")
        self.assertEqual(delivery_page.status_code, 200)

    def test_project_write_failure_rolls_back_and_pool_connection_is_reused(self):
        connection = database.get_connection()
        try:
            connection.execute(
                """CREATE FUNCTION reject_project_note() RETURNS trigger
                   LANGUAGE plpgsql AS $$
                   BEGIN
                       RAISE EXCEPTION 'rejected project note';
                   END;
                   $$"""
            )
            connection.execute(
                """CREATE TRIGGER reject_project_note
                   BEFORE INSERT ON project_task_notes
                   FOR EACH ROW EXECUTE FUNCTION reject_project_note()"""
            )
            project_id = connection.execute(
                """INSERT INTO projects (name, created_by)
                   VALUES (%s, %s) RETURNING id""",
                ("Connection cleanup project", self.project_owner_id),
            ).fetchone()[0]
            task_id = connection.execute(
                """INSERT INTO project_tasks (project_id, title, created_by)
                   VALUES (%s, %s, %s) RETURNING id""",
                (project_id, "Connection cleanup task", self.project_owner_id),
            ).fetchone()[0]
            connection.commit()
        finally:
            connection.close()

        self.login_as(
            self.project_owner_id,
            "test_project_owner",
            "user",
            "projects",
        )
        previous_propagate_exceptions = self.app.config.get(
            "PROPAGATE_EXCEPTIONS"
        )
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        try:
            response = self.client.post(
                f"/projects/{project_id}/items/{task_id}/notes",
                data={"note": "This insert is rejected."},
            )
        finally:
            self.app.config["PROPAGATE_EXCEPTIONS"] = previous_propagate_exceptions
        self.assertEqual(response.status_code, 500)

        connection = database.get_connection()
        try:
            note_count = connection.execute(
                "SELECT COUNT(*) FROM project_task_notes WHERE task_id = %s",
                (task_id,),
            ).fetchone()[0]
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            connection.execute(
                "DROP TRIGGER reject_project_note ON project_task_notes"
            )
            connection.execute("DROP FUNCTION reject_project_note()")
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(note_count, 0)

    def test_invalid_delivery_route_does_not_create_partial_records(self):
        self.login_as(self.dispatcher_id, "test_dispatcher", "dispatcher", "delivery")
        response = self.create_delivery_route(
            route_date="not-a-date",
            **{
                "stops-0-customer_name": "First stop",
                "stops-0-address": "1 Example Road",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Choose a valid route date.", response.data)
        connection = database.get_connection()
        try:
            counts = [
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "delivery_routes",
                    "delivery_route_stops",
                    "delivery_notifications",
                )
            ]
        finally:
            connection.close()
        self.assertEqual(counts, [0, 0, 0])

    def test_delivery_push_subscription_upsert_and_delete(self):
        self.login_as(self.driver_id, "test_driver", "driver", "delivery")
        endpoint = "https://fcm.googleapis.com/fcm/send/upsert-example"
        first = {
            "endpoint": endpoint,
            "keys": {"p256dh": "first-p256dh", "auth": "first-auth"},
        }
        second = {
            "endpoint": endpoint,
            "keys": {"p256dh": "second-p256dh", "auth": "second-auth"},
        }
        self.assertEqual(
            self.client.post("/delivery/push/subscriptions", json=first).status_code,
            200,
        )
        self.assertEqual(
            self.client.post("/delivery/push/subscriptions", json=second).status_code,
            200,
        )
        connection = database.get_connection()
        try:
            row = connection.execute(
                """SELECT user_id, p256dh, auth FROM delivery_push_subscriptions
                   WHERE endpoint = %s""",
                (endpoint,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            tuple(row), (self.driver_id, "second-p256dh", "second-auth")
        )
        self.assertEqual(
            self.client.delete(
                "/delivery/push/subscriptions", json={"endpoint": endpoint}
            ).status_code,
            200,
        )
        connection = database.get_connection()
        try:
            count = connection.execute(
                """SELECT COUNT(*) FROM delivery_push_subscriptions
                   WHERE endpoint = %s""",
                (endpoint,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_failed_delivery_insert_rolls_back_and_pool_connection_is_reusable(self):
        connection = database.get_connection()
        try:
            connection.execute(
                """CREATE FUNCTION reject_delivery_stop() RETURNS trigger
                   LANGUAGE plpgsql AS $$
                   BEGIN
                       RAISE EXCEPTION 'rejected delivery stop';
                   END;
                   $$"""
            )
            connection.execute(
                """CREATE TRIGGER reject_delivery_stop
                   BEFORE INSERT ON delivery_route_stops
                   FOR EACH ROW EXECUTE FUNCTION reject_delivery_stop()"""
            )
            connection.commit()
        finally:
            connection.close()

        self.login_as(self.dispatcher_id, "test_dispatcher", "dispatcher", "delivery")
        response = self.create_delivery_route()
        self.assertEqual(response.status_code, 503)
        connection = database.get_connection()
        try:
            counts = [
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "delivery_routes",
                    "delivery_route_stops",
                    "delivery_notifications",
                )
            ]
            connection.execute(
                "DROP TRIGGER reject_delivery_stop ON delivery_route_stops"
            )
            connection.execute("DROP FUNCTION reject_delivery_stop()")
            connection.commit()
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        finally:
            connection.close()
        self.assertEqual(counts, [0, 0, 0])
        self.assertEqual(self.create_delivery_route().status_code, 302)

    def test_careers_lists_open_jobs(self):
        response = self.client.get("/careers")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PostgreSQL Hiring Test", response.data)

    def test_non_row_command_then_select_uses_compatible_row_factory(self):
        connection = database.get_connection()
        try:
            connection.execute("TRUNCATE TABLE users RESTART IDENTITY CASCADE")
            row = connection.execute("SELECT 7 AS id, 'seven' AS label").fetchone()
        finally:
            connection.close()

        self.assertEqual(row[0], 7)
        self.assertEqual(row["ID"], 7)
        self.assertEqual(row["label"], "seven")
        self.assertEqual(row.keys(), ["id", "label"])
        self.assertEqual(tuple(row), (7, "seven"))

    def test_valid_application_persists_with_expected_values(self):
        response = self.submit_application()
        self.assertEqual(response.status_code, 302)

        connection = database.get_connection()
        try:
            candidate = connection.execute(
                """SELECT full_name, email, position, source, status, job_id, created_by
                   FROM hiring_candidates"""
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            tuple(candidate),
            (
                "PostgreSQL Applicant",
                "candidate@example.test",
                "PostgreSQL Hiring Test",
                "Careers page",
                "new",
                self.job_id,
                None,
            ),
        )

    def test_missing_closed_and_invalid_job_ids_are_rejected(self):
        self.assertEqual(
            self.client.get("/careers/not-a-number/apply").status_code,
            404,
        )
        self.assertEqual(self.submit_application(job_id=999999).status_code, 404)

        connection = database.get_connection()
        try:
            connection.execute(
                "UPDATE hiring_jobs SET status = 'closed' WHERE id = %s",
                (self.job_id,),
            )
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(self.submit_application().status_code, 404)

    def test_validation_failure_does_not_persist_candidate(self):
        response = self.submit_application(full_name="")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Enter your name", response.data)
        connection = database.get_connection()
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM hiring_candidates"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_failed_insert_rolls_back_and_pooled_connection_is_reusable(self):
        connection = database.get_connection()
        try:
            connection.execute(
                """CREATE FUNCTION reject_test_candidate() RETURNS trigger
                   LANGUAGE plpgsql AS $$
                   BEGIN
                       RAISE EXCEPTION 'rejected candidate %', NEW.email;
                   END;
                   $$"""
            )
            connection.execute(
                """CREATE TRIGGER reject_test_candidate
                   BEFORE INSERT ON hiring_candidates
                   FOR EACH ROW EXECUTE FUNCTION reject_test_candidate()"""
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertLogs("modules.hiring", level="WARNING") as captured:
            response = self.submit_application(email="private-candidate@example.test")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"private-candidate@example.test", response.data)
        self.assertNotIn("private-candidate@example.test", "\n".join(captured.output))
        self.assertNotIn(self.test_database_url, "\n".join(captured.output))

        connection = database.get_connection()
        try:
            connection.execute(
                "DROP TRIGGER reject_test_candidate ON hiring_candidates"
            )
            connection.execute("DROP FUNCTION reject_test_candidate()")
            connection.commit()
        finally:
            connection.close()

        self.assertEqual(self.submit_application().status_code, 302)
        self.assertEqual(self.client.get("/careers").status_code, 200)

    def test_stock_read_routes_filter_dates_and_restrict_product_deletion(self):
        import modules.stock as stock_module

        connection = database.get_connection()
        try:
            product_id = self.create_stock_product(connection)
            stock_module.add_stock_movement(
                connection.cursor(),
                product_id,
                "IN",
                10,
                "opening",
                None,
                "STOCK-IN-1",
                date(2026, 10, 1),
                5,
            )
            stock_module.add_stock_movement(
                connection.cursor(),
                product_id,
                "OUT",
                3,
                "grn",
                None,
                "STOCK-OUT-1",
                date(2026, 10, 2),
                5,
            )
            stock_module.add_stock_movement(
                connection.cursor(),
                product_id,
                "IN",
                8,
                "opening",
                None,
                "STOCK-IN-2",
                date(2026, 10, 3),
                4,
            )
            connection.execute(
                "INSERT INTO products (product_name) VALUES (%s)",
                ("widget Beta",),
            )
            connection.commit()
        finally:
            connection.close()

        self.login_as(
            self.stock_admin_id, "test_stock_admin", "admin", "distribution"
        )
        listing = self.client.get("/stock?search=wIdGeT")
        self.assertEqual(listing.status_code, 200)
        self.assertIn(b"Widget Alpha", listing.data)
        self.assertIn(b"Stock Components", listing.data)
        self.assertIn(b"15.0", listing.data)
        self.assertLess(
            listing.data.index(b"Widget Alpha"),
            listing.data.index(b"widget Beta"),
        )

        ledger = self.client.get(
            f"/stock/ledger/{product_id}?from_date=2026-10-02"
            "&to_date=2026-10-03&sort=date_desc"
        )
        self.assertEqual(ledger.status_code, 200)
        self.assertIn(b"STOCK-IN-2", ledger.data)
        self.assertIn(b"STOCK-OUT-1", ledger.data)
        self.assertNotIn(b"STOCK-IN-1", ledger.data)
        amount_sorted = self.client.get(
            f"/stock/ledger/{product_id}?sort=amount_desc"
        )
        self.assertLess(
            amount_sorted.data.index(b"STOCK-IN-1"),
            amount_sorted.data.index(b"STOCK-IN-2"),
        )
        self.assertLess(
            amount_sorted.data.index(b"STOCK-IN-2"),
            amount_sorted.data.index(b"STOCK-OUT-1"),
        )
        self.assertEqual(
            self.client.get(
                f"/stock/ledger/{product_id}?from_date=not-a-date"
            ).status_code,
            400,
        )
        self.assertEqual(self.client.get("/stock/ledger/999999").status_code, 404)

        product = next(
            item
            for item in self.client.get("/api/products-with-stock").json["products"]
            if item["id"] == product_id
        )
        self.assertEqual(product["stock"], 15.0)
        self.assertEqual(product["unit"], "Each")
        self.assertEqual(product["hsn"], "9988")

        self.login_as(
            self.projects_admin_id, "test_projects_admin", "admin", "projects"
        )
        self.assertEqual(self.client.get("/stock").status_code, 302)

        connection = database.get_connection()
        try:
            with self.assertRaises(self.psycopg.errors.ForeignKeyViolation):
                connection.execute("DELETE FROM products WHERE id = %s", (product_id,))
            connection.rollback()
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM products WHERE id = %s", (product_id,)
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_stock_failed_insert_rolls_back_and_pool_connection_is_reusable(self):
        connection = database.get_connection()
        try:
            product_id = self.create_stock_product(connection)
            connection.commit()
        finally:
            connection.close()

        connection = database.get_connection()
        try:
            with self.assertRaises(self.psycopg.errors.ForeignKeyViolation):
                connection.execute(
                    """INSERT INTO stock_movements
                       (product_id, movement_type, quantity)
                       VALUES (%s, %s, %s)""",
                    (product_id + 999999, "IN", 1),
                )
        finally:
            connection.close()

        connection = database.get_connection()
        try:
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM stock_movements"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_sales_and_purchase_document_workflows_post_stock_atomically(self):
        connection = database.get_connection()
        try:
            customer_id, transporter_id, supplier_id, product_id = (
                self.create_distribution_masters(connection)
            )
            connection.commit()
        finally:
            connection.close()

        self.login_as(
            self.stock_admin_id, "test_stock_admin", "admin", "distribution"
        )
        line = {
            "product_id[]": [str(product_id)],
            "unit_id[]": ["1"],
            "hsn_id[]": ["1"],
            "quantity[]": ["2"],
            "rate[]": ["10"],
            "discount[]": ["10"],
            "tax_rate[]": ["5"],
        }
        customer_fields = {
            "customer_id": str(customer_id),
            "transporter_id": str(transporter_id),
            "po_no": "CUSTOMER-PO",
            "po_date": "2026-10-10",
        }
        self.assertEqual(
            self.client.post(
                "/sales-challans/add",
                data={
                    **customer_fields,
                    "challan_date": "2026-10-10",
                    **line,
                },
            ).status_code,
            302,
        )

        connection = database.get_connection()
        try:
            challan = connection.execute(
                "SELECT id, total_amount FROM sales_challans"
            ).fetchone()
            challan_item = connection.execute(
                "SELECT id, amount, billed_qty FROM sales_challan_items"
            ).fetchone()
            movement = connection.execute(
                """SELECT movement_type, quantity, amount, source_id
                   FROM stock_movements WHERE source_type = 'challan'"""
            ).fetchone()
        finally:
            connection.close()
        self.assertGreater(challan["id"], 0)
        self.assertAlmostEqual(challan["total_amount"], 18.9)
        self.assertAlmostEqual(challan_item["amount"], 18.9)
        self.assertEqual(tuple(movement), ("OUT", 2.0, 20.0, challan["id"]))

        invoice_data = {
            **customer_fields,
            "invoice_date": "2026-10-11",
            "cr_days": "0",
            "due_date": "2026-10-11",
            **line,
            "source_type[]": ["challan"],
            "source_item_id[]": [str(challan_item["id"])],
        }
        self.assertEqual(
            self.client.post("/sales-invoices/add", data=invoice_data).status_code,
            302,
        )
        connection = database.get_connection()
        try:
            invoice = connection.execute(
                "SELECT id, total_amount, invoice_date FROM sales_invoices"
            ).fetchone()
            billed_qty = connection.execute(
                "SELECT billed_qty FROM sales_challan_items WHERE id = %s",
                (challan_item["id"],),
            ).fetchone()[0]
            invoice_movements = connection.execute(
                "SELECT COUNT(*) FROM stock_movements WHERE source_type = 'sales_invoice'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertGreater(invoice["id"], 0)
        self.assertEqual(invoice["invoice_date"], date(2026, 10, 11))
        self.assertAlmostEqual(invoice["total_amount"], 18.9)
        self.assertEqual(billed_qty, 2.0)
        self.assertEqual(invoice_movements, 0)

        self.assertEqual(
            self.client.post(
                "/sales-returns/add",
                data={
                    **customer_fields,
                    "invoice_id": str(invoice["id"]),
                    "return_date": "2026-10-12",
                    "reason": "Damaged",
                    **line,
                },
            ).status_code,
            302,
        )
        supplier_fields = {
            "supplier_id": str(supplier_id),
            "transporter_id": str(transporter_id),
        }
        self.assertEqual(
            self.client.post(
                "/purchase-invoices/add",
                data={
                    **supplier_fields,
                    "invoice_date": "2026-10-13",
                    "sup_invoice_no": "SUP-100",
                    "sup_invoice_date": "2026-10-12",
                    "cr_days": "0",
                    "due_date": "2026-10-13",
                    **line,
                },
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                "/purchase-grn/add",
                data={
                    **supplier_fields,
                    "grn_date": "2026-10-14",
                    **line,
                },
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                "/purchase-returns/add",
                data={
                    **supplier_fields,
                    "return_date": "2026-10-15",
                    "reason": "Excess",
                    **line,
                },
            ).status_code,
            302,
        )

        connection = database.get_connection()
        try:
            movements = connection.execute(
                """SELECT movement_type, source_type, movement_date
                   FROM stock_movements ORDER BY id"""
            ).fetchall()
            purchase_invoice = connection.execute(
                "SELECT total_amount, invoice_date FROM purchase_invoices"
            ).fetchone()
            grn = connection.execute(
                "SELECT id, total_amount, grn_date FROM purchase_grn"
            ).fetchone()
            grn_item = connection.execute(
                "SELECT amount FROM purchase_grn_items"
            ).fetchone()
            return_amount = connection.execute(
                "SELECT total_amount FROM purchase_returns"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(
            [(row["movement_type"], row["source_type"]) for row in movements],
            [
                ("OUT", "challan"),
                ("IN", "sales_return"),
                ("IN", "grn"),
                ("OUT", "purchase_return"),
            ],
        )
        self.assertEqual(movements[0]["movement_date"], date(2026, 10, 10))
        self.assertAlmostEqual(purchase_invoice["total_amount"], 18.9)
        self.assertEqual(purchase_invoice["invoice_date"], date(2026, 10, 13))
        self.assertAlmostEqual(grn["total_amount"], 18.9)
        self.assertEqual(grn["grn_date"], date(2026, 10, 14))
        self.assertAlmostEqual(grn_item["amount"], 18.9)
        self.assertAlmostEqual(return_amount, 18.9)
        for path in (
            "/sales-invoices",
            "/sales-challans",
            "/sales-returns",
            "/sales-quotations",
            "/sales-orders",
            "/proforma-invoices",
            f"/api/pending-items/{customer_id}/{product_id}",
            "/purchase-invoices",
            "/purchase-orders",
            "/purchase-grn",
            "/purchase-returns",
            f"/api/supplier-orders/{supplier_id}",
            f"/api/supplier-grns/{supplier_id}",
        ):
            self.assertEqual(self.client.get(path).status_code, 200)

    def test_concurrent_document_number_allocations_are_unique(self):
        from modules.document_database import (
            managed_document_connection,
            next_document_number,
        )

        get_connection = managed_document_connection(database.get_connection)
        scope = "concurrency-" + uuid4().hex

        def allocate(_):
            connection = get_connection()
            try:
                number = next_document_number(
                    connection.cursor(),
                    "sales_invoice",
                    scope,
                    "SI-20261020-",
                    "SI-",
                )
                connection.commit()
                return number
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=5) as executor:
            numbers = list(executor.map(allocate, range(10)))

        self.assertEqual(len(numbers), len(set(numbers)))
        suffixes = sorted(int(number.rsplit("-", 1)[1]) for number in numbers)
        self.assertEqual(suffixes, list(range(suffixes[0], suffixes[0] + 10)))

    def test_repeated_grn_requests_have_no_duplicate_line_movements(self):
        connection = database.get_connection()
        try:
            _, transporter_id, supplier_id, product_id = (
                self.create_distribution_masters(connection)
            )
            connection.commit()
        finally:
            connection.close()
        self.login_as(
            self.stock_admin_id, "test_stock_admin", "admin", "distribution"
        )
        data = {
            "supplier_id": str(supplier_id),
            "transporter_id": str(transporter_id),
            "grn_date": "2026-10-16",
            "product_id[]": [str(product_id)],
            "unit_id[]": ["1"],
            "hsn_id[]": ["1"],
            "quantity[]": ["3"],
            "rate[]": ["2"],
            "discount[]": ["0"],
            "tax_rate[]": ["18"],
        }
        self.assertEqual(self.client.post("/purchase-grn/add", data=data).status_code, 302)
        self.assertEqual(self.client.post("/purchase-grn/add", data=data).status_code, 302)
        connection = database.get_connection()
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM purchase_grn").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM purchase_grn_items"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM stock_movements WHERE source_type = 'grn'"
                ).fetchone()[0],
                2,
            )
        finally:
            connection.close()

    def test_failed_sales_line_rolls_back_document_and_stock_then_reuses_pool(self):
        connection = database.get_connection()
        try:
            customer_id, transporter_id, _, product_id = (
                self.create_distribution_masters(connection)
            )
            connection.execute(
                """CREATE FUNCTION reject_test_sales_line() RETURNS trigger
                   LANGUAGE plpgsql AS $$
                   BEGIN
                       RAISE EXCEPTION 'rejected sales line';
                   END;
                   $$"""
            )
            connection.execute(
                """CREATE TRIGGER reject_test_sales_line
                   BEFORE INSERT ON sales_invoice_items
                   FOR EACH ROW EXECUTE FUNCTION reject_test_sales_line()"""
            )
            connection.commit()
        finally:
            connection.close()

        self.login_as(
            self.stock_admin_id, "test_stock_admin", "admin", "distribution"
        )
        with self.assertRaises(self.psycopg.Error):
            self.client.post(
                "/sales-invoices/add",
                data={
                    "customer_id": str(customer_id),
                    "transporter_id": str(transporter_id),
                    "invoice_date": "2026-10-17",
                    "product_id[]": [str(product_id)],
                    "unit_id[]": ["1"],
                    "hsn_id[]": ["1"],
                    "quantity[]": ["2"],
                    "rate[]": ["5"],
                    "discount[]": ["0"],
                    "tax_rate[]": ["18"],
                },
            )

        connection = database.get_connection()
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM sales_invoices").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sales_invoice_items"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0],
                0,
            )
            connection.execute(
                "DROP TRIGGER reject_test_sales_line ON sales_invoice_items"
            )
            connection.execute("DROP FUNCTION reject_test_sales_line()")
            connection.commit()
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        finally:
            connection.close()

    def test_payment_workflows_are_system_scoped_and_reuse_pool_after_failure(self):
        connection = database.get_connection()
        try:
            customer_id, _, supplier_id, _ = self.create_distribution_masters(
                connection
            )
            sales_invoice_id = connection.execute(
                """INSERT INTO sales_invoices
                   (invoice_no, invoice_date, customer_id, due_date, total_amount)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (
                    "SI-PAYMENT-TEST",
                    date(2026, 10, 18),
                    customer_id,
                    date(2026, 11, 17),
                    100,
                ),
            ).fetchone()[0]
            purchase_invoice_id = connection.execute(
                """INSERT INTO purchase_invoices
                   (invoice_no, invoice_date, supplier_id, due_date, total_amount)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (
                    "PI-PAYMENT-TEST",
                    date(2026, 10, 18),
                    supplier_id,
                    date(2026, 11, 17),
                    80,
                ),
            ).fetchone()[0]
            connection.commit()
        finally:
            connection.close()

        self.login_as(
            self.projects_admin_id, "test_projects_admin", "admin", "projects"
        )
        self.assertEqual(self.client.get("/sales-payments").status_code, 302)
        self.login_as(
            self.stock_admin_id, "test_stock_admin", "admin", "distribution"
        )
        with self.assertRaises(self.psycopg.Error):
            self.client.post(
                "/sales-payments/add",
                data={
                    "invoice_id": str(sales_invoice_id),
                    "customer_id": str(customer_id + 99999),
                    "payment_date": "2026-10-19",
                    "amount": "20",
                },
            )

        connection = database.get_connection()
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM sales_payments").fetchone()[0],
                0,
            )
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        finally:
            connection.close()

        self.assertEqual(
            self.client.post(
                "/sales-payments/add",
                data={
                    "invoice_id": str(sales_invoice_id),
                    "customer_id": str(customer_id),
                    "payment_date": "2026-10-19",
                    "amount": "40.50",
                    "payment_mode": "bank",
                    "reference_no": "PAY-RECEIVED",
                },
            ).status_code,
            302,
        )
        invoice_response = self.client.get(
            f"/api/customer-invoices/{customer_id}"
        )
        invoice_data = invoice_response.json["invoices"][0]
        self.assertEqual(invoice_data["invoice_date"], "2026-10-18")
        self.assertEqual(invoice_data["due_date"], "2026-11-17")
        self.assertEqual(invoice_data["paid"], 40.5)
        self.assertEqual(invoice_data["status"], "partial")
        self.assertEqual(
            self.client.get(
                "/sales-payments?customer_id="
                f"{customer_id}&from_date=2026-10-19&to_date=2026-10-19"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                "/daybook?from_date=2026-10-19&to_date=2026-10-19"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/purchase-payments/add",
                data={
                    "invoice_id": str(purchase_invoice_id),
                    "supplier_id": str(supplier_id),
                    "payment_date": "2026-10-19",
                    "amount": "15",
                    "payment_mode": "upi",
                    "reference_no": "PAY-PAID",
                },
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.get(f"/statement/receivable/{customer_id}").status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/outstanding?view=ageing").status_code,
            200,
        )
        supplier_invoice = self.client.get(
            f"/api/supplier-invoices/{supplier_id}"
        )
        self.assertEqual(supplier_invoice.status_code, 200)
        purchase_data = supplier_invoice.json["invoices"][0]
        self.assertEqual(purchase_data["invoice_date"], "2026-10-18")
        self.assertEqual(purchase_data["due_date"], "2026-11-17")
        self.assertEqual(purchase_data["paid"], 15)
        self.assertEqual(purchase_data["status"], "partial")

    def test_authentication_system_scope_user_management_and_rollback(self):
        from werkzeug.security import check_password_hash, generate_password_hash

        connection = database.get_connection()
        try:
            distribution_user_id = connection.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, role, system)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (
                    "auth_scoped_user",
                    generate_password_hash("distribution-password"),
                    "Distribution Auth User",
                    "admin",
                    "distribution",
                ),
            ).fetchone()[0]
            manufacturing_user_id = connection.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, role, system)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (
                    "auth_scoped_user",
                    generate_password_hash("manufacturing-password"),
                    "Manufacturing Auth User",
                    "admin",
                    "manufacturing",
                ),
            ).fetchone()[0]
            connection.execute(
                """CREATE FUNCTION reject_test_auth_user() RETURNS trigger
                   LANGUAGE plpgsql AS $$
                   BEGIN
                       IF NEW.username = 'rollback_auth_user' THEN
                           RAISE EXCEPTION 'rejected auth user insert';
                       END IF;
                       RETURN NEW;
                   END;
                   $$"""
            )
            connection.execute(
                """CREATE TRIGGER reject_test_auth_user
                   BEFORE INSERT ON users
                   FOR EACH ROW EXECUTE FUNCTION reject_test_auth_user()"""
            )
            connection.commit()
        finally:
            connection.close()

        self.assertEqual(
            self.client.post(
                "/login",
                data={
                    "username": "auth_scoped_user",
                    "password": "manufacturing-password",
                    "system": "distribution",
                },
            ).status_code,
            302,
        )
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

        self.assertEqual(
            self.client.post(
                "/login",
                data={
                    "username": "auth_scoped_user",
                    "password": "distribution-password",
                    "system": "distribution",
                },
            ).status_code,
            302,
        )
        with self.client.session_transaction() as session:
            self.assertEqual(session["user_id"], distribution_user_id)
            self.assertEqual(session["system"], "distribution")

        self.assertEqual(
            self.client.post(
                "/users/add",
                data={
                    "username": "auth_created_user",
                    "full_name": "Created Auth User",
                    "role": "sales",
                    "password": "initial-password",
                },
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                "/users/add",
                data={
                    "username": "auth_created_user",
                    "full_name": "Duplicate Auth User",
                    "role": "sales",
                    "password": "duplicate-password",
                },
            ).status_code,
            302,
        )
        with self.client.session_transaction() as session:
            self.assertTrue(
                any(
                    "username is already taken" in message
                    for _, message in session.get("_flashes", [])
                )
            )
        connection = database.get_connection()
        try:
            created = connection.execute(
                """SELECT id, password_hash, role, system
                   FROM users WHERE username = %s AND system = %s""",
                ("auth_created_user", "distribution"),
            ).fetchone()
            self.assertEqual(created["role"], "sales")
            self.assertEqual(created["system"], "distribution")
            self.assertTrue(
                check_password_hash(created["password_hash"], "initial-password")
            )
            created_user_id = created["id"]

            connection.execute(
                """INSERT INTO users
                   (username, password_hash, role, system)
                   VALUES (%s, %s, %s, %s)""",
                (
                    "auth_created_user",
                    generate_password_hash("manufacturing-only-password"),
                    "admin",
                    "manufacturing",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        self.assertEqual(
            self.client.post(
                f"/users/edit/{created_user_id}",
                data={
                    "full_name": "Updated Auth User",
                    "role": "purchase",
                    "is_active": "on",
                    "password": "updated-password",
                },
            ).status_code,
            302,
        )
        connection = database.get_connection()
        try:
            updated = connection.execute(
                "SELECT password_hash, full_name, role, system FROM users WHERE id = %s",
                (created_user_id,),
            ).fetchone()
            self.assertEqual(updated["full_name"], "Updated Auth User")
            self.assertEqual(updated["role"], "purchase")
            self.assertEqual(updated["system"], "distribution")
            self.assertTrue(
                check_password_hash(updated["password_hash"], "updated-password")
            )
        finally:
            connection.close()

        self.client.get("/logout")
        self.assertEqual(
            self.client.post(
                "/login",
                data={
                    "username": "auth_created_user",
                    "password": "updated-password",
                    "system": "distribution",
                },
            ).status_code,
            302,
        )
        with self.client.session_transaction() as session:
            self.assertEqual(session["user_id"], created_user_id)
            self.assertEqual(session["system"], "distribution")
        self.client.get("/logout")
        self.login_as(
            self.stock_admin_id, "test_stock_admin", "admin", "distribution"
        )

        with self.assertRaises(self.psycopg.Error):
            self.client.post(
                "/users/add",
                data={
                    "username": "rollback_auth_user",
                    "full_name": "Rollback Auth User",
                    "role": "sales",
                    "password": "rollback-password",
                },
            )
        connection = database.get_connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM users WHERE username = %s",
                    ("rollback_auth_user",),
                ).fetchone()[0],
                0,
            )
            connection.execute(
                "DROP TRIGGER reject_test_auth_user ON users"
            )
            connection.execute("DROP FUNCTION reject_test_auth_user()")
            connection.commit()
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        finally:
            connection.close()

        self.assertNotEqual(distribution_user_id, manufacturing_user_id)


if __name__ == "__main__":
    unittest.main()
