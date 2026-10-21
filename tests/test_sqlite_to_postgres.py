from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
import io
import sys
from pathlib import Path

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database
from initial_admin import (
    AdminProvisioningError,
    get_admin_provisioning_target,
    provision_initial_admin,
)
from sqlite_to_postgres import (
    TransferError,
    UnsafeTransferTargetError,
    _column_sql,
    _extract_table_sql,
    _offline_plan,
    convert_value,
    create_read_only_source,
    dependency_order,
    get_transfer_target,
    inspect_source,
    load_migrations,
    load_migration_schema,
    main as transfer_main,
    source_sequence_values,
    transfer_database,
)


@pytest.fixture
def sqlite_source(tmp_path, monkeypatch):
    old_database = database.DATABASE
    database.close_postgres_pool()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database.DATABASE = str(tmp_path / "source.db")
    database.create_tables()
    connection = database.get_connection()
    try:
        customer_id = connection.execute(
            "INSERT INTO customers (name) VALUES (?)", ("Transfer Customer",)
        ).lastrowid
        deleted_customer_id = connection.execute(
            "INSERT INTO customers (name) VALUES (?)", ("Deleted Customer",)
        ).lastrowid
        connection.execute(
            "DELETE FROM customers WHERE id = ?", (deleted_customer_id,)
        )
        connection.execute(
            "UPDATE users SET password_hash = ?",
            (generate_password_hash("individually-changed-development-password"),),
        )
        invoice_id = connection.execute(
            """INSERT INTO sales_invoices
               (invoice_no, invoice_date, customer_id, total_amount)
               VALUES (?, ?, ?, ?)""",
            ("SI-20261020-0001", "2026-10-20", customer_id, 125.5),
        ).lastrowid
        connection.execute(
            """INSERT INTO sales_payments
               (invoice_id, customer_id, payment_date, amount)
               VALUES (?, ?, ?, ?)""",
            (invoice_id, customer_id, "2026-10-20", 25.5),
        )
        product_id = connection.execute(
            "INSERT INTO products (product_name) VALUES (?)", ("Transfer Product",)
        ).lastrowid
        connection.execute(
            """INSERT INTO stock_movements
               (product_id, movement_type, quantity, movement_date, amount)
               VALUES (?, 'IN', ?, ?, ?)""",
            (product_id, 4.0, "2026-10-20", 80.0),
        )
        connection.commit()
    finally:
        connection.close()
    source = create_read_only_source(database.DATABASE)
    yield source, Path(database.DATABASE)
    source.close()
    database.close_postgres_pool()
    database.DATABASE = old_database


