import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from production_postgres_migrations import (
    UnsafeProductionTargetError,
    get_production_migration_config,
    main,
)


ROOT = Path(__file__).resolve().parents[1]
VALID_ENV = {
    "ALLOW_PRODUCTION_DATABASE_MIGRATIONS": "YES",
    "DATABASE_URL": (
        "postgresql://erp_user:secret@db.example.com:5432/sumakerp"
        "?sslmode=require"
    ),
    "PRODUCTION_DATABASE_HOST_ALLOWLIST": "db.example.com",
    "PRODUCTION_DATABASE_NAME": "sumakerp",
    "PRODUCTION_DATABASE_SCHEMA": "public",
}


class ProductionMigrationConfigurationTests(unittest.TestCase):
    def test_accepts_only_explicitly_confirmed_target(self):
        config = get_production_migration_config(VALID_ENV)

        self.assertEqual(config.host, "db.example.com")
        self.assertEqual(config.database, "sumakerp")
        self.assertEqual(config.schema, "public")

    def test_requires_explicit_opt_in(self):
        env = {**VALID_ENV, "ALLOW_PRODUCTION_DATABASE_MIGRATIONS": "NO"}
        with self.assertRaises(UnsafeProductionTargetError):
            get_production_migration_config(env)

    def test_requires_database_url(self):
        env = {**VALID_ENV, "DATABASE_URL": ""}
        with self.assertRaises(UnsafeProductionTargetError):
            get_production_migration_config(env)

    def test_rejects_sqlite_and_malformed_postgres_urls(self):
        for url in (
            "sqlite:///sumakerp.db",
            "postgresql://db.example.com:99999/sumakerp",
            "postgresql://db.example.com/sumakerp/extra",
            "postgresql://db.example.com/sumakerp#fragment",
            "postgresql://db.example.com/sumakerp?sslmode=disable",
        ):
            with self.subTest(url=url):
                env = {**VALID_ENV, "DATABASE_URL": url}
                with self.assertRaises(UnsafeProductionTargetError):
                    get_production_migration_config(env)

    def test_requires_exact_host_database_and_schema_confirmation(self):
        cases = (
            {"PRODUCTION_DATABASE_HOST_ALLOWLIST": ""},
            {"PRODUCTION_DATABASE_HOST_ALLOWLIST": "other.example.com"},
            {"PRODUCTION_DATABASE_NAME": "otherdb"},
            {"PRODUCTION_DATABASE_SCHEMA": ""},
        )
        for override in cases:
            with self.subTest(override=override):
                env = {**VALID_ENV, **override}
                with self.assertRaises(UnsafeProductionTargetError):
                    get_production_migration_config(env)

    def test_rejects_development_and_test_database_markers(self):
        env = {
            **VALID_ENV,
            "DATABASE_URL": "postgresql://db.example.com/sumakerp-staging",
            "PRODUCTION_DATABASE_NAME": "sumakerp-staging",
        }
        with self.assertRaises(UnsafeProductionTargetError):
            get_production_migration_config(env)

    def test_command_refuses_missing_configuration_before_connecting(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("psycopg.connect") as connect:
                with self.assertRaises(SystemExit):
                    main()
                connect.assert_not_called()


class ApplicationImportSafetyTests(unittest.TestCase):
    def test_importing_app_does_not_initialize_schema_or_migrations(self):
        script = """
import sys
import database

def unexpected_schema_initialization():
    raise AssertionError("schema initialization ran during app import")

database.create_tables = unexpected_schema_initialization
import app
assert "production_postgres_migrations" not in sys.modules
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env={**os.environ, "APP_ENV": "development", "FLASK_SECRET_KEY": ""},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
