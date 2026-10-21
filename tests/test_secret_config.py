import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import _configure_production, _get_secret_key, _run_development_server
from werkzeug.middleware.proxy_fix import ProxyFix


class SecretConfigTests(unittest.TestCase):
    def test_configured_secret_key_is_used(self):
        with patch.dict(
            os.environ,
            {"FLASK_SECRET_KEY": "test-only-secret", "APP_ENV": "production"},
        ):
            self.assertEqual(_get_secret_key(), "test-only-secret")

    def test_missing_production_secret_fails_safely(self):
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            os.environ.pop("FLASK_SECRET_KEY", None)
            with self.assertRaisesRegex(RuntimeError, "FLASK_SECRET_KEY must be set"):
                _get_secret_key()

    def test_missing_development_secret_generates_a_temporary_key(self):
        with patch.dict(os.environ, {"APP_ENV": "", "FLASK_SECRET_KEY": ""}):
            first_key = _get_secret_key()
            second_key = _get_secret_key()

        self.assertEqual(len(first_key), 64)
        self.assertNotEqual(first_key, second_key)

    def test_production_uses_secure_cookies_and_one_trusted_proxy(self):
        flask_app = Flask(__name__)

        _configure_production(flask_app, {"APP_ENV": "production"})

        self.assertTrue(flask_app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(flask_app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(flask_app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertEqual(flask_app.config["PREFERRED_URL_SCHEME"], "https")
        self.assertIsInstance(flask_app.wsgi_app, ProxyFix)
        self.assertEqual(flask_app.wsgi_app.x_for, 1)
        self.assertEqual(flask_app.wsgi_app.x_proto, 1)
        self.assertEqual(flask_app.wsgi_app.x_host, 1)

    def test_development_does_not_trust_forwarded_headers(self):
        flask_app = Flask(__name__)
        original_wsgi_app = flask_app.wsgi_app

        _configure_production(flask_app, {"APP_ENV": "development"})

        self.assertEqual(flask_app.wsgi_app, original_wsgi_app)
        self.assertFalse(flask_app.config["SESSION_COOKIE_SECURE"])

    def test_production_direct_start_refuses_sqlite_initializer(self):
        with (
            patch("app.create_tables") as create_tables,
            patch("app.is_postgres_configured", return_value=False),
        ):
            with self.assertRaisesRegex(RuntimeError, "Do not start production"):
                _run_development_server({"APP_ENV": "production"})

        create_tables.assert_not_called()

    def test_postgres_direct_start_refuses_sqlite_initializer(self):
        with (
            patch("app.create_tables") as create_tables,
            patch("app.is_postgres_configured", return_value=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "Do not start production"):
                _run_development_server({"APP_ENV": "development"})

        create_tables.assert_not_called()


if __name__ == "__main__":
    unittest.main()