class FakeDestination:
    def __init__(self, schema, *, fail_on_table=None, row_count_mismatch=None):
        self.schema = schema
        self.fail_on_table = fail_on_table
        self.row_count_mismatch = row_count_mismatch
        self.data = {table: [] for table in schema}
        self.sequences = {}
        self.rollback_count = 0
        self.migration_sql = {}
        for migration in load_migrations():
            self.migration_sql.update(_extract_table_sql(migration.sql))

    @contextmanager
    def transaction(self):
        before_data = deepcopy(self.data)
        before_sequences = deepcopy(self.sequences)
        try:
            yield
        except Exception:
            self.data = before_data
            self.sequences = before_sequences
            self.rollback_count += 1
            raise

    def verify_migration_ledger(self, schema, migrations):
        assert schema == "transfer_stage"
        assert [migration.version for migration in migrations] == [
            "0001", "0002", "0003", "0004", "0005", "0006", "0007", "0008"
        ]

    def application_tables(self, schema):
        return sorted(self.schema)

    def table_definition(self, schema, table):
        definition = self.schema[table]
        columns = []
        for name, declared_type in definition.columns:
            postgres_type = {
                "BIGINT": "bigint",
                "INTEGER": "integer",
                "DOUBLE": "double precision",
                "TEXT": "text",
                "DATE": "date",
                "TIMESTAMP": "timestamp without time zone",
            }.get(declared_type, "text")
            required = (
                name in definition.primary_key
                or "NOT NULL" in _column_sql(self.migration_sql[table], name)
            )
            identity = "YES" if name in definition.primary_key and name == "id" else "NO"
            columns.append(
                (name, postgres_type, "NO" if required else "YES", identity)
            )
        return {
            "columns": tuple(columns),
            "primary_key": definition.primary_key,
            "foreign_keys": definition.foreign_keys,
            "unique_keys": definition.unique_keys,
        }

    def row_count(self, schema, table):
        count = len(self.data[table])
        if table == self.row_count_mismatch and count:
            return count + 1
        return count

    def insert_rows(self, schema, table, columns, rows):
        if table == self.fail_on_table:
            raise TransferError(f"Simulated foreign-key violation in {table}.")
        self.data[table].extend(
            dict(zip(columns, row)) for row in rows
        )

    def advance_sequence(self, schema, table, primary_key, source_high_water=None):
        if primary_key == ("id",):
            self.sequences[table] = max(
                max((row["id"] for row in self.data[table]), default=0),
                source_high_water or 0,
            ) + 1

    def critical_totals(self, schema, table, columns, stock_keys):
        if columns:
            keys, measure = columns
            groups = {}
            for row in self.data[table]:
                key = tuple(row.get(column) for column in keys)
                groups[key] = groups.get(key, 0) + (row.get(measure) or 0)
            return [tuple(key) + (value,) for key, value in sorted(groups.items())]
        groups = {}
        for row in self.data[table]:
            key = tuple(row.get(column) for column in stock_keys)
            groups[key] = groups.get(key, 0) + (row.get("quantity") or 0)
        return [tuple(key) + (value,) for key, value in sorted(groups.items())]


class FakeAdminConnection:
    def __init__(self, database_name="admin_stage", existing_admin=False):
        self.database_name = database_name
        self.existing_admin = existing_admin
        self.inserted = None
        self.rolled_back = False
        self.statements = []

    @contextmanager
    def transaction(self):
        try:
            yield
        except Exception:
            self.inserted = None
            self.rolled_back = True
            raise

    def execute(self, statement, parameters=()):
        self.statements.append((statement, parameters))
        if statement == "SELECT current_database()":
            return FakeResult((self.database_name,))
        if statement.startswith("SELECT 1 FROM users"):
            return FakeResult((1,) if self.existing_admin else None)
        if "pg_namespace" in statement or "information_schema.tables" in statement:
            return FakeResult((1,))
        if statement.lstrip().startswith("INSERT INTO users"):
            self.inserted = parameters
            return FakeResult(None)
        return FakeResult(None)


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def _target_env(**overrides):
    values = {
        "ALLOW_STAGING_DATA_TRANSFER": "YES",
        "TRANSFER_DATABASE_DISPOSABLE": "YES",
        "TRANSFER_DATABASE_URL": "postgresql://user:secret@staging-db.example.test:5432/sumak_stage?sslmode=require",
        "TRANSFER_DATABASE_HOST_ALLOWLIST": "staging-db.example.test",
        "TRANSFER_DATABASE_NAME": "sumak_stage",
        "TRANSFER_DATABASE_SCHEMA": "transfer_stage",
    }
    values.update(overrides)
    return values


def test_offline_plan_checks_75_tables_counts_and_fk_dependency_order(sqlite_source):
    source, _ = sqlite_source

    counts, order = inspect_source(source)
    plan = _offline_plan(source)

    assert len(load_migration_schema()) == 75
    assert len(order) == 75
    assert counts["users"] == 2
    assert counts["sales_invoices"] == 1
    assert plan["write_performed"] is False
    assert plan["postgresql_validation"].startswith("not performed")
    customer_plan = next(
        item for item in plan["tables"] if item["name"] == "customers"
    )
    assert customer_plan["sqlite_sequence_high_water"] == 2
    assert order.index("customers") < order.index("sales_invoices")
    assert order.index("sales_invoices") < order.index("sales_payments")


