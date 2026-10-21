import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import database


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = None

    def execute(self, query, params=None):
        if self.connection.fail_next_query:
            self.connection.fail_next_query = False
            self.connection.transaction_failed = True
            raise RuntimeError("simulated query failure")
        return self


class FakePostgresConnection:
    def __init__(self):
        self.commit_count = 0
        self.rollback_count = 0
        self.transaction_failed = False
        self.fail_next_query = False
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def execute(self, query, params=None):
        return self.cursor().execute(query, params)

    def commit(self):
        self.commit_count += 1
        self.transaction_failed = False

    def rollback(self):
        self.rollback_count += 1
        self.transaction_failed = False

    def close(self):
        self.closed = True


class FakeConnectionPool:
    instances = []
    open_error = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.connection = FakePostgresConnection()
        self.returned = []
        self.closed = False
        self.getconn_error = None
        self.putconn_fail_once = False
        type(self).instances.append(self)

    def open(self, **kwargs):
        self.open_kwargs = kwargs
        if type(self).open_error is not None:
            raise type(self).open_error

    def getconn(self, **kwargs):
        self.getconn_kwargs = kwargs
        if self.getconn_error is not None:
            raise self.getconn_error
        return self.connection

    def putconn(self, connection, **kwargs):
        if self.putconn_fail_once and not kwargs.get("close"):
            self.putconn_fail_once = False
            raise RuntimeError("simulated pool return failure")
        self.returned.append((connection, kwargs))

    def close(self):
        self.closed = True


