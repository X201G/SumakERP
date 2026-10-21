"""Versioned PostgreSQL migrations and guarded disposable-test-database runner.

This runner is deliberately separate from database.create_tables(), which
initializes the SQLite development database. It only supports the explicitly
configured test target until a complete production schema baseline is reviewed.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


MIGRATIONS_DIR = Path(__file__).parent / "migrations" / "postgres"
MIGRATION_LOCK_ID = 721946315028410
MIGRATION_TABLE = "sumak_schema_migrations"
_MIGRATION_FILE_PATTERN = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_NON_PRODUCTION_MARKER = re.compile(r"(?:^|[-_.])(test|testing|stage|staging|dev|ci)(?:$|[-_.])")
_PRODUCTION_MARKER = re.compile(r"(?:^|[-_.])(prod|production|live)(?:$|[-_.])")


class MigrationError(RuntimeError):
    """Base error for PostgreSQL migration failures."""


class MigrationChecksumError(MigrationError):
    """Raised when an applied migration no longer matches its checksum."""


class MissingMigrationError(MigrationError):
    """Raised when the database records a migration absent from this checkout."""


class UnsafeMigrationTargetError(MigrationError):
    """Raised when migration configuration does not identify a test database."""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str
    checksum: str


def get_test_database_url(environ=None):
    """Return TEST_DATABASE_URL only when explicit non-production guards pass."""
    env = os.environ if environ is None else environ
    database_url = env.get("TEST_DATABASE_URL", "").strip()
    if not database_url:
        raise UnsafeMigrationTargetError(
            "Set TEST_DATABASE_URL to a dedicated non-production database."
        )
    if env.get("ALLOW_TEST_DATABASE_MIGRATIONS", "").strip() != "YES":
        raise UnsafeMigrationTargetError(
            "Set ALLOW_TEST_DATABASE_MIGRATIONS=YES to permit test migrations."
        )

    try:
        parsed = urlsplit(database_url)
        port = parsed.port
    except ValueError:
        raise UnsafeMigrationTargetError(
            "TEST_DATABASE_URL is not a valid PostgreSQL URL."
        ) from None

    if (
        parsed.scheme not in ("postgres", "postgresql")
        or not parsed.hostname
        or not parsed.path.strip("/")
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise UnsafeMigrationTargetError(
            "TEST_DATABASE_URL must be a PostgreSQL URL with a host and database name."
        )

    host = parsed.hostname.casefold()
    database_name = parsed.path.strip("/").casefold()
    if _PRODUCTION_MARKER.search(host) or _PRODUCTION_MARKER.search(database_name):
        raise UnsafeMigrationTargetError(
            "Production-like PostgreSQL targets are not permitted for test migrations."
        )

    allowlisted_hosts = {
        item.strip().casefold()
        for item in env.get("TEST_DATABASE_HOST_ALLOWLIST", "").split(",")
        if item.strip()
    }
    if (
        not _NON_PRODUCTION_MARKER.search(database_name)
        and not _NON_PRODUCTION_MARKER.search(host)
        and host not in allowlisted_hosts
    ):
        raise UnsafeMigrationTargetError(
            "The test database name or host must identify a non-production target."
        )
    return database_url


def load_migrations(migrations_dir=MIGRATIONS_DIR):
    """Load ordered SQL files and calculate SHA-256 checksums over their bytes."""
    migrations = []
    seen_versions = set()
    for path in sorted(Path(migrations_dir).glob("*.sql")):
        match = _MIGRATION_FILE_PATTERN.fullmatch(path.name)
        if not match:
            raise MigrationError(f"Invalid PostgreSQL migration filename: {path.name}")
        version = match.group(1)
        if version in seen_versions:
            raise MigrationError(f"Duplicate PostgreSQL migration version: {version}")
        seen_versions.add(version)
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


def apply_postgres_migrations(
    connection, migrations_dir=MIGRATIONS_DIR, schema=None
):
    """Apply migrations in one schema under a transaction-scoped advisory lock."""
    from psycopg import sql

    migrations = load_migrations(migrations_dir)
    versions = {migration.version for migration in migrations}
    ledger = None

    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
        target_schema = schema
        if target_schema is None:
            target_schema = connection.execute(
                "SELECT current_schema()"
            ).fetchone()[0]
        if not target_schema:
            raise MigrationError("PostgreSQL migration target schema is not set.")
        ledger = sql.Identifier(target_schema, MIGRATION_TABLE)
        connection.execute(
            sql.SQL("SET LOCAL search_path TO {}").format(
                sql.Identifier(target_schema)
            )
        )
        connection.execute(
            sql.SQL("""CREATE TABLE IF NOT EXISTS {} (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                checksum CHAR(64) NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""").format(ledger)
        )
        applied_versions = {
            row[0]
            for row in connection.execute(
                sql.SQL("SELECT version FROM {}").format(ledger)
            ).fetchall()
        }
        missing = applied_versions - versions
        if missing:
            raise MissingMigrationError(
                "Database contains PostgreSQL migrations missing from this checkout: "
                + ", ".join(sorted(missing))
            )

    applied = []
    for migration in migrations:
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,)
            )
            connection.execute(
                sql.SQL("SET LOCAL search_path TO {}").format(
                    sql.Identifier(target_schema)
                )
            )
            row = connection.execute(
                sql.SQL("SELECT checksum FROM {} WHERE version = %s").format(ledger),
                (migration.version,),
            ).fetchone()
            if row is not None:
                if row[0].strip() != migration.checksum:
                    raise MigrationChecksumError(
                        f"Applied PostgreSQL migration {migration.version} "
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
    """Apply the current slice only to the guarded TEST_DATABASE_URL target."""
    try:
        database_url = get_test_database_url()
        import psycopg
    except (UnsafeMigrationTargetError, ImportError):
        raise SystemExit(
            "PostgreSQL test migrations require a guarded TEST_DATABASE_URL "
            "and the project Psycopg dependency."
        ) from None

    try:
        with psycopg.connect(database_url) as connection:
            applied = apply_postgres_migrations(connection)
    except psycopg.Error:
        raise SystemExit(
            "PostgreSQL test migrations failed; database details were withheld."
        ) from None

    if applied:
        print("Applied PostgreSQL test migrations: " + ", ".join(applied))
    else:
        print("PostgreSQL test migrations are already current.")


if __name__ == "__main__":
    main()
