from datetime import date
import json
import logging
import os
from urllib.parse import urlsplit

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from pywebpush import WebPushException, webpush

from database import get_connection
from modules.auth import require_system

delivery = Blueprint("delivery", __name__)
delivery.before_request(require_system("delivery"))
logger = logging.getLogger(__name__)


def _can_dispatch():
    return session.get("role") in ("admin", "dispatcher")


def _active_drivers(cursor):
    cursor.execute(
        """SELECT id, username, full_name FROM users
           WHERE system = 'delivery' AND role = 'driver' AND is_active = 1
           ORDER BY full_name COLLATE NOCASE, username"""
    )
    return cursor.fetchall()


def _valid_maps_url(value):
    if not value:
        return True
    parsed = urlsplit(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _vapid_config():
    return {
        "public_key": os.environ.get("DELIVERY_VAPID_PUBLIC_KEY", "").strip(),
        "private_key": os.environ.get("DELIVERY_VAPID_PRIVATE_KEY", "").strip(),
        "subject": os.environ.get("DELIVERY_VAPID_SUBJECT", "").strip(),
    }


def _valid_push_endpoint(value):
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.fragment
    ):
        return False
    host = host.lower()
    return host in {
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "push.services.mozilla.com",
        "web.push.apple.com",
    } or host.endswith(".notify.windows.com")


def _send_driver_push(user_id, route_id, title, route_date):
    config = _vapid_config()
    conn = get_connection()
    subscriptions = conn.execute(
        """SELECT id, endpoint, p256dh, auth FROM delivery_push_subscriptions
           WHERE user_id = ?""",
        (user_id,),
    ).fetchall()
    conn.close()

    if not subscriptions:
        return 0, "This driver has not enabled browser push notifications; the route is still in their portal."
    if not all(config.values()):
        return 0, "Web Push is not configured on the server; the route is still in the driver's portal."

    message = json.dumps(
        {
            "title": "New delivery route",
            "body": f"{title} · {route_date}",
            "url": url_for("delivery.route_detail", route_id=route_id),
            "tag": f"delivery-route-{route_id}",
        }
    )
    delivered = 0
    failed = 0
    stale_subscription_ids = []
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription["endpoint"],
                    "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
                },
                data=message,
                vapid_private_key=config["private_key"],
                vapid_claims={"sub": config["subject"]},
                timeout=10,
            )
            delivered += 1
        except WebPushException as error:
            failed += 1
            response = getattr(error, "response", None)
            status_code = getattr(response, "status_code", None)
            if status_code in (404, 410):
                stale_subscription_ids.append(subscription["id"])
            logger.warning(
                "Web Push failed for delivery subscription %s (HTTP %s)",
                subscription["id"],
                status_code or "unknown",
            )

    if stale_subscription_ids:
        conn = get_connection()
        conn.executemany(
            "DELETE FROM delivery_push_subscriptions WHERE id = ?",
            [(subscription_id,) for subscription_id in stale_subscription_ids],
        )
        conn.commit()
        conn.close()

    if delivered and failed:
        return delivered, f"Push sent to {delivered} device(s); {failed} device(s) could not be reached."
    if delivered:
        return delivered, f"Push notification sent to {delivered} device(s)."
    return 0, "The route is assigned, but push could not be delivered. The driver can still see it in the portal."


@delivery.app_context_processor
def inject_delivery_notifications():
    user_id = session.get("user_id")
    if session.get("system") != "delivery" or not user_id:
        return {"delivery_unread_count": 0}

    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM delivery_notifications WHERE user_id = ? AND read_at IS NULL",
        (user_id,),
    ).fetchone()
    conn.close()
    return {"delivery_unread_count": row["total"]}


