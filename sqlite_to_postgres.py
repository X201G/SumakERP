"""Guarded, explicit SQLite-to-PostgreSQL staging data transfer utility."""

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qsl, urlsplit

from postgres_migrations import MIGRATIONS_DIR, load_migrations
from werkzeug.security import check_password_hash


_CREATE_TABLE = re.compile(
    r"\bCREATE\s+TABLE\s+([A-Za-z_][A-Za-z_0-9]*)\s*\(",
    re.IGNORECASE,
)
_COLUMN = re.compile(r"^\s*([A-Za-z_][A-Za-z_0-9]*)\s+([A-Za-z_][A-Za-z_0-9]*)")
_TABLE_FK = re.compile(
    r"\bFOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+"
    r"([A-Za-z_][A-Za-z_0-9]*)\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_INLINE_FK = re.compile(
    r"\bREFERENCES\s+([A-Za-z_][A-Za-z_0-9]*)\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_TABLE_PK = re.compile(r"\bPRIMARY\s+KEY\s*\(([^)]+)\)", re.IGNORECASE)
_FK_ACTION = {
    action: re.compile(rf"\bON\s+{action}\s+(NO\s+ACTION|CASCADE|RESTRICT|SET\s+NULL|SET\s+DEFAULT)\b", re.IGNORECASE)
    for action in ("UPDATE", "DELETE")
}
_NON_PRODUCTION_MARKER = re.compile(
    r"(?:^|[-_.])(test|testing|stage|staging|dev|development|ci|disposable|sandbox)(?:$|[-_.])"
)
_PRODUCTION_MARKER = re.compile(
    r"(?:^|[-_.])(prod|production|live)(?:$|[-_.])"
)
_CRITICAL_RECORDS = {
    "sales_invoices": (("invoice_no",), "total_amount"),
    "purchase_invoices": (("invoice_no",), "total_amount"),
    "sales_returns": (("return_no",), "total_amount"),
    "purchase_returns": (("return_no",), "total_amount"),
    "sales_payments": (("id", "invoice_id", "customer_id", "payment_date"), "amount"),
    "purchase_payments": (("id", "invoice_id", "supplier_id", "payment_date"), "amount"),
    "mfg_invoices": (("invoice_no",), "total_amount"),
    "mfg_purchase_invoices": (("invoice_no",), "total_amount"),
    "mfg_purchase_returns": (("return_no",), "total_amount"),
    "mfg_sales_payments": (("id", "invoice_id", "customer_id", "payment_date"), "amount"),
    "mfg_purchase_payments": (("id", "invoice_id", "supplier_id", "payment_date"), "amount"),
    "mfg_resharpen_invoices": (("invoice_no",), "total_amount"),
    "mfg_resharpen_sales_payments": (("id", "invoice_id", "customer_id", "payment_date"), "amount"),
}
_STOCK_BALANCE_KEYS = {
    "stock_movements": ("product_id", "movement_type"),
    "mfg_stock_movements": ("item_type", "item_id", "movement_type"),
}
_LEGACY_DEFAULT_ACCOUNTS = (
    ("admin", "distribution", "admin123"),
    ("mfgadmin", "manufacturing", "mfg123"),
)


class TransferError(RuntimeError):
    """Raised when source validation or a destination transfer fails."""


class UnsafeTransferTargetError(TransferError):
    """Raised when the destination is not explicitly confirmed disposable."""


@dataclass(frozen=True)
class TransferTarget:
    database_url: str
    host: str
    database: str
    schema: str


@dataclass(frozen=True)
class TableDefinition:
    name: str
    columns: tuple
    primary_key: tuple
    foreign_keys: tuple
    unique_keys: tuple


@dataclass
class TransferReport:
    source_counts: dict
    destination_counts: dict
    verified_totals: dict
    table_order: tuple