def test_source_opens_read_only_and_leaves_database_unchanged(sqlite_source):
    source, source_path = sqlite_source

    with pytest.raises(Exception):
        source.execute("DELETE FROM customers")
    check = database.sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        assert check.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 1
    finally:
        check.close()


def test_transfer_preflight_rejects_unchanged_builtin_admin_password(tmp_path, monkeypatch):
    old_database = database.DATABASE
    database.close_postgres_pool()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database.DATABASE = str(tmp_path / "default-users.db")
    database.create_tables()
    source = create_read_only_source(database.DATABASE)
    try:
        with pytest.raises(TransferError, match="built-in development administrator password"):
            inspect_source(source)
    finally:
        source.close()
        database.close_postgres_pool()
        database.DATABASE = old_database


def test_source_preflight_rejects_foreign_key_violations(sqlite_source):
    source, source_path = sqlite_source
    source.close()
    writable = database.sqlite3.connect(source_path)
    try:
        writable.execute(
            """INSERT INTO stock_movements
               (product_id, movement_type, quantity)
               VALUES (987654, 'IN', 1)"""
        )
        writable.commit()
    finally:
        writable.close()
    invalid_source = create_read_only_source(source_path)
    try:
        with pytest.raises(TransferError, match="foreign-key violation"):
            inspect_source(invalid_source)
    finally:
        invalid_source.close()


def test_source_preflight_rejects_empty_value_for_required_date(sqlite_source):
    source, source_path = sqlite_source
    source.close()
    writable = database.sqlite3.connect(source_path)
    try:
        writable.execute(
            "UPDATE sales_invoices SET invoice_date = ''"
        )
        writable.commit()
    finally:
        writable.close()
    invalid_source = create_read_only_source(source_path)
    try:
        with pytest.raises(TransferError, match="empty or NULL"):
            inspect_source(invalid_source)
    finally:
        invalid_source.close()


def test_transfer_cli_dry_run_is_offline_and_reports_table_counts(
    sqlite_source, capsys
):
    _, source_path = sqlite_source

    transfer_main(["--source", str(source_path), "--dry-run"])

    result = __import__("json").loads(capsys.readouterr().out)
    assert result["table_count"] == 75
    assert result["write_performed"] is False
    assert "no destination connection" in result["postgresql_validation"]


def test_transfer_preserves_rows_critical_totals_and_advances_ids(sqlite_source):
    source, _ = sqlite_source
    schema = load_migration_schema()
    destination = FakeDestination(schema)

    report = transfer_database(source, destination, "transfer_stage")

    assert report.source_counts == report.destination_counts
    assert report.destination_counts["sales_invoices"] == 1
    assert report.verified_totals["sales_invoices"] == [
        ("SI-20261020-0001", 125.5)
    ]
    assert destination.data["sales_invoices"][0]["due_date"] is None
    assert report.verified_totals["stock_movements"] == [
        (1, "IN", 4.0)
    ]
    assert source_sequence_values(source)["customers"] == 2
    assert destination.sequences["customers"] == 3
    assert destination.sequences["sales_invoices"] == 2
    assert destination.rollback_count == 0


@pytest.mark.parametrize(
    "failure",
    ["row_count_mismatch", "foreign_key_violation", "critical_total_mismatch"],
)
def test_failed_transfer_rolls_back_all_destination_rows(sqlite_source, failure):
    source, _ = sqlite_source
    schema = load_migration_schema()
    destination = FakeDestination(
        schema,
        row_count_mismatch=(
            "customers" if failure == "row_count_mismatch" else None
        ),
        fail_on_table=(
            "sales_invoices" if failure == "foreign_key_violation" else None
        ),
    )
    if failure == "critical_total_mismatch":
        original_totals = destination.critical_totals

        def bad_totals(schema_name, table, columns, stock_keys):
            totals = original_totals(schema_name, table, columns, stock_keys)
            if table == "sales_invoices":
                totals[0] = totals[0][:-1] + (totals[0][-1] + 1,)
            return totals

        destination.critical_totals = bad_totals

    with pytest.raises(TransferError):
        transfer_database(source, destination, "transfer_stage")

    assert destination.rollback_count == 1
    assert all(not rows for rows in destination.data.values())
    assert destination.sequences == {}


