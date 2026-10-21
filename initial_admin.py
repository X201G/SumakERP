"""Explicitly provision one system-scoped administrator in PostgreSQL."""

import argparse
from dataclasses import dataclass
import getpass
import os
import re
import sys
from urllib.parse import parse_qsl, urlsplit

from werkzeug.security import generate_password_hash


SUPPORTED_SYSTEMS = {"distribution", "manufacturing", "projects", "hiring", "delivery"}


class AdminProvisioningError(RuntimeError):
    """Raised when initial-admin provisioning cannot safely proceed."""


@dataclass(frozen=True)
class AdminProvisioningTarget:
    database_url: str
    host: str
    database: str
    schema: str


def get_admin_provisioning_target(environ=None):
    """Require explicit URL, host/database confirmations, TLS, and opt-in."""
    env = os.environ if environ is None else environ
    if env.get("ALLOW_INITIAL_ADMIN_PROVISIONING", "").strip() != "YES":
        raise AdminProvisioningError(
            "Set ALLOW_INITIAL_ADMIN_PROVISIONING=YES to explicitly enable provisioning."
        )
    database_url = env.get("ADMIN_PROVISIONING_DATABASE_URL", "").strip()
    if not database_url:
        raise AdminProvisioningError(
            "Set ADMIN_PROVISIONING_DATABASE_URL to the intended PostgreSQL target."
        )
    try:
        parsed = urlsplit(database_url)
        port = parsed.port
    except ValueError:
        raise AdminProvisioningError(
            "ADMIN_PROVISIONING_DATABASE_URL is not a valid PostgreSQL URL."
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
        raise AdminProvisioningError(
            "ADMIN_PROVISIONING_DATABASE_URL must include a PostgreSQL host and database."
        )
    if dict(parse_qsl(parsed.query)).get("sslmode", "").casefold() not in {
        "require",
        "verify-ca",
        "verify-full",
    }:
        raise AdminProvisioningError(
            "ADMIN_PROVISIONING_DATABASE_URL must require TLS."
        )

    host = parsed.hostname.casefold()
    database = parsed.path[1:].casefold()
    hosts = {
        value.strip().casefold()
        for value in env.get("ADMIN_PROVISIONING_HOST_ALLOWLIST", "").split(",")
        if value.strip()
    }
    expected_database = env.get(
        "ADMIN_PROVISIONING_DATABASE_NAME", ""
    ).strip().casefold()
    schema = env.get("ADMIN_PROVISIONING_SCHEMA", "").strip()
    if not hosts or host not in hosts:
        raise AdminProvisioningError(
            "ADMIN_PROVISIONING_HOST_ALLOWLIST must contain the exact database host."
        )
    if not expected_database or database != expected_database:
        raise AdminProvisioningError(
            "ADMIN_PROVISIONING_DATABASE_NAME must exactly match the URL database."
        )
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", schema):
        raise AdminProvisioningError(
            "ADMIN_PROVISIONING_SCHEMA must be a valid explicit schema name."
        )
    return AdminProvisioningTarget(database_url, host, database, schema)


def provision_initial_admin(
    connection, expected_database, schema, username, full_name, system, password
):
    """Create an admin only if the selected portal has no existing admin."""
    if system not in SUPPORTED_SYSTEMS:
        raise AdminProvisioningError("Unsupported system.")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", schema):
        raise AdminProvisioningError("Invalid schema name.")
    username = username.strip()
    full_name = full_name.strip()
    if not username or not full_name:
        raise AdminProvisioningError("Username and full name are required.")
    if len(password) < 12:
        raise AdminProvisioningError("The password must contain at least 12 characters.")

    with connection.transaction():
        actual_database = connection.execute("SELECT current_database()").fetchone()[0]
        if actual_database.casefold() != expected_database.casefold():
            raise AdminProvisioningError(
                "Connected database does not match ADMIN_PROVISIONING_DATABASE_NAME."
            )
        connection.execute(
            "SELECT pg_advisory_xact_lock(%s)", (721946315028415,)
        )
        schema_exists = connection.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)
        ).fetchone()
        if schema_exists is None:
            raise AdminProvisioningError(
                "ADMIN_PROVISIONING_SCHEMA does not exist in the target database."
            )
        users_table_exists = connection.execute(
            """SELECT 1 FROM information_schema.tables
               WHERE table_schema = %s AND table_name = 'users'""",
            (schema,),
        ).fetchone()
        if users_table_exists is None:
            raise AdminProvisioningError(
                "The selected schema has no migrated users table."
            )
        connection.execute(f'SET LOCAL search_path TO "{schema}"')
        existing = connection.execute(
            "SELECT 1 FROM users WHERE system = %s AND role = 'admin' LIMIT 1",
            (system,),
        ).fetchone()
        if existing is not None:
            raise AdminProvisioningError(
                "An administrator already exists for the selected system."
            )
        connection.execute(
            """INSERT INTO users
               (username, password_hash, full_name, role, system, is_active)
               VALUES (%s, %s, %s, 'admin', %s, 1)""",
            (username, generate_password_hash(password), full_name, system),
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--full-name", required=True)
    parser.add_argument("--system", required=True, choices=sorted(SUPPORTED_SYSTEMS))
    args = parser.parse_args(argv)
    try:
        target = get_admin_provisioning_target()
    except AdminProvisioningError as error:
        raise SystemExit(str(error)) from None
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit(
            "Administrator provisioning requires an interactive terminal for password entry."
        )
    password = getpass.getpass("New administrator password: ")
    confirmation = getpass.getpass("Confirm administrator password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    if len(password) < 12:
        raise SystemExit("The password must contain at least 12 characters.")
    try:
        import psycopg
    except ImportError:
        raise SystemExit(
            "Administrator provisioning requires the project Psycopg dependency."
        ) from None
    try:
        with psycopg.connect(target.database_url) as connection:
            provision_initial_admin(
                connection,
                target.database,
                target.schema,
                args.username,
                args.full_name,
                args.system,
                password,
            )
    except AdminProvisioningError as error:
        raise SystemExit(str(error)) from None
    except psycopg.Error:
        raise SystemExit(
            "Administrator provisioning failed; database details were withheld."
        ) from None
    print(f"Administrator provisioned for the {args.system} system.")


if __name__ == "__main__":
    main()