def get_transfer_target(environ=None):
    """Require exact staging identity, TLS, allowlisting, and disposable opt-in."""
    env = os.environ if environ is None else environ
    if env.get("ALLOW_STAGING_DATA_TRANSFER", "").strip() != "YES":
        raise UnsafeTransferTargetError(
            "Set ALLOW_STAGING_DATA_TRANSFER=YES to explicitly enable data transfer."
        )
    if env.get("TRANSFER_DATABASE_DISPOSABLE", "").strip() != "YES":
        raise UnsafeTransferTargetError(
            "Set TRANSFER_DATABASE_DISPOSABLE=YES only after confirming the target is disposable."
        )
    database_url = env.get("TRANSFER_DATABASE_URL", "").strip()
    if not database_url:
        raise UnsafeTransferTargetError(
            "Set TRANSFER_DATABASE_URL to the dedicated disposable PostgreSQL target."
        )
    try:
        parsed = urlsplit(database_url)
        port = parsed.port
    except ValueError:
        raise UnsafeTransferTargetError(
            "TRANSFER_DATABASE_URL is not a valid PostgreSQL URL."
        ) from None
    if (
        parsed.scheme not in ("postgres", "postgresql")
        or not parsed.hostname
        or not parsed.path.startswith("/")
        or not parsed.path[1:]
        or "/" in parsed.path[1:]
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise UnsafeTransferTargetError(
            "TRANSFER_DATABASE_URL must be a PostgreSQL URL with a host and database."
        )

    host = parsed.hostname.casefold()
    database = parsed.path[1:].casefold()
    sslmode = dict(parse_qsl(parsed.query)).get("sslmode", "").casefold()
    if sslmode not in {"require", "verify-ca", "verify-full"}:
        raise UnsafeTransferTargetError(
            "TRANSFER_DATABASE_URL must require TLS with a supported sslmode."
        )
    if _PRODUCTION_MARKER.search(host) or _PRODUCTION_MARKER.search(database):
        raise UnsafeTransferTargetError(
            "Production-like hosts and database names are not permitted for transfer."
        )

    allowlisted_hosts = {
        entry.strip().casefold()
        for entry in env.get("TRANSFER_DATABASE_HOST_ALLOWLIST", "").split(",")
        if entry.strip()
    }
    expected_database = env.get("TRANSFER_DATABASE_NAME", "").strip().casefold()
    schema = env.get("TRANSFER_DATABASE_SCHEMA", "").strip()
    if not allowlisted_hosts or host not in allowlisted_hosts:
        raise UnsafeTransferTargetError(
            "TRANSFER_DATABASE_HOST_ALLOWLIST must contain the exact target host."
        )
    if not _NON_PRODUCTION_MARKER.search(database) and not _NON_PRODUCTION_MARKER.search(host):
        raise UnsafeTransferTargetError(
            "The target host or database name must identify a test, staging, or disposable environment."
        )
    if not expected_database or database != expected_database:
        raise UnsafeTransferTargetError(
            "TRANSFER_DATABASE_NAME must exactly match the URL database."
        )
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", schema):
        raise UnsafeTransferTargetError(
            "TRANSFER_DATABASE_SCHEMA must be a valid explicit PostgreSQL schema name."
        )

    production_url = env.get("DATABASE_URL", "").strip()
    if production_url:
        try:
            production = urlsplit(production_url)
            production_host = (production.hostname or "").casefold()
            production_database = production.path.strip("/").casefold()
        except ValueError:
            production_host = ""
            production_database = ""
        if (host, database) == (production_host, production_database):
            raise UnsafeTransferTargetError(
                "The transfer destination must not match DATABASE_URL."
            )
    if database == env.get("PRODUCTION_DATABASE_NAME", "").strip().casefold():
        raise UnsafeTransferTargetError(
            "The transfer destination must not match PRODUCTION_DATABASE_NAME."
        )
    return TransferTarget(database_url, host, database, schema)


def _extract_table_sql(migration_sql):
    definitions = {}
    for match in _CREATE_TABLE.finditer(migration_sql):
        depth = 1
        index = match.end()
        quote = None
        while index < len(migration_sql) and depth:
            char = migration_sql[index]
            if quote:
                if char == quote:
                    if quote == "'" and index + 1 < len(migration_sql) and migration_sql[index + 1] == "'":
                        index += 1
                    else:
                        quote = None
            elif char in ("'", '"', "`"):
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            index += 1
        if depth:
            raise TransferError("A PostgreSQL migration contains an unclosed table definition.")
        table = match.group(1).lower()
        definitions[table] = migration_sql[match.end(): index - 1]
    return definitions


def _split_sql_items(definition):
    items = []
    start = 0
    depth = 0
    quote = None
    index = 0
    while index < len(definition):
        char = definition[index]
        if quote:
            if char == quote:
                if quote == "'" and index + 1 < len(definition) and definition[index + 1] == "'":
                    index += 1
                else:
                    quote = None
        elif char in ("'", '"', "`"):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            items.append(definition[start:index].strip())
            start = index + 1
        index += 1
    tail = definition[start:].strip()
    if tail:
        items.append(tail)
    return items


def _migration_fk_actions(item):
    update = _FK_ACTION["UPDATE"].search(item)
    delete = _FK_ACTION["DELETE"].search(item)
    return (
        update.group(1).casefold() if update else "no action",
        delete.group(1).casefold() if delete else "no action",
    )


def load_migration_schema(migrations_dir=MIGRATIONS_DIR):
    """Read table/column/key definitions needed for offline source validation."""
    definitions = {}
    for migration in load_migrations(migrations_dir):
        definitions.update(_extract_table_sql(migration.sql))
    result = {}
    for table, definition in definitions.items():
        columns = []
        primary_key = []
        foreign_keys = []
        unique_keys = []
        for item in _split_sql_items(definition):
            if re.match(
                r"^\s*(?:CONSTRAINT|PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK)\b",
                item,
                re.IGNORECASE,
            ):
                pk_match = _TABLE_PK.search(item)
                if pk_match:
                    primary_key.extend(
                        part.strip().strip('"').lower()
                        for part in pk_match.group(1).split(",")
                    )
                fk_match = _TABLE_FK.search(item)
                if fk_match:
                    source_columns = tuple(
                        part.strip().strip('"').lower()
                        for part in fk_match.group(1).split(",")
                    )
                    target_columns = tuple(
                        part.strip().strip('"').lower()
                        for part in fk_match.group(3).split(",")
                    )
                    foreign_keys.append(
                        (
                            source_columns,
                            fk_match.group(2).lower(),
                            target_columns,
                            *_migration_fk_actions(item),
                        )
                    )
                unique_match = re.search(r"\bUNIQUE\s*\(([^)]+)\)", item, re.IGNORECASE)
                if unique_match:
                    unique_keys.append(
                        tuple(
                            part.strip().strip('"').lower()
                            for part in unique_match.group(1).split(",")
                        )
                    )
                continue
            column_match = _COLUMN.match(item)
            if column_match is None:
                pk_match = _TABLE_PK.search(item)
                if pk_match:
                    primary_key.extend(
                        part.strip().strip('"').lower()
                        for part in pk_match.group(1).split(",")
                    )
                fk_match = _TABLE_FK.search(item)
                if fk_match:
                    source_columns = tuple(
                        part.strip().strip('"').lower()
                        for part in fk_match.group(1).split(",")
                    )
                    target_columns = tuple(
                        part.strip().strip('"').lower()
                        for part in fk_match.group(3).split(",")
                    )
                    foreign_keys.append(
                        (
                            source_columns,
                            fk_match.group(2).lower(),
                            target_columns,
                            *_migration_fk_actions(item),
                        )
                    )
                unique_match = re.search(r"\bUNIQUE\s*\(([^)]+)\)", item, re.IGNORECASE)
                if unique_match:
                    unique_keys.append(
                        tuple(
                            part.strip().strip('"').lower()
                            for part in unique_match.group(1).split(",")
                        )
                    )
                continue
            name, declared_type = column_match.groups()
            name = name.lower()
            columns.append((name, declared_type.upper()))
            if re.search(r"\bPRIMARY\s+KEY\b", item, re.IGNORECASE):
                primary_key.append(name)
            if re.search(r"\bUNIQUE\b", item, re.IGNORECASE):
                unique_keys.append((name,))
            fk_match = _INLINE_FK.search(item)
            if fk_match:
                target_columns = tuple(
                    part.strip().strip('"').lower()
                    for part in fk_match.group(2).split(",")
                )
                foreign_keys.append(
                    (
                        (name,),
                        fk_match.group(1).lower(),
                        target_columns,
                        *_migration_fk_actions(item),
                    )
                )
        result[table] = TableDefinition(
            table,
            tuple(columns),
            tuple(primary_key),
            tuple(sorted(foreign_keys)),
            tuple(sorted(unique_keys)),
        )
    for migration in load_migrations(migrations_dir):
        for match in re.finditer(
            r"\bCREATE\s+UNIQUE\s+INDEX\s+[A-Za-z_][A-Za-z_0-9]*\s+"
            r"ON\s+([A-Za-z_][A-Za-z_0-9]*)\s*\(([^)]+)\)",
            migration.sql,
            re.IGNORECASE,
        ):
            table = match.group(1).lower()
            if table not in result:
                raise TransferError(
                    f"Unique index in migration references unknown table {table}."
                )
            columns = tuple(
                part.strip().strip('"').lower()
                for part in match.group(2).split(",")
            )
            old = result[table]
            result[table] = TableDefinition(
                old.name,
                old.columns,
                old.primary_key,
                old.foreign_keys,
                tuple(sorted(set(old.unique_keys) | {columns})),
            )
    if not result:
        raise TransferError("No PostgreSQL application tables were found in migrations.")
    return result


def _sqlite_tables(connection):
    return tuple(
        row[0]
        for row in connection.execute(
            """SELECT name FROM sqlite_master
               WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"""
        )
    )


def _sqlite_definition(connection, table):
    columns = tuple(
        (row["name"].lower(), row["type"].upper(), bool(row["notnull"]), row["pk"])
        for row in connection.execute(f'PRAGMA table_info("{table}")')
    )
    primary_key = tuple(
        row["name"].lower()
        for row in sorted(
            (row for row in connection.execute(f'PRAGMA table_info("{table}")') if row["pk"]),
            key=lambda row: row["pk"],
        )
    )
    grouped = {}
    for row in connection.execute(f'PRAGMA foreign_key_list("{table}")'):
        grouped.setdefault(row["id"], []).append(row)
    foreign_keys = []
    for rows in grouped.values():
        rows.sort(key=lambda row: row["seq"])
        foreign_keys.append(
            (
                tuple(row["from"].lower() for row in rows),
                rows[0]["table"].lower(),
                tuple(row["to"].lower() for row in rows),
                rows[0]["on_update"].casefold(),
                rows[0]["on_delete"].casefold(),
            )
        )
    unique_keys = []
    for index in connection.execute(f'PRAGMA index_list("{table}")'):
        if not index["unique"] or index["origin"] == "pk":
            continue
        index_name = index["name"]
        index_xinfo = connection.execute(
            f'PRAGMA index_xinfo("{index_name}")'
        ).fetchall()
        if any(
            row["key"] and row["coll"] and row["coll"].casefold() != "binary"
            for row in index_xinfo
        ):
            raise TransferError(
                f"SQLite unique index on {table} uses a non-portable collation."
            )
        index_columns = tuple(
            row["name"]
            for row in connection.execute(f'PRAGMA index_info("{index_name}")')
        )
        if any(column is None for column in index_columns):
            raise TransferError(
                f"SQLite unique expression index is unsupported on {table}."
            )
        unique_keys.append(tuple(column.lower() for column in index_columns))
    return TableDefinition(
        table,
        tuple((name, kind) for name, kind, _, _ in columns),
        primary_key,
        tuple(sorted(foreign_keys)),
        tuple(sorted(unique_keys)),
    ), columns


def _type_family(declared_type):
    value = declared_type.upper()
    if any(token in value for token in ("INT", "SERIAL")):
        return "integer"
    if any(token in value for token in ("REAL", "FLOA", "DOUB")):
        return "float"
    if any(token in value for token in ("NUM", "DEC")):
        return "numeric"
    if "BOOL" in value:
        return "boolean"
    if any(token in value for token in ("BLOB", "BYTEA")):
        return "binary"
    if any(token in value for token in ("DATE", "TIME")):
        return "datetime"
    return "text"


def _compatible_types(sqlite_type, postgres_type):
    source = _type_family(sqlite_type)
    target = _type_family(postgres_type)
    return source == target or (source == "integer" and target == "boolean")


def inspect_source(connection):
    """Validate SQLite application tables, schema, and source foreign keys."""
    definitions = load_migration_schema()
    migration_sql = {}
    for migration in load_migrations():
        migration_sql.update(_extract_table_sql(migration.sql))
    source_tables = set(_sqlite_tables(connection))
    expected_tables = set(definitions)
    if source_tables != expected_tables:
        missing = sorted(expected_tables - source_tables)
        extra = sorted(source_tables - expected_tables)
        raise TransferError(
            f"SQLite table inventory differs from migrations (missing={missing}, extra={extra})."
        )

    table_counts = {}
    for table in sorted(source_tables):
        source_definition, source_columns = _sqlite_definition(connection, table)
        expected = definitions[table]
        if expected.primary_key == ("id",) and not re.search(
            r"\bGENERATED\s+(?:ALWAYS|BY\s+DEFAULT)\s+AS\s+IDENTITY\b",
            _column_sql(migration_sql[table], "id"),
            re.IGNORECASE,
        ):
            raise TransferError(
                f"PostgreSQL migration does not define an identity for {table}.id."
            )
        source_names = {name for name, _ in source_definition.columns}
        expected_names = {name for name, _ in expected.columns}
        if source_names != expected_names:
            raise TransferError(
                f"Column inventory mismatch in {table}: "
                f"missing={sorted(expected_names - source_names)}, "
                f"extra={sorted(source_names - expected_names)}."
            )
        expected_types = dict(expected.columns)
        for source_name, source_type in source_definition.columns:
            expected_type = expected_types[source_name]
            if not _compatible_types(source_type, expected_type):
                raise TransferError(
                    f"Incompatible column type for {table}.{source_name}: "
                    f"SQLite {source_type or 'UNDECLARED'}, PostgreSQL migration {expected_type}."
                )
        if source_definition.primary_key != expected.primary_key:
            raise TransferError(
                f"Primary-key mismatch in {table}: SQLite {source_definition.primary_key}, "
                f"migrations {expected.primary_key}."
            )
        if source_definition.foreign_keys != expected.foreign_keys:
            raise TransferError(
                f"Foreign-key mismatch in {table}: SQLite {source_definition.foreign_keys}, "
                f"migrations {expected.foreign_keys}."
            )
        if source_definition.unique_keys != expected.unique_keys:
            raise TransferError(
                f"Unique-constraint mismatch in {table}: SQLite {source_definition.unique_keys}, "
                f"migrations {expected.unique_keys}."
            )
        for name, _, not_null, primary_key_order in source_columns:
            target_column = next(column for column in expected.columns if column[0] == name.lower())
            target_not_null = (
                "NOT NULL" in _column_sql(migration_sql[table], name)
                or bool(primary_key_order)
            )
            if not primary_key_order and bool(not_null) != target_not_null:
                raise TransferError(
                    f"Nullability mismatch for {table}.{name} between SQLite and migrations."
                )
            if target_column[1] == "BOOLEAN":
                null_count = connection.execute(
                    f'SELECT COUNT(*) FROM "{table}" '
                    f'WHERE LOWER(CAST("{name}" AS TEXT)) '
                    "NOT IN ('0', '1', 'true', 'false') "
                    f'AND "{name}" IS NOT NULL'
                ).fetchone()[0]
                if null_count:
                    raise TransferError(
                        f"{table}.{name} contains {null_count} values that cannot be converted to PostgreSQL boolean."
                    )
        required_columns = {
            name
            for name, _, _, primary_key_order in source_columns
            if primary_key_order
            or "NOT NULL" in _column_sql(migration_sql[table], name)
        }
        validate_source_values(
            connection,
            table,
            source_columns,
            expected.columns,
            required_columns,
        )
        table_counts[table] = connection.execute(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0]

    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchmany(1)
    if foreign_key_errors:
        first = foreign_key_errors[0]
        raise TransferError(
            f"SQLite source has a foreign-key violation in table {first[0]}."
        )
    source_users = connection.execute(
        "SELECT password_hash FROM users"
    ).fetchall()
    if any(
        check_password_hash(user["password_hash"], default_password)
        for user in source_users
        for _, _, default_password in _LEGACY_DEFAULT_ACCOUNTS
    ):
        raise TransferError(
            "SQLite source still contains a built-in development administrator password; "
            "change it before transfer."
        )
    order = dependency_order(definitions)
    return table_counts, order


def _postgres_type(declared_type):
    name = declared_type.casefold()
    types = {
        "bigint": "bigint",
        "integer": "integer",
        "smallint": "smallint",
        "double": "double precision",
        "real": "real",
        "text": "text",
        "date": "date",
        "timestamp": "timestamp without time zone",
        "timestamptz": "timestamp with time zone",
        "boolean": "boolean",
        "numeric": "numeric",
        "decimal": "decimal",
        "bytea": "bytea",
    }
    try:
        return types[name]
    except KeyError:
        raise TransferError(
            f"Unsupported PostgreSQL migration type {declared_type}."
        ) from None


def validate_source_values(
    connection, table, source_columns, migration_columns, required_columns
):
    """Check all scalar conversions without making a destination connection."""
    expected_types = {
        name: _postgres_type(declared_type)
        for name, declared_type in migration_columns
    }
    names = [column[0] for column in source_columns]
    cursor = connection.execute(
        f'SELECT {", ".join(chr(34) + name + chr(34) for name in names)} '
        f'FROM "{table}"'
    )
    while True:
        rows = cursor.fetchmany(500)
        if not rows:
            return
        for row in rows:
            for name, value in zip(names, row):
                converted = convert_value(value, table, name, expected_types[name])
                if converted is None and name in required_columns:
                    raise TransferError(
                        f"{table}.{name} contains an empty or NULL value required by the PostgreSQL schema."
                    )


def _column_sql(definition, name):
    for item in _split_sql_items(definition):
        match = _COLUMN.match(item)
        if match and match.group(1).casefold() == name.casefold():
            return item
    return ""


def dependency_order(definitions):
    """Return stable parent-before-child ordering; reject cyclic FK graphs."""
    parents = {table: set() for table in definitions}
    for table, definition in definitions.items():
        for foreign_key in definition.foreign_keys:
            parent = foreign_key[1]
            if parent not in definitions:
                raise TransferError(
                    f"Migration foreign key in {table} references unknown table {parent}."
                )
            parents[table].add(parent)
    ordered = []
    ready = sorted(table for table, dependencies in parents.items() if not dependencies)
    while ready:
        table = ready.pop(0)
        ordered.append(table)
        for child in sorted(parents):
            if table in parents[child]:
                parents[child].remove(table)
                if not parents[child] and child not in ordered and child not in ready:
                    ready.append(child)
                    ready.sort()
    if len(ordered) != len(definitions):
        cyclic = sorted(table for table, dependencies in parents.items() if dependencies)
        raise TransferError(
            "Foreign-key dependency cycle cannot be safely transferred: "
            + ", ".join(cyclic)
        )
    return tuple(ordered)


def _parse_date(value, table, column):
    if value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise TransferError(f"Invalid date value in {table}.{column}.") from None


def _parse_datetime(value, table, column):
    if value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise TransferError(f"Invalid timestamp value in {table}.{column}.") from None


def convert_value(value, table, column, postgres_type):
    """Convert SQLite scalar values to Psycopg-compatible target values."""
    if value is None:
        return None
    target = postgres_type.casefold()
    if value == "" and target in {
        "boolean",
        "smallint",
        "integer",
        "bigint",
        "real",
        "double precision",
        "numeric",
        "decimal",
    }:
        return None
    if target == "boolean":
        if value in (0, 1, False, True):
            return bool(value)
        if isinstance(value, str) and value.casefold() in {"true", "false", "0", "1"}:
            return value.casefold() in {"true", "1"}
        raise TransferError(f"Invalid boolean value in {table}.{column}.")
    if target == "date":
        return _parse_date(value, table, column)
    if target in {"timestamp without time zone", "timestamp with time zone"}:
        result = _parse_datetime(value, table, column)
        if target == "timestamp without time zone" and result.tzinfo is not None:
            raise TransferError(
                f"Timezone-aware timestamp cannot be copied to {table}.{column}."
            )
        return result
    if target in {"smallint", "integer", "bigint"}:
        if isinstance(value, bool):
            return int(value)
        try:
            converted = int(value)
        except (TypeError, ValueError, OverflowError):
            raise TransferError(f"Invalid integer value in {table}.{column}.") from None
        if isinstance(value, float) and not value.is_integer():
            raise TransferError(f"Non-integral value in integer column {table}.{column}.")
        return converted
    if target in {"real", "double precision"}:
        try:
            converted = float(value)
        except (TypeError, ValueError, OverflowError):
            raise TransferError(f"Invalid numeric value in {table}.{column}.") from None
        if not math.isfinite(converted):
            raise TransferError(f"Non-finite numeric value in {table}.{column}.")
        return converted
    if target in {"numeric", "decimal"}:
        try:
            converted = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise TransferError(f"Invalid numeric value in {table}.{column}.") from None
        if not converted.is_finite():
            raise TransferError(f"Non-finite numeric value in {table}.{column}.")
        return converted
    if target == "bytea":
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise TransferError(f"Non-binary value in PostgreSQL bytea column {table}.{column}.")
        return bytes(value)
    if target in {"text", "character varying", "character"}:
        if not isinstance(value, str):
            raise TransferError(f"Non-text value in PostgreSQL text column {table}.{column}.")
        if "\x00" in value:
            raise TransferError(f"NUL character cannot be copied from {table}.{column}.")
        return value
    raise TransferError(
        f"Unsupported PostgreSQL type {postgres_type} for {table}.{column}."
    )


def source_critical_totals(connection, table):
    if table in _CRITICAL_RECORDS:
        keys, measure = _CRITICAL_RECORDS[table]
        selected = ", ".join(f'"{column}"' for column in keys)
        grouped = selected
        rows = connection.execute(
            f'SELECT {selected}, COALESCE(SUM("{measure}"), 0) '
            f'FROM "{table}" GROUP BY {grouped} ORDER BY {grouped}'
        ).fetchall()
        return [tuple(row) for row in rows]
    if table in _STOCK_BALANCE_KEYS:
        keys = _STOCK_BALANCE_KEYS[table]
        group_by = ", ".join(f'"{column}"' for column in keys)
        selection = ", ".join(f'"{column}"' for column in keys)
        rows = connection.execute(
            f'SELECT {selection}, COALESCE(SUM(quantity), 0) '
            f'FROM "{table}" GROUP BY {group_by} ORDER BY {group_by}'
        ).fetchall()
        return [tuple(row) for row in rows]
    return None


def _values_match(source, destination):
    if len(source) != len(destination):
        return False
    for source_row, destination_row in zip(source, destination):
        if len(source_row) != len(destination_row):
            return False
        for left, right in zip(source_row, destination_row):
            if isinstance(left, (int, float, Decimal)) and isinstance(
                right, (int, float, Decimal)
            ):
                if not math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-6):
                    return False
            elif isinstance(left, (date, datetime)) and isinstance(right, (date, datetime)):
                if left.isoformat() != right.isoformat():
                    return False
            elif isinstance(left, str) and isinstance(right, (date, datetime)):
                try:
                    if date.fromisoformat(left).isoformat() != right.isoformat():
                        return False
                except ValueError:
                    return False
            elif isinstance(right, str) and isinstance(left, (date, datetime)):
                try:
                    if left.isoformat() != date.fromisoformat(right).isoformat():
                        return False
                except ValueError:
                    return False
            elif left != right:
                return False
    return True


