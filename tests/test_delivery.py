import sys
import json
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module
import database
import modules.delivery as delivery_module


@pytest.fixture
def delivery_client(tmp_path):
    database.DATABASE = str(tmp_path / "delivery_test.db")
    database.create_tables()
    conn = database.get_connection()
    conn.execute("DELETE FROM users")
    conn.executemany(
        """INSERT INTO users (username, password_hash, full_name, role, system)
           VALUES (?, ?, ?, ?, ?)""",
        [
            ("delivery_admin", generate_password_hash("admin-password"), "Delivery Admin", "admin", "delivery"),
            ("dispatcher", generate_password_hash("dispatcher-password"), "Dispatcher", "dispatcher", "delivery"),
            ("driver", generate_password_hash("driver-password"), "Driver One", "driver", "delivery"),
            ("other_driver", generate_password_hash("other-password"), "Driver Two", "driver", "delivery"),
            ("dist_admin", generate_password_hash("dist-password"), "Distribution Admin", "admin", "distribution"),
        ],
    )
    conn.commit()
    conn.close()
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


def login_as(client, username):
    conn = database.get_connection()
    user = conn.execute("SELECT id, role, system FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    with client.session_transaction() as session:
        session["user_id"] = user["id"]
        session["username"] = username
        session["full_name"] = username
        session["role"] = user["role"]
        session["system"] = user["system"]
    return user["id"]


def test_dispatcher_assigns_route_driver_gets_notification_and_completes(delivery_client):
    assert b"/login?system=delivery" in delivery_client.get("/login").data
    login_as(delivery_client, "dispatcher")
    conn = database.get_connection()
    driver_id = conn.execute("SELECT id FROM users WHERE username = 'driver'").fetchone()["id"]
    conn.close()

    response = delivery_client.post(
        "/delivery/routes/new",
        data={
            "title": "North side deliveries",
            "vehicle": "Van 12",
            "route_date": "2026-10-09",
            "driver_id": str(driver_id),
            "stop_count": "2",
            "stops-0-customer_name": "Customer One",
            "stops-0-customer_phone": "+1 555 0100",
            "stops-0-address": "10 Main Street, Austin",
            "stops-1-customer_name": "Customer Two",
            "stops-1-customer_phone": "555-0110",
            "stops-1-maps_url": "https://maps.google.com/?q=customer",
        },
    )
    assert response.status_code == 302
    route_id = int(response.headers["Location"].rsplit("/", 1)[1])
    dispatcher_detail = delivery_client.get(f"/delivery/routes/{route_id}")
    assert dispatcher_detail.status_code == 200
    assert b"Customer One" in dispatcher_detail.data

    conn = database.get_connection()
    notification = conn.execute(
        "SELECT user_id, read_at FROM delivery_notifications WHERE route_id = ?",
        (route_id,),
    ).fetchone()
    assert notification["user_id"] == driver_id
    assert notification["read_at"] is None
    conn.close()

    login_as(delivery_client, "driver")
    driver_home = delivery_client.get("/delivery")
    assert b"New route plan assigned: North side deliveries" in driver_home.data
    assert b"North side deliveries" in driver_home.data
    driver_detail = delivery_client.get(f"/delivery/routes/{route_id}")
    assert b"Customer One" in driver_detail.data
    assert b"+1 555 0100" in driver_detail.data
    assert b"Open address in Google Maps" in driver_detail.data
    assert b"maps/search/?api=1&amp;query=10%20Main%20Street%2C%20Austin" in driver_detail.data
    assert b"Open map and directions" in driver_detail.data
    assert b"maps.google.com/?q=customer" in driver_detail.data

    conn = database.get_connection()
    notification = conn.execute(
        "SELECT read_at FROM delivery_notifications WHERE route_id = ?",
        (route_id,),
    ).fetchone()
    conn.close()
    assert notification["read_at"] is not None

    assert delivery_client.post(f"/delivery/routes/{route_id}/complete").status_code == 302
    assert b"completed" in delivery_client.get(f"/delivery/routes/{route_id}").data

    login_as(delivery_client, "other_driver")
    assert delivery_client.get(f"/delivery/routes/{route_id}").status_code == 404
    assert delivery_client.post(f"/delivery/routes/{route_id}/complete").status_code == 404


def test_delivery_role_boundaries_and_route_validation(delivery_client):
    login_as(delivery_client, "driver")
    assert delivery_client.get("/delivery/routes/new").status_code == 403
    assert delivery_client.post("/delivery/routes/new").status_code == 403

    login_as(delivery_client, "dispatcher")
    conn = database.get_connection()
    driver_id = conn.execute("SELECT id FROM users WHERE username = 'driver'").fetchone()["id"]
    conn.close()
    response = delivery_client.post(
        "/delivery/routes/new",
        data={
            "title": "Bad maps URL",
            "route_date": "2026-10-09",
            "driver_id": str(driver_id),
            "stop_count": "1",
            "stops-0-customer_name": "Customer",
            "stops-0-maps_url": "javascript:alert(1)",
        },
    )
    assert response.status_code == 200
    assert b"valid map link" in response.data
    assert b'value="Bad maps URL"' in response.data
    assert b'"customer_name": "Customer"' in response.data
    conn = database.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM delivery_routes").fetchone()[0] == 0
    conn.close()


def test_delivery_system_setup_from_distribution_admin(delivery_client):
    conn = database.get_connection()
    conn.execute("DELETE FROM users WHERE system = 'delivery'")
    conn.commit()
    conn.close()
    login_as(delivery_client, "dist_admin")

    assert delivery_client.get("/users/setup-delivery").status_code == 200
    response = delivery_client.post(
        "/users/setup-delivery",
        data={
            "username": "first_delivery_admin",
            "full_name": "First Delivery Admin",
            "password": "secure-pass-9",
        },
    )
    assert response.status_code == 302
    conn = database.get_connection()
    created_user = conn.execute(
        "SELECT role, system FROM users WHERE username = 'first_delivery_admin'"
    ).fetchone()
    conn.close()
    assert tuple(created_user) == ("admin", "delivery")


def test_driver_push_subscription_and_route_assignment(delivery_client, monkeypatch):
    monkeypatch.setenv("DELIVERY_VAPID_PUBLIC_KEY", "browser-public-key")
    monkeypatch.setenv("DELIVERY_VAPID_PRIVATE_KEY", "/secure/vapid-private.pem")
    monkeypatch.setenv("DELIVERY_VAPID_SUBJECT", "mailto:ops@example.com")
    driver_id = login_as(delivery_client, "driver")

    assert delivery_client.get("/delivery/push/public-key").json == {"publicKey": "browser-public-key"}
    driver_home = delivery_client.get("/delivery")
    assert b"Enable push notifications" in driver_home.data
    service_worker = delivery_client.get("/service-worker.js")
    assert b"addEventListener('push'" in service_worker.data
    assert b"showNotification" in service_worker.data
    assert service_worker.headers["Service-Worker-Allowed"] == "/"
    assert delivery_client.get("/static/delivery.webmanifest").status_code == 200

    subscription = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/example",
        "keys": {"p256dh": "browser-p256dh", "auth": "browser-auth"},
    }
    response = delivery_client.post("/delivery/push/subscriptions", json=subscription)
    assert response.status_code == 200
    conn = database.get_connection()
    assert conn.execute(
        "SELECT user_id FROM delivery_push_subscriptions WHERE endpoint = ?",
        (subscription["endpoint"],),
    ).fetchone()["user_id"] == driver_id
    conn.close()
    assert delivery_client.post(
        "/delivery/push/subscriptions",
        json={"endpoint": "https://attacker.example/push", "keys": subscription["keys"]},
    ).status_code == 400

    sent_pushes = []

    def fake_webpush(**kwargs):
        sent_pushes.append(kwargs)

    monkeypatch.setattr(delivery_module, "webpush", fake_webpush)
    login_as(delivery_client, "dispatcher")
    response = delivery_client.post(
        "/delivery/routes/new",
        data={
            "title": "East route",
            "route_date": "2026-10-09",
            "driver_id": str(driver_id),
            "stop_count": "1",
            "stops-0-customer_name": "Customer",
            "stops-0-customer_phone": "555-0100",
            "stops-0-address": "1 East Road",
        },
    )
    assert response.status_code == 302
    assert len(sent_pushes) == 1
    assert sent_pushes[0]["vapid_private_key"] == "/secure/vapid-private.pem"
    push_payload = json.loads(sent_pushes[0]["data"])
    assert push_payload["title"] == "New delivery route"
    assert push_payload["body"] == "East route · 2026-10-09"
    assert push_payload["url"].startswith("/delivery/routes/")
    assert "555-0100" not in sent_pushes[0]["data"]

    login_as(delivery_client, "driver")
    response = delivery_client.delete(
        "/delivery/push/subscriptions",
        json={"endpoint": subscription["endpoint"]},
    )
    assert response.status_code == 200
    conn = database.get_connection()
    assert conn.execute(
        "SELECT COUNT(*) FROM delivery_push_subscriptions WHERE user_id = ?",
        (driver_id,),
    ).fetchone()[0] == 0
    conn.close()