@delivery.route("/delivery")
def index():
    user_id = session["user_id"]
    if _can_dispatch():
        routes = get_connection()
        route_rows = routes.execute(
            """SELECT delivery_routes.*, users.full_name AS driver_name,
                      COUNT(delivery_route_stops.id) AS stop_count
               FROM delivery_routes
               JOIN users ON users.id = delivery_routes.driver_id
               LEFT JOIN delivery_route_stops ON delivery_route_stops.route_id = delivery_routes.id
               GROUP BY delivery_routes.id
               ORDER BY delivery_routes.route_date DESC, delivery_routes.id DESC"""
        ).fetchall()
        routes.close()
        return render_template("delivery/index.html", routes=route_rows, is_dispatcher=True, notifications=[])
    if session.get("role") != "driver":
        abort(403)

    conn = get_connection()
    route_rows = conn.execute(
        """SELECT delivery_routes.*, users.full_name AS driver_name,
                  COUNT(delivery_route_stops.id) AS stop_count
           FROM delivery_routes
           JOIN users ON users.id = delivery_routes.driver_id
           LEFT JOIN delivery_route_stops ON delivery_route_stops.route_id = delivery_routes.id
           WHERE delivery_routes.driver_id = ?
           GROUP BY delivery_routes.id
           ORDER BY delivery_routes.route_date DESC, delivery_routes.id DESC""",
        (user_id,),
    ).fetchall()
    notifications = conn.execute(
        """SELECT delivery_notifications.id, delivery_notifications.message,
                  delivery_notifications.created_at, delivery_routes.id AS route_id,
                  delivery_routes.route_date
           FROM delivery_notifications
           JOIN delivery_routes ON delivery_routes.id = delivery_notifications.route_id
           WHERE delivery_notifications.user_id = ? AND delivery_notifications.read_at IS NULL
           ORDER BY delivery_notifications.created_at DESC""",
        (user_id,),
    ).fetchall()
    conn.close()
    return render_template(
        "delivery/index.html",
        routes=route_rows,
        is_dispatcher=False,
        notifications=notifications,
        push_enabled=bool(all(_vapid_config().values())),
    )


@delivery.route("/delivery/push/public-key")
def push_public_key():
    if session.get("role") != "driver":
        abort(403)
    config = _vapid_config()
    if not all(config.values()):
        return jsonify(error="Web Push is not configured on the server."), 503
    return jsonify(publicKey=config["public_key"])


@delivery.route("/delivery/push/subscriptions", methods=["POST", "DELETE"])
def push_subscriptions():
    if session.get("role") != "driver":
        abort(403)
    if not request.is_json:
        return jsonify(error="A JSON subscription is required."), 400

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="A valid JSON subscription is required."), 400
    endpoint = payload.get("endpoint")
    if not _valid_push_endpoint(endpoint):
        return jsonify(error="The push endpoint is invalid."), 400

    conn = get_connection()
    if request.method == "DELETE":
        conn.execute(
            "DELETE FROM delivery_push_subscriptions WHERE user_id = ? AND endpoint = ?",
            (session["user_id"], endpoint),
        )
        conn.commit()
        conn.close()
        return jsonify(success=True)

    keys = payload.get("keys")
    if not isinstance(keys, dict):
        conn.close()
        return jsonify(error="The push subscription keys are invalid."), 400
    p256dh = keys.get("p256dh")
    auth_key = keys.get("auth")
    if not all(isinstance(value, str) and 1 <= len(value) <= 256 for value in (p256dh, auth_key)):
        conn.close()
        return jsonify(error="The push subscription keys are invalid."), 400
    conn.execute(
        """INSERT INTO delivery_push_subscriptions (user_id, endpoint, p256dh, auth)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(endpoint) DO UPDATE SET
               user_id = excluded.user_id,
               p256dh = excluded.p256dh,
               auth = excluded.auth,
               updated_at = CURRENT_TIMESTAMP""",
        (session["user_id"], endpoint, p256dh, auth_key),
    )
    conn.commit()
    conn.close()
    return jsonify(success=True)