def _prepare_source_rows(source, table, source_columns, target_columns):
    names = [column[0] for column in source_columns]
    type_by_name = {
        name: data_type for name, data_type, _, _ in target_columns
    }
    cursor = source.execute(
        f'SELECT {", ".join(chr(34) + name + chr(34) for name in names)} '
        f'FROM "{table}"'
    )
    while True:
        batch = cursor.fetchmany(500)
        if not batch:
            return
        for row in batch:
            yield tuple(
                convert_value(value, table, name, type_by_name[name])
                for name, value in zip(names, row)
            )


def transfer_database(source, destination, schema):
    """Transfer all migration tables in one destination transaction."""
    definitions = load_migration_schema()
    source_counts, order = inspect_source(source)
    source_sequences = source_sequence_values(source)
    verified_totals = {}
    destination_counts = {}
    with destination.transaction():
        destination.verify_migration_ledger(schema, load_migrations())
        destination_tables = set(destination.application_tables(schema))
        if destination_tables != set(definitions):
            missing = sorted(set(definitions) - destination_tables)
            extra = sorted(destination_tables - set(definitions))
            raise TransferError(
                f"Destination schema inventory differs from migrations (missing={missing}, extra={extra})."
            )
        target_schema = {
            table: destination.table_definition(schema, table)
            for table in order
        }
        for table in order:
            _validate_live_table(source, table, target_schema[table], definitions[table])
            if destination.row_count(schema, table):
                raise TransferError(
                    f"Destination table {table} is not empty; transfer requires a fresh empty schema."
                )

        for table in order:
            source_definition, source_columns = _sqlite_definition(source, table)
            target_columns = target_schema[table]["columns"]
            rows = _prepare_source_rows(source, table, source_columns, target_columns)
            destination.insert_rows(
                schema, table, [column[0] for column in source_columns], rows
            )
            actual_count = destination.row_count(schema, table)
            if actual_count != source_counts[table]:
                raise TransferError(
                    f"Row-count mismatch for {table}: source={source_counts[table]}, "
                    f"destination={actual_count}."
                )
            destination.advance_sequence(
                schema,
                table,
                source_definition.primary_key,
                source_sequences.get(table),
            )
            destination_counts[table] = actual_count

        for table in order:
            total = source_critical_totals(source, table)
            if total is None:
                continue
            actual = destination.critical_totals(
                schema,
                table,
                _CRITICAL_RECORDS.get(table),
                _STOCK_BALANCE_KEYS.get(table),
            )
            if not _values_match(total, actual):
                raise TransferError(
                    f"Critical business totals differ after transfer for {table}."
                )
            verified_totals[table] = total

    return TransferReport(source_counts, destination_counts, verified_totals, order)