class DatabaseConnectionTests(unittest.TestCase):
    POSTGRES_URL = "postgresql://dbuser:sensitive-test-password@db.example.test:5432/sumak"

    def setUp(self):
        self.previous_pool = database._postgres_pool
        self.previous_pool_url = database._postgres_pool_url
        self.previous_database = database.DATABASE
        database._postgres_pool = None
        database._postgres_pool_url = None
        FakeConnectionPool.instances = []
        FakeConnectionPool.open_error = None

    def tearDown(self):
        database._postgres_pool = self.previous_pool
        database._postgres_pool_url = self.previous_pool_url
        database.DATABASE = self.previous_database

    def use_fake_pool(self):
        return patch.dict(sys.modules, {"psycopg_pool": SimpleNamespace(ConnectionPool=FakeConnectionPool)})

    def test_default_connection_remains_sqlite_and_supports_existing_row_access(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE = os.path.join(temp_dir, "local.db")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("DATABASE_URL", None)
                connection = database.get_connection()
                self.assertIsInstance(connection, sqlite3.Connection)
                connection.execute("CREATE TABLE sample (id INTEGER, label TEXT)")
                connection.execute("INSERT INTO sample VALUES (?, ?)", (7, "seven"))
                connection.commit()
                connection.close()

                reopened = database.get_connection()
                row = reopened.execute("SELECT id, label FROM sample").fetchone()
                self.assertEqual(row[0], 7)
                self.assertEqual(row["label"], "seven")
                reopened.close()

    def test_postgres_row_supports_named_positional_and_value_iteration(self):
        cursor = SimpleNamespace(
            description=[
                SimpleNamespace(name="id"),
                SimpleNamespace(name="label"),
                SimpleNamespace(name="ID"),
            ]
        )
        row_factory = database._postgres_row_factory(cursor)
        self.assertTrue(callable(row_factory))
        row = row_factory((7, "seven", 9))

        self.assertEqual(row[0], 7)
        self.assertEqual(row[1], "seven")
        self.assertEqual(row["label"], "seven")
        self.assertEqual(row["LABEL"], "seven")
        self.assertEqual(row["ID"], 7)
        self.assertEqual(tuple(row), (7, "seven", 9))
        self.assertEqual(row.keys(), ["id", "label", "ID"])

    def test_postgres_row_factory_handles_commands_without_result_columns(self):
        cursor = SimpleNamespace(description=None)

        row_factory = database._postgres_row_factory(cursor)

        self.assertEqual(row_factory((7, "seven")), (7, "seven"))

    def test_postgres_acquisition_uses_pool_and_passes_hybrid_row_factory(self):
        with self.use_fake_pool(), patch.dict(
            os.environ, {"DATABASE_URL": self.POSTGRES_URL}
        ):
            connection = database.get_connection()
            pool = FakeConnectionPool.instances[-1]

            self.assertIsInstance(connection, database._PooledPostgresConnection)
            self.assertEqual(pool.kwargs["conninfo"], self.POSTGRES_URL)
            self.assertIs(pool.kwargs["kwargs"]["row_factory"], database._postgres_row_factory)
            self.assertEqual(pool.open_kwargs, {"wait": True, "timeout": database.POSTGRES_POOL_TIMEOUT})
            self.assertEqual(pool.getconn_kwargs, {"timeout": database.POSTGRES_POOL_TIMEOUT})

            connection.close()
            self.assertEqual(pool.connection.rollback_count, 1)
            self.assertEqual(pool.returned, [(pool.connection, {})])

    def test_explicit_postgres_url_is_validated_without_echoing_credentials(self):
        unsafe_url = "http://user:do-not-show@host/database"
        with patch.dict(os.environ, {"DATABASE_URL": unsafe_url}):
            with self.assertRaises(database.DatabaseConfigurationError) as raised:
                database.get_connection()

        self.assertNotIn("do-not-show", str(raised.exception))
        self.assertNotIn(unsafe_url, str(raised.exception))

    def test_missing_psycopg_fails_clearly_without_echoing_url(self):
        with patch.dict(sys.modules, {"psycopg_pool": None}), patch.dict(
            os.environ, {"DATABASE_URL": self.POSTGRES_URL}
        ):
            with self.assertRaises(database.DatabaseConfigurationError) as raised:
                database.get_connection()

        self.assertIn("Psycopg 3 pooling is unavailable", str(raised.exception))
        self.assertNotIn("sensitive-test-password", str(raised.exception))
        self.assertNotIn(self.POSTGRES_URL, str(raised.exception))

    def test_pool_connectivity_failure_is_sanitized_and_not_logged(self):
        FakeConnectionPool.open_error = RuntimeError(
            f"connection failed for {self.POSTGRES_URL}"
        )
        logger = logging.getLogger("database")
        with self.use_fake_pool(), patch.dict(
            os.environ, {"DATABASE_URL": self.POSTGRES_URL}
        ), self.assertNoLogs(logger, level="DEBUG"):
            with self.assertRaises(database.DatabaseConfigurationError) as raised:
                database.get_connection()

        self.assertNotIn("sensitive-test-password", str(raised.exception))
        self.assertNotIn(self.POSTGRES_URL, str(raised.exception))
        self.assertTrue(FakeConnectionPool.instances[-1].closed)

    def test_pool_acquisition_failure_is_sanitized(self):
        with self.use_fake_pool(), patch.dict(
            os.environ, {"DATABASE_URL": self.POSTGRES_URL}
        ):
            connection = database.get_connection()
            pool = FakeConnectionPool.instances[-1]
            connection.close()
            pool.getconn_error = RuntimeError(
                f"pool failed for {self.POSTGRES_URL}"
            )

            with self.assertRaises(database.DatabaseConfigurationError) as raised:
                database.get_connection()

        self.assertNotIn("sensitive-test-password", str(raised.exception))
        self.assertNotIn(self.POSTGRES_URL, str(raised.exception))

    def test_commit_is_explicit_and_close_rolls_back_before_pool_reuse(self):
        with self.use_fake_pool(), patch.dict(
            os.environ, {"DATABASE_URL": self.POSTGRES_URL}
        ):
            connection = database.get_connection()
            pool = FakeConnectionPool.instances[-1]
            raw_connection = pool.connection

            connection.commit()
            connection.close()
            self.assertEqual(raw_connection.commit_count, 1)
            self.assertEqual(raw_connection.rollback_count, 1)
            self.assertFalse(raw_connection.transaction_failed)

            next_connection = database.get_connection()
            self.assertIs(next_connection._connection, raw_connection)
            next_connection.close()
            self.assertEqual(raw_connection.rollback_count, 2)
            self.assertEqual(len(pool.returned), 2)

    def test_failed_query_is_rolled_back_before_connection_returns_to_pool(self):
        with self.use_fake_pool(), patch.dict(
            os.environ, {"DATABASE_URL": self.POSTGRES_URL}
        ):
            connection = database.get_connection()
            pool = FakeConnectionPool.instances[-1]
            pool.connection.fail_next_query = True

            with self.assertRaisesRegex(RuntimeError, "simulated query failure"):
                connection.execute("SELECT failure")
            self.assertTrue(pool.connection.transaction_failed)

            connection.close()
            self.assertFalse(pool.connection.transaction_failed)
            self.assertEqual(pool.connection.rollback_count, 1)
            self.assertEqual(pool.returned, [(pool.connection, {})])

    def test_rollback_failure_discards_connection(self):
        with self.use_fake_pool(), patch.dict(
            os.environ, {"DATABASE_URL": self.POSTGRES_URL}
        ):
            connection = database.get_connection()
            pool = FakeConnectionPool.instances[-1]
            pool.connection.rollback = lambda: (_ for _ in ()).throw(RuntimeError("reset failed"))

            with self.assertRaisesRegex(
                database.DatabaseConfigurationError, "was discarded"
            ):
                connection.close()

            self.assertTrue(pool.connection.closed)
            self.assertEqual(pool.returned, [(pool.connection, {})])

    def test_pool_return_failure_discards_connection(self):
        with self.use_fake_pool(), patch.dict(
            os.environ, {"DATABASE_URL": self.POSTGRES_URL}
        ):
            connection = database.get_connection()
            pool = FakeConnectionPool.instances[-1]
            pool.putconn_fail_once = True

            with self.assertRaisesRegex(
                database.DatabaseConfigurationError, "was discarded"
            ):
                connection.close()

            self.assertEqual(pool.connection.rollback_count, 1)
            self.assertTrue(pool.connection.closed)
            self.assertEqual(pool.returned, [(pool.connection, {})])


if __name__ == "__main__":
    unittest.main()