def test_transfer_rejects_nonempty_destination_before_inserting(sqlite_source):
    source, _ = sqlite_source
    schema = load_migration_schema()
    destination = FakeDestination(schema)
    destination.data["users"].append({"id": 55})

    with pytest.raises(TransferError, match="not empty"):
        transfer_database(source, destination, "transfer_stage")

    assert destination.rollback_count == 1
    assert destination.data["users"] == [{"id": 55}]


@pytest.mark.parametrize(
    "overrides",
    [
        {"ALLOW_STAGING_DATA_TRANSFER": ""},
        {"TRANSFER_DATABASE_DISPOSABLE": ""},
        {"TRANSFER_DATABASE_URL": "sqlite:///production.db"},
        {"TRANSFER_DATABASE_URL": "postgresql://u:p@prod.example.test:5432/sumak_stage?sslmode=require"},
        {"TRANSFER_DATABASE_URL": "postgresql://u:p@stage.example.test:5432/sumak_production?sslmode=require"},
        {"TRANSFER_DATABASE_URL": "postgresql://u:p@staging-db.example.test:5432/sumak_stage"},
        {"TRANSFER_DATABASE_HOST_ALLOWLIST": "wrong.example.test"},
        {"TRANSFER_DATABASE_NAME": "different"},
        {"TRANSFER_DATABASE_SCHEMA": "not-valid.schema"},
        {"DATABASE_URL": "postgresql://u:p@staging-db.example.test:5432/sumak_stage?sslmode=require"},
    ],
)
def test_transfer_target_refuses_unsafe_or_unconfirmed_configuration(overrides):
    with pytest.raises(UnsafeTransferTargetError):
        get_transfer_target(_target_env(**overrides))


def test_transfer_target_requires_explicit_disposable_nonproduction_identity():
    target = get_transfer_target(_target_env())

    assert target.host == "staging-db.example.test"
    assert target.database == "sumak_stage"
    assert target.schema == "transfer_stage"


@pytest.mark.parametrize(
    "value,target,expected",
    [
        (None, "text", None),
        ("2026-10-20", "date", date(2026, 10, 20)),
        ("2026-10-20T01:02:03", "timestamp without time zone", datetime(2026, 10, 20, 1, 2, 3)),
        (1, "boolean", True),
        (0, "boolean", False),
        (b"\x00\xff", "bytea", b"\x00\xff"),
        (12.5, "numeric", pytest.approx(12.5)),
    ],
)
def test_sqlite_scalar_conversions_are_explicit(value, target, expected):
    converted = convert_value(value, "example", "value", target)
    if target == "numeric" and expected is not None:
        assert float(converted) == expected
    else:
        assert converted == expected


def test_sqlite_scalar_conversion_rejects_invalid_values():
    with pytest.raises(TransferError, match="Invalid date"):
        convert_value("not-a-date", "sales_invoices", "invoice_date", "date")
    with pytest.raises(TransferError, match="Non-finite"):
        convert_value(float("inf"), "products", "sale_price", "double precision")
    with pytest.raises(TransferError, match="NUL"):
        convert_value("bad\x00value", "customers", "name", "text")


def test_dependency_order_rejects_cycles():
    from sqlite_to_postgres import TableDefinition

    tables = {
        "a": TableDefinition("a", (), (), ((('b_id',), "b", ("id",)),), ()),
        "b": TableDefinition("b", (), (), ((('a_id',), "a", ("id",)),), ()),
    }

    with pytest.raises(TransferError, match="cycle"):
        dependency_order(tables)