def _validate_live_table(source, table, actual, expected):
    actual_columns = actual["columns"]
    expected_names = {name for name, _ in expected.columns}
    actual_names = {column[0] for column in actual_columns}
    if actual_names != expected_names:
        raise TransferError(f"Destination column inventory mismatch in {table}.")
    source_definition, source_columns = _sqlite_definition(source, table)
    target_by_name = {column[0]: column for column in actual_columns}
    for name, source_type in source_definition.columns:
        target_column = target_by_name[name]
        if not _compatible_types(source_type, target_column[1]):
            raise TransferError(
                f"Incompatible live PostgreSQL type for {table}.{name}: {target_column[1]}."
            )
    if source_definition.primary_key != actual["primary_key"]:
        raise TransferError(f"Destination primary-key mismatch in {table}.")
    if source_definition.foreign_keys != actual["foreign_keys"]:
        raise TransferError(f"Destination foreign-key mismatch in {table}.")
    if source_definition.unique_keys != actual["unique_keys"]:
        raise TransferError(f"Destination unique-constraint mismatch in {table}.")
    for name, _, not_null, primary_key_order in source_columns:
        target = next(column for column in actual_columns if column[0] == name.lower())
        target_not_null = target[2] == "NO"
        if bool(not_null or primary_key_order) != target_not_null:
            raise TransferError(
                f"Destination nullability mismatch for {table}.{name}."
            )
        if primary_key_order and name.lower() == "id" and target[3] != "YES":
            raise TransferError(
                f"Destination identity column is missing for {table}.{name}."
            )