def test_web_push_configuration_required_for_public_key(delivery_client, monkeypatch):
    monkeypatch.delenv("DELIVERY_VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("DELIVERY_VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("DELIVERY_VAPID_SUBJECT", raising=False)
    login_as(delivery_client, "driver")

    assert delivery_client.get("/delivery/push/public-key").status_code == 503
    assert b"Push is not configured" in delivery_client.get("/delivery").data


def test_same_username_can_login_to_separate_systems_and_delivery_admin_can_change_credentials(delivery_client):
    conn = database.get_connection()
    conn.execute(
        """INSERT INTO users (username, password_hash, full_name, role, system)
           VALUES (?, ?, ?, ?, ?)""",
        ("admin", generate_password_hash("distribution-password"), "Distribution Admin", "admin", "distribution"),
    )
    conn.execute(
        """INSERT INTO users (username, password_hash, full_name, role, system)
           VALUES (?, ?, ?, ?, ?)""",
        ("admin", generate_password_hash("1234"), "Temporary Delivery Admin", "admin", "delivery"),
    )
    conn.commit()
    delivery_admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin' AND system = 'delivery'"
    ).fetchone()["id"]
    conn.close()

    response = delivery_client.post(
        "/login?system=delivery",
        data={"username": "admin", "password": "1234", "system": "delivery"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/delivery")
    edit_page = delivery_client.get(f"/users/edit/{delivery_admin_id}")
    assert edit_page.status_code == 200
    assert b'name="username" value="admin"' in edit_page.data

    response = delivery_client.post(
        f"/users/edit/{delivery_admin_id}",
        data={
            "username": "delivery_owner",
            "full_name": "Delivery Owner",
            "role": "admin",
            "password": "new-delivery-password",
            "is_active": "on",
        },
    )
    assert response.status_code == 302
    response = delivery_client.post(
        "/login?system=delivery",
        data={
            "username": "delivery_owner",
            "password": "new-delivery-password",
            "system": "delivery",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/delivery")

    response = delivery_client.post(
        "/login?system=distribution",
        data={
            "username": "admin",
            "password": "distribution-password",
            "system": "distribution",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
