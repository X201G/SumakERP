"""Explicitly invoked, guarded PostgreSQL production migration command."""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


MIGRATIONS_DIR = Path(__file__).parent / "migrations" / "postgres"
MIGRATION_LOCK_ID = 721946315028412
MIGRATION_TABLE = "sumak_prod_schema_migrations"
_MIGRATION_FILE_PATTERN = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_CREATE_TABLE_PATTERN = re.compile(
    r"^\s*CREATE\s+TABLE\s+([A-Za-z_][A-Za-z_0-9]*)\b",
    re.IGNORECASE | re.MULTILINE,
)
_NON_PRODUCTION_MARKER = re.compile(
    r"(?:^|[-_.])(test|testing|stage|staging|dev|development|ci)(?:$|[-_.])"
)


class ProductionMigrationError(RuntimeError):
    """Base error for explicit production migration failures."""


class UnsafeProductionTargetError(ProductionMigrationError):
    """Raised when production migration target confirmation is incomplete."""


class ProductionMigrationChecksumError(ProductionMigrationError):
    """Raised when an applied production migration checksum has changed."""


class MissingProductionMigrationError(ProductionMigrationError):
    """Raised when the database records a migration absent from this checkout."""


class ExistingProductionSchemaError(ProductionMigrationError):
    """Raised when application tables exist without the production ledger."""


@dataclass(frozen=True)
class ProductionMigrationConfig:
    database_url: str
    host: str
    database: str
    schema: str


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str
    checksum: str