def source_sequence_values(source):
    """Read SQLite AUTOINCREMENT high-water marks, including deleted IDs."""
    sequence_table = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
    ).fetchone()
    if sequence_table is None:
        return {}
    application_tables = set(load_migration_schema())
    sequences = {}
    for row in source.execute("SELECT name, seq FROM sqlite_sequence"):
        if row["name"] not in application_tables:
            continue
        try:
            sequence = int(row["seq"])
        except (TypeError, ValueError, OverflowError):
            raise TransferError(
                f"SQLite sequence state is invalid for {row['name']}."
            ) from None
        if sequence < 0:
            raise TransferError(
                f"SQLite sequence state is negative for {row['name']}."
            )
        sequences[row["name"]] = sequence
    return sequences


class PsycopgDestination:
    """Psycopg-backed transfer operations; all data writes use the caller transaction."""

    def __init__(self, connection):
        self.connection = connection

    def transaction(self):
        return self._locked_transaction()

    @contextmanager
    def _locked_transaction(self):
        with self.connection.transaction():
            self.connection.execute(
                "SELECT pg_advisory_xact_lock(%s)", (721946315028413,)
            )
            yield

    def verify_migration_ledger(self, schema, migrations):
        from psycopg import sql

        ledgers = ("sumak_prod_schema_migrations", "sumak_schema_migrations")
        ledger = None
        for candidate in ledgers:
            found = self.connection.execute(
                """SELECT 1 FROM information_schema.tables
                   WHERE table_schema = %s AND table_name = %s""",
                (schema, candidate),
            ).fetchone()
            if found:
                ledger = candidate
                break
        if ledger is None:
            raise TransferError(
                "Destination has no reviewed PostgreSQL migration ledger; apply migrations separately first."
            )
        identifier = sql.Identifier(schema, ledger)
        applied = {
            row[0]: row[1].strip()
            for row in self.connection.execute(
                sql.SQL("SELECT version, checksum FROM {}").format(identifier)
            ).fetchall()
        }
        expected = {migration.version: migration.checksum for migration in migrations}
        if applied != expected:
            raise TransferError(
                "Destination migration ledger is incomplete or does not match migrations 0001-0008."
            )

    def application_tables(self, schema):
        rows = self.connection.execute(
            """SELECT table_name FROM information_schema.tables
               WHERE table_schema = %s AND table_type = 'BASE TABLE'
               AND table_name NOT IN (%s, %s)
               ORDER BY table_name""",
            (schema, "sumak_schema_migrations", "sumak_prod_schema_migrations"),
        ).fetchall()
        return [row[0] for row in rows]

    def table_definition(self, schema, table):
        from psycopg import sql

        columns = self.connection.execute(
            """SELECT column_name, data_type, is_nullable, is_identity
               FROM information_schema.columns
               WHERE table_schema = %s AND table_name = %s
               ORDER BY ordinal_position""",
            (schema, table),
        ).fetchall()
        primary_key = self.connection.execute(
            """SELECT kcu.column_name
               FROM information_schema.table_constraints tc
               JOIN information_schema.key_column_usage kcu
                 ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
                AND tc.table_name = kcu.table_name
               WHERE tc.constraint_type = 'PRIMARY KEY'
                 AND tc.table_schema = %s AND tc.table_name = %s
               ORDER BY kcu.ordinal_position""",
            (schema, table),
        ).fetchall()
        foreign_rows = self.connection.execute(
            """SELECT con.oid, source_key.ordinality,
                      source_column.attname, parent_table.relname,
                      target_column.attname,
                      CASE con.confupdtype
                        WHEN 'r' THEN 'restrict' WHEN 'c' THEN 'cascade'
                        WHEN 'n' THEN 'set null' WHEN 'd' THEN 'set default'
                        ELSE 'no action' END,
                      CASE con.confdeltype
                        WHEN 'r' THEN 'restrict' WHEN 'c' THEN 'cascade'
                        WHEN 'n' THEN 'set null' WHEN 'd' THEN 'set default'
                        ELSE 'no action' END
               FROM pg_constraint con
               JOIN pg_class source_table ON source_table.oid = con.conrelid
               JOIN pg_namespace source_schema
                 ON source_schema.oid = source_table.relnamespace
               JOIN pg_class parent_table ON parent_table.oid = con.confrelid
               JOIN LATERAL unnest(con.conkey) WITH ORDINALITY
                    AS source_key(attnum, ordinality) ON true
               JOIN LATERAL unnest(con.confkey) WITH ORDINALITY
                    AS target_key(attnum, ordinality)
                 ON target_key.ordinality = source_key.ordinality
               JOIN pg_attribute source_column
                 ON source_column.attrelid = source_table.oid
                AND source_column.attnum = source_key.attnum
               JOIN pg_attribute target_column
                 ON target_column.attrelid = parent_table.oid
                AND target_column.attnum = target_key.attnum
               WHERE con.contype = 'f'
                 AND source_schema.nspname = %s
                 AND source_table.relname = %s
               ORDER BY con.oid, source_key.ordinality""",
            (schema, table),
        ).fetchall()
        grouped = {}
        for row in foreign_rows:
            grouped.setdefault(row[0], []).append(row)
        foreign_keys = []
        for rows in grouped.values():
            foreign_keys.append(
                (
                    tuple(row[2].lower() for row in rows),
                    rows[0][3].lower(),
                    tuple(row[4].lower() for row in rows),
                    rows[0][5].casefold(),
                    rows[0][6].casefold(),
                )
            )
        unique_rows = self.connection.execute(
            """SELECT array_agg(a.attname ORDER BY key_column.ordinality)
               FROM pg_index i
               JOIN pg_class t ON t.oid = i.indrelid
               JOIN pg_namespace n ON n.oid = t.relnamespace
               JOIN LATERAL unnest(i.indkey) WITH ORDINALITY
                    AS key_column(attnum, ordinality) ON true
               JOIN pg_attribute a
                 ON a.attrelid = t.oid AND a.attnum = key_column.attnum
               WHERE i.indisunique AND NOT i.indisprimary
                 AND i.indpred IS NULL AND n.nspname = %s AND t.relname = %s
               GROUP BY i.indexrelid""",
            (schema, table),
        ).fetchall()
        return {
            "columns": tuple(
                (row[0].lower(), row[1], row[2], row[3]) for row in columns
            ),
            "primary_key": tuple(row[0].lower() for row in primary_key),
            "foreign_keys": tuple(sorted(foreign_keys)),
            "unique_keys": tuple(
                sorted(tuple(column.lower() for column in row[0]) for row in unique_rows)
            ),
        }

    def row_count(self, schema, table):
        from psycopg import sql

        return self.connection.execute(
            sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(schema, table))
        ).fetchone()[0]

    def insert_rows(self, schema, table, columns, rows):
        from psycopg import sql

        statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(schema, table),
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        )
        cursor = self.connection.cursor()
        while True:
            batch = []
            try:
                for _ in range(500):
                    batch.append(next(rows))
            except StopIteration:
                pass
            if not batch:
                break
            cursor.executemany(statement, batch)

    def advance_sequence(self, schema, table, primary_key, source_high_water=None):
        from psycopg import sql

        if len(primary_key) != 1 or primary_key[0] != "id":
            return
        max_id = self.connection.execute(
            sql.SQL("SELECT MAX(id) FROM {}").format(sql.Identifier(schema, table))
        ).fetchone()[0]
        highest_id = max(int(max_id or 0), int(source_high_water or 0))
        next_id = max(1, highest_id + 1)
        self.connection.execute(
            sql.SQL("ALTER TABLE {} ALTER COLUMN id RESTART WITH {}").format(
                sql.Identifier(schema, table),
                sql.Literal(next_id),
            )
        )

    def critical_totals(self, schema, table, columns, stock_keys):
        from psycopg import sql

        identifier = sql.Identifier(schema, table)
        if columns:
            keys, measure = columns
            selected = sql.SQL(", ").join(sql.Identifier(key) for key in keys)
            return [
                tuple(row)
                for row in self.connection.execute(
                    sql.SQL(
                        "SELECT {}, COALESCE(SUM({}), 0) FROM {} "
                        "GROUP BY {} ORDER BY {}"
                    ).format(
                        selected,
                        sql.Identifier(measure),
                        identifier,
                        selected,
                        selected,
                    )
                ).fetchall()
            ]
        select_keys = sql.SQL(", ").join(sql.Identifier(key) for key in stock_keys)
        result = self.connection.execute(
            sql.SQL(
                "SELECT {}, COALESCE(SUM(quantity), 0) FROM {} "
                "GROUP BY {} ORDER BY {}"
            ).format(select_keys, identifier, select_keys, select_keys)
        ).fetchall()
        return [tuple(row) for row in result]