def test_admin_provisioning_target_requires_all_confirmations():
    env = {
        "ALLOW_INITIAL_ADMIN_PROVISIONING": "YES",
        "ADMIN_PROVISIONING_DATABASE_URL": "postgresql://user:secret@admin-stage.test:5432/sumak_stage?sslmode=require",
        "ADMIN_PROVISIONING_HOST_ALLOWLIST": "admin-stage.test",
        "ADMIN_PROVISIONING_DATABASE_NAME": "sumak_stage",
        "ADMIN_PROVISIONING_SCHEMA": "erp",
    }
    target = get_admin_provisioning_target(env)
    assert target.database == "sumak_stage"
    assert target.schema == "erp"
    env["ADMIN_PROVISIONING_HOST_ALLOWLIST"] = "different.test"
    with pytest.raises(AdminProvisioningError):
        get_admin_provisioning_target(env)


def test_admin_provisioning_hashes_password_and_keeps_system_scope():
    connection = FakeAdminConnection()
    provision_initial_admin(
        connection,
        "admin_stage",
        "erp",
        " scoped-admin ",
        "Scoped Admin",
        "manufacturing",
        "a-long-random-password",
    )

    username, password_hash, full_name, system = connection.inserted
    assert username == "scoped-admin"
    assert full_name == "Scoped Admin"
    assert system == "manufacturing"
    assert password_hash != "a-long-random-password"
    assert check_password_hash(password_hash, "a-long-random-password")
    lock_index = next(
        index
        for index, (statement, _) in enumerate(connection.statements)
        if "pg_advisory_xact_lock" in statement
    )
    admin_check_index = next(
        index
        for index, (statement, _) in enumerate(connection.statements)
        if statement.startswith("SELECT 1 FROM users")
    )
    assert lock_index < admin_check_index


def test_admin_provisioning_refuses_existing_admin_and_wrong_database():
    existing = FakeAdminConnection(existing_admin=True)
    with pytest.raises(AdminProvisioningError, match="already exists"):
        provision_initial_admin(
            existing,
            "admin_stage",
            "erp",
            "new-admin",
            "New Admin",
            "distribution",
            "a-long-random-password",
        )
    assert existing.rolled_back

    wrong_target = FakeAdminConnection(database_name="unexpected")
    with pytest.raises(AdminProvisioningError, match="does not match"):
        provision_initial_admin(
            wrong_target,
            "admin_stage",
            "erp",
            "new-admin",
            "New Admin",
            "distribution",
            "a-long-random-password",
        )
    assert wrong_target.rolled_back


def test_admin_provisioning_refuses_short_password_and_invalid_system():
    with pytest.raises(AdminProvisioningError, match="12 characters"):
        provision_initial_admin(
            FakeAdminConnection(),
            "admin_stage",
            "erp",
            "admin",
            "Admin",
            "distribution",
            "short",
        )
    with pytest.raises(AdminProvisioningError, match="Unsupported system"):
        provision_initial_admin(
            FakeAdminConnection(),
            "admin_stage",
            "erp",
            "admin",
            "Admin",
            "unknown",
            "a-long-random-password",
        )


def test_admin_cli_requires_tty_before_reading_password(monkeypatch):
    import initial_admin

    monkeypatch.setattr(
        initial_admin,
        "get_admin_provisioning_target",
        lambda: object(),
    )
    monkeypatch.setattr(initial_admin.sys, "stdin", io.StringIO())
    monkeypatch.setattr(initial_admin.sys, "stdout", io.StringIO())
    monkeypatch.setattr(
        initial_admin.getpass,
        "getpass",
        lambda *_args: pytest.fail("password prompt must not run without a TTY"),
    )

    with pytest.raises(SystemExit, match="interactive terminal"):
        initial_admin.main(
            [
                "--system",
                "distribution",
                "--username",
                "secure-admin",
                "--full-name",
                "Secure Admin",
            ]
        )