def get_production_migration_config(environ=None):
    """Validate explicit opt-in and exact host/database/schema confirmations."""
    env = os.environ if environ is None else environ
    if env.get("ALLOW_PRODUCTION_DATABASE_MIGRATIONS", "").strip() != "YES":
        raise UnsafeProductionTargetError(
            "Set ALLOW_PRODUCTION_DATABASE_MIGRATIONS=YES to explicitly enable production migrations."
        )

    database_url = env.get("DATABASE_URL", "").strip()
    if not database_url:
        raise UnsafeProductionTargetError(
            "Set DATABASE_URL to the intended PostgreSQL production database."
        )

    try:
        parsed = urlsplit(database_url)
        port = parsed.port
    except ValueError:
        raise UnsafeProductionTargetError(
            "DATABASE_URL is not a valid PostgreSQL connection URL."
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
        raise UnsafeProductionTargetError(
            "DATABASE_URL must be a valid PostgreSQL URL with a host and database name."
        )

    host = parsed.hostname.casefold()
    database = parsed.path.strip("/").casefold()
    sslmode = dict(parse_qsl(parsed.query)).get("sslmode", "").casefold()
    if sslmode not in {"require", "verify-ca", "verify-full"}:
        raise UnsafeProductionTargetError(
            "DATABASE_URL must require TLS with sslmode=require, verify-ca, or verify-full."
        )
    schema = env.get("PRODUCTION_DATABASE_SCHEMA", "").strip()
    if not schema or "\x00" in schema:
        raise UnsafeProductionTargetError(
            "Set PRODUCTION_DATABASE_SCHEMA to the exact migration schema."
        )

    allowlisted_hosts = {
        item.strip().casefold()
        for item in env.get("PRODUCTION_DATABASE_HOST_ALLOWLIST", "").split(",")
        if item.strip()
    }
    expected_database = env.get("PRODUCTION_DATABASE_NAME", "").strip().casefold()
    if not allowlisted_hosts or host not in allowlisted_hosts:
        raise UnsafeProductionTargetError(
            "PRODUCTION_DATABASE_HOST_ALLOWLIST must contain the exact production host."
        )
    if not expected_database or database != expected_database:
        raise UnsafeProductionTargetError(
            "PRODUCTION_DATABASE_NAME must exactly match the DATABASE_URL database."
        )
    if _NON_PRODUCTION_MARKER.search(host) or _NON_PRODUCTION_MARKER.search(database):
        raise UnsafeProductionTargetError(
            "Test, staging, development, and CI targets are not production migration targets."
        )

    return ProductionMigrationConfig(database_url, host, database, schema)


def load_production_migrations(migrations_dir=MIGRATIONS_DIR):
    """Load ordered migration SQL and calculate checksums over the source bytes."""
    migrations = []
    versions = set()
    for path in sorted(Path(migrations_dir).glob("*.sql")):
        match = _MIGRATION_FILE_PATTERN.fullmatch(path.name)
        if not match:
            raise ProductionMigrationError(
                f"Invalid PostgreSQL migration filename: {path.name}"
            )
        version = match.group(1)
        if version in versions:
            raise ProductionMigrationError(
                f"Duplicate PostgreSQL migration version: {version}"
            )
        versions.add(version)
        content = path.read_bytes()
        migrations.append(
            Migration(
                version=version,
                name=path.stem,
                sql=content.decode("utf-8"),
                checksum=hashlib.sha256(content).hexdigest(),
            )
        )
    return migrations


def apply_production_migrations(
    connection,
    schema,
    expected_database,
    migrations_dir=MIGRATIONS_DIR,
):
    """Apply checksummed migrations under a production-specific advisory lock."""
    from psycopg import sql

    migrations = load_production_migrations(migrations_dir)
    versions = {migration.version for migration in migrations}
    expected_tables = sorted(
        {
            table
            for migration in migrations
            for table in _CREATE_TABLE_PATTERN.findall(migration.sql)
        }
    )
    ledger = sql.Identifier(schema, MIGRATION_TABLE)

    with connection.transaction():
        connection.execute(
            "SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,)
        )
        actual_database = connection.execute(
            "SELECT current_database()"
        ).fetchone()[0]
        if actual_database.casefold() != expected_database.casefold():
            raise UnsafeProductionTargetError(
                "Connected database does not match PRODUCTION_DATABASE_NAME."
            )
        schema_exists = connection.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)
        ).fetchone()
        if schema_exists is None:
            raise UnsafeProductionTargetError(
                "PRODUCTION_DATABASE_SCHEMA does not exist in the target database."
            )
        ledger_exists = connection.execute(
            """SELECT 1 FROM information_schema.tables
               WHERE table_schema = %s AND table_name = %s""",
            (schema, MIGRATION_TABLE),
        ).fetchone()
        test_ledger_exists = connection.execute(
            """SELECT 1 FROM information_schema.tables
               WHERE table_schema = %s AND table_name = %s""",
            (schema, "sumak_schema_migrations"),
        ).fetchone()
        if test_ledger_exists is not None and ledger_exists is None:
            raise ExistingProductionSchemaError(
                "A test migration ledger exists in this schema; production ledger adoption requires a reviewed procedure."
            )
        if ledger_exists is None:
            existing_tables = connection.execute(
                """SELECT table_name FROM information_schema.tables
                   WHERE table_schema = %s AND table_name = ANY(%s)""",
                (schema, expected_tables),
            ).fetchall()
            if existing_tables:
                raise ExistingProductionSchemaError(
                    "Application tables already exist without the production migration ledger; "
                    "review schema and ledger adoption before proceeding."
                )
        connection.execute(
            sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
        )
        connection.execute(
            sql.SQL(
                """CREATE TABLE IF NOT EXISTS {} (
                    version TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum CHAR(64) NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            ).format(ledger)
        )
        applied_versions = {
            row[0]
            for row in connection.execute(
                sql.SQL("SELECT version FROM {}").format(ledger)
            ).fetchall()
        }
        missing = applied_versions - versions
        if missing:
            raise MissingProductionMigrationError(
                "Database contains production migrations missing from this checkout: "
                + ", ".join(sorted(missing))
            )

    applied = []
    for migration in migrations:
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,)
            )
            connection.execute(
                sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
            )
            row = connection.execute(
                sql.SQL("SELECT checksum FROM {} WHERE version = %s").format(ledger),
                (migration.version,),
            ).fetchone()
            if row is not None:
                if row[0].strip() != migration.checksum:
                    raise ProductionMigrationChecksumError(
                        f"Applied production migration {migration.version} "
                        "does not match its recorded checksum."
                    )
                continue

            connection.execute(migration.sql)
            connection.execute(
                sql.SQL(
                    """INSERT INTO {} (version, name, checksum)
                       VALUES (%s, %s, %s)"""
                ).format(ledger),
                (migration.version, migration.name, migration.checksum),
            )
            applied.append(migration.version)
    return applied


def main():
    """Run only when explicitly invoked as ``python -m production_postgres_migrations``."""
    try:
        config = get_production_migration_config()
    except UnsafeProductionTargetError as error:
        raise SystemExit(str(error)) from None

    try:
        import psycopg
    except ImportError:
        raise SystemExit(
            "Production PostgreSQL migrations require the project Psycopg dependency."
        ) from None

    try:
        with psycopg.connect(config.database_url) as connection:
            applied = apply_production_migrations(
                connection,
                config.schema,
                config.database,
            )
    except psycopg.Error:
        raise SystemExit(
            "Production PostgreSQL migration failed; database details were withheld."
        ) from None

    if applied:
        print("Applied production PostgreSQL migrations: " + ", ".join(applied))
    else:
        print("Production PostgreSQL migrations are already current.")


if __name__ == "__main__":
    main()