def create_read_only_source(path):
    """Open a SQLite source without write capability and enable query-only mode."""
    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file():
        raise TransferError("SQLite source file does not exist.")
    source = None
    try:
        source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        source.execute("PRAGMA query_only = ON")
        source.execute("PRAGMA foreign_keys = ON")
        source.execute("BEGIN")
        return source
    except sqlite3.Error:
        if source is not None:
            source.close()
        raise


def _offline_plan(source):
    counts, order = inspect_source(source)
    sequences = source_sequence_values(source)
    return {
        "mode": "offline-dry-run",
        "table_count": len(counts),
        "tables": [
            {
                "name": table,
                "rows": counts[table],
                "sqlite_sequence_high_water": sequences.get(table),
            }
            for table in order
        ],
        "write_performed": False,
        "postgresql_validation": "not performed; no destination connection was made",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="SQLite source database path")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--dry-run", action="store_true", help="Validate source and print an offline transfer plan")
    operation.add_argument("--apply", action="store_true", help="Transfer into an explicitly guarded disposable PostgreSQL database")
    args = parser.parse_args(argv)

    try:
        source = create_read_only_source(args.source)
    except (OSError, sqlite3.Error, TransferError) as error:
        raise SystemExit(str(error)) from None

    try:
        if args.dry_run:
            try:
                print(json.dumps(_offline_plan(source), indent=2))
            except (sqlite3.Error, TransferError) as error:
                raise SystemExit(f"SQLite transfer preflight failed: {error}") from None
            return

        try:
            target = get_transfer_target()
        except UnsafeTransferTargetError as error:
            raise SystemExit(str(error)) from None
        try:
            import psycopg
        except ImportError:
            raise SystemExit(
                "PostgreSQL data transfer requires the project Psycopg dependency."
            ) from None
        try:
            with psycopg.connect(target.database_url) as connection:
                connection.execute(
                    "SELECT set_config('search_path', %s, true)", (target.schema,)
                )
                actual_database = connection.execute(
                    "SELECT current_database()"
                ).fetchone()[0]
                if actual_database.casefold() != target.database:
                    raise UnsafeTransferTargetError(
                        "Connected database does not match TRANSFER_DATABASE_NAME."
                    )
                report = transfer_database(
                    source, PsycopgDestination(connection), target.schema
                )
        except UnsafeTransferTargetError as error:
            raise SystemExit(str(error)) from None
        except (TransferError, sqlite3.Error) as error:
            raise SystemExit(
                "SQLite-to-PostgreSQL transfer stopped; the source was not modified "
                "and any active destination transaction was rolled back. "
                f"Details: {error}"
            ) from None
        except psycopg.Error:
            raise SystemExit(
                "PostgreSQL data transfer failed and the destination transaction was rolled back; database details were withheld."
            ) from None
        print(
            json.dumps(
                {
                    "mode": "staging-transfer",
                    "table_count": len(report.source_counts),
                    "tables": [
                        {
                            "name": table,
                            "source_rows": report.source_counts[table],
                            "destination_rows": report.destination_counts[table],
                        }
                        for table in report.table_order
                    ],
                    "critical_totals_verified": sorted(report.verified_totals),
                    "committed": True,
                },
                indent=2,
            )
        )
    finally:
        source.close()


if __name__ == "__main__":
    main()