@delivery.route("/delivery/routes/new", methods=["GET", "POST"])
def create_route():
    if not _can_dispatch():
        abort(403)

    conn = get_connection()
    cursor = conn.cursor()
    drivers = _active_drivers(cursor)
    route_date = date.today().isoformat()
    selected_driver_id = None
    form_stops = [{"customer_name": "", "customer_phone": "", "address": "", "maps_url": ""}]

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        vehicle = request.form.get("vehicle", "").strip()
        route_date = request.form.get("route_date", "").strip()
        driver_id = request.form.get("driver_id", type=int)
        stop_count = request.form.get("stop_count", type=int)
        selected_driver_id = driver_id
        selected_driver = next((driver for driver in drivers if driver["id"] == driver_id), None)
        if stop_count is not None and 1 <= stop_count <= 50:
            form_stops = [
                {
                    field: request.form.get(f"stops-{index}-{field}", "").strip()
                    for field in ("customer_name", "customer_phone", "address", "maps_url")
                }
                for index in range(stop_count)
            ]

        try:
            date.fromisoformat(route_date)
            valid_date = True
        except ValueError:
            valid_date = False

        if not title:
            flash("Enter a route plan name.", "danger")
        elif not valid_date:
            flash("Choose a valid route date.", "danger")
        elif selected_driver is None:
            flash("Choose an active driver.", "danger")
        elif stop_count is None or not 1 <= stop_count <= 50:
            flash("Add between 1 and 50 customer stops.", "danger")
        else:
            stops = []
            has_invalid_stop = False
            for stop in form_stops:
                customer_name = stop["customer_name"]
                customer_phone = stop["customer_phone"]
                address = stop["address"]
                maps_url = stop["maps_url"]
                if not customer_name or not (address or maps_url) or not _valid_maps_url(maps_url):
                    has_invalid_stop = True
                    break
                stops.append((customer_name, customer_phone, address, maps_url or None))

            if has_invalid_stop:
                flash("Each stop needs a customer name and an address or valid map link.", "danger")
            else:
                cursor.execute(
                    """INSERT INTO delivery_routes
                       (title, vehicle, route_date, driver_id, status, created_by)
                       VALUES (?, ?, ?, ?, 'assigned', ?)""",
                    (title, vehicle, route_date, driver_id, session["user_id"]),
                )
                route_id = cursor.lastrowid
                cursor.executemany(
                    """INSERT INTO delivery_route_stops
                       (route_id, stop_order, customer_name, customer_phone, address, maps_url)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    [
                        (route_id, index + 1, *stop)
                        for index, stop in enumerate(stops)
                    ],
                )
                cursor.execute(
                    """INSERT INTO delivery_notifications (user_id, route_id, message)
                       VALUES (?, ?, ?)""",
                    (driver_id, route_id, f"New route plan assigned: {title}"),
                )
                conn.commit()
                conn.close()
                push_count, push_message = _send_driver_push(
                    driver_id,
                    route_id,
                    title,
                    route_date,
                )
                flash(
                    f"Route plan assigned. {push_message}",
                    "success" if push_message.startswith("Push notification sent") else "info",
                )
                return redirect(url_for("delivery.route_detail", route_id=route_id))

    conn.close()
    return render_template(
        "delivery/route_form.html",
        drivers=drivers,
        route_date=route_date,
        selected_driver_id=selected_driver_id,
        title=request.form.get("title", "").strip() if request.method == "POST" else "",
        vehicle=request.form.get("vehicle", "").strip() if request.method == "POST" else "",
        form_stops=form_stops,
    )


@delivery.route("/delivery/routes/<int:route_id>")
def route_detail(route_id):
    conn = get_connection()
    route = conn.execute(
        """SELECT delivery_routes.*, users.full_name AS driver_name,
                  users.username AS driver_username
           FROM delivery_routes
           JOIN users ON users.id = delivery_routes.driver_id
           WHERE delivery_routes.id = ?""",
        (route_id,),
    ).fetchone()
    if route is None:
        conn.close()
        abort(404)
    if not _can_dispatch() and (
        session.get("role") != "driver" or route["driver_id"] != session["user_id"]
    ):
        conn.close()
        abort(404)

    stops = conn.execute(
        "SELECT * FROM delivery_route_stops WHERE route_id = ? ORDER BY stop_order",
        (route_id,),
    ).fetchall()
    if session.get("role") == "driver":
        conn.execute(
            """UPDATE delivery_notifications SET read_at = CURRENT_TIMESTAMP
               WHERE route_id = ? AND user_id = ? AND read_at IS NULL""",
            (route_id, session["user_id"]),
        )
        conn.commit()
    conn.close()
    return render_template("delivery/route_detail.html", route=route, stops=stops)


@delivery.route("/delivery/routes/<int:route_id>/complete", methods=["POST"])
def complete_route(route_id):
    if session.get("role") != "driver":
        abort(403)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE delivery_routes SET status = 'completed', updated_at = CURRENT_TIMESTAMP
           WHERE id = ? AND driver_id = ? AND status = 'assigned'""",
        (route_id, session["user_id"]),
    )
    updated = cursor.rowcount
    conn.commit()
    conn.close()
    if not updated:
        abort(404)
    flash("Route marked as completed.", "success")
    return redirect(url_for("delivery.route_detail", route_id=route_id))
