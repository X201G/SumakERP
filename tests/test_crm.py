import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module
import database


@pytest.fixture
def crm_client(tmp_path):
    database.DATABASE = str(tmp_path / "crm_test.db")
    database.create_tables()
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users")
    cursor.executemany(
        "INSERT INTO users (username, password_hash, full_name, role, system) VALUES (?, ?, ?, ?, ?)",
        [
            ("dist_admin", generate_password_hash("dist-password"), "Distribution Admin", "admin", "distribution"),
            ("crmadmin", generate_password_hash("CrmTemp2026!"), "CRM Administrator", "admin", "crm"),
            ("mfg_admin", generate_password_hash("mfg-password"), "MFG Admin", "admin", "manufacturing"),
        ],
    )
    cursor.execute("INSERT INTO customers (name, phone, email) VALUES (?, ?, ?)", ("CRM Customer", "555-0100", "crm@example.test"))
    customer_id = cursor.lastrowid
    cursor.execute("INSERT INTO products (product_name, sale_price) VALUES (?, ?)", ("CRM Product", 1250))
    product_id = cursor.lastrowid
    conn.commit()
    crm_user_id = cursor.execute("SELECT id FROM users WHERE system = 'crm'").fetchone()[0]
    distribution_user_id = cursor.execute("SELECT id FROM users WHERE system = 'distribution'").fetchone()[0]
    manufacturing_user_id = cursor.execute("SELECT id FROM users WHERE system = 'manufacturing'").fetchone()[0]
    conn.close()

    app_module.app.config.update(TESTING=True)
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session.update(
            user_id=crm_user_id,
            username="crmadmin",
            full_name="CRM Administrator",
            role="admin",
            system="crm",
        )
    return client, crm_user_id, distribution_user_id, manufacturing_user_id, customer_id, product_id


def test_new_sales_quotation_automatically_creates_crm_opportunity(crm_client):
    client, crm_user_id, distribution_user_id, _, customer_id, product_id = crm_client
    with client.session_transaction() as session:
        session.update(
            user_id=distribution_user_id,
            username="dist_admin",
            full_name="Distribution Admin",
            role="admin",
            system="distribution",
        )
    response = client.post(
        "/sales-quotations/add",
        data={
            "customer_id": str(customer_id),
            "quotation_date": "2026-09-29",
            "valid_till": "2026-10-29",
            "product_id[]": str(product_id),
            "unit_id[]": "",
            "hsn_id[]": "",
            "quantity[]": "2",
            "rate[]": "1250",
            "discount[]": "0",
            "tax_rate[]": "0",
        },
    )
    assert response.status_code == 302

    conn = database.get_connection()
    opportunity = conn.execute(
        """SELECT crm_opportunities.*, sales_quotations.quotation_no
           FROM crm_opportunities JOIN sales_quotations
             ON sales_quotations.id = crm_opportunities.quotation_id"""
    ).fetchone()
    conn.close()
    assert opportunity["stage"] == "quoted"
    assert opportunity["customer_id"] == customer_id
    assert opportunity["product_id"] == product_id
    assert opportunity["potential_value"] == 2500
    assert opportunity["quotation_no"].startswith("QT-")
    assert opportunity["owner_id"] == crm_user_id


def test_crm_activity_po_and_won_flow_updates_linked_quotation(crm_client):
    client, user_id, _, _, customer_id, product_id = crm_client
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sales_quotations (quotation_no, quotation_date, customer_id, total_amount) VALUES (?, ?, ?, ?)",
        ("QT-CRM-1", "2026-09-29", customer_id, 2500),
    )
    quotation_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO sales_quotation_items (quotation_id, product_id, quantity, rate, amount) VALUES (?, ?, ?, ?, ?)",
        (quotation_id, product_id, 2, 1250, 2500),
    )
    cursor.execute(
        """INSERT INTO crm_opportunities
           (title, customer_id, product_id, quotation_id, stage, potential_value, created_by, owner_id)
           VALUES (?, ?, ?, ?, 'quoted', ?, ?, ?)""",
        ("CRM deal", customer_id, product_id, quotation_id, 2500, user_id, user_id),
    )
    opportunity_id = cursor.lastrowid
    conn.commit()
    conn.close()

    response = client.post(
        f"/crm/opportunities/{opportunity_id}/activities",
        data={
            "activity_type": "trial",
            "activity_date": "2026-09-29",
            "outcome": "Promising",
            "next_follow_up": "2026-10-06",
            "notes": "Customer completed a product trial.",
        },
    )
    assert response.status_code == 302
    conn = database.get_connection()
    after_trial = conn.execute(
        "SELECT stage, product_id, next_follow_up FROM crm_opportunities WHERE id = ?",
        (opportunity_id,),
    ).fetchone()
    conn.close()
    assert after_trial["stage"] == "trial"
    assert after_trial["product_id"] == product_id
    assert after_trial["next_follow_up"] == "2026-10-06"

    response = client.post(
        f"/crm/opportunities/{opportunity_id}/update",
        data={
            "title": "CRM deal",
            "stage": "won",
            "potential_value": "2500",
            "po_status": "not_received",
            "customer_po_no": "PO-42",
            "owner_id": str(user_id),
        },
    )
    assert response.status_code == 302

    conn = database.get_connection()
    opportunity = conn.execute(
        "SELECT stage, customer_po_no, po_status, next_follow_up FROM crm_opportunities WHERE id = ?",
        (opportunity_id,),
    ).fetchone()
    quotation_status = conn.execute(
        "SELECT status FROM sales_quotations WHERE id = ?", (quotation_id,)
    ).fetchone()[0]
    activity_count = conn.execute(
        "SELECT COUNT(*) FROM crm_activities WHERE opportunity_id = ?", (opportunity_id,)
    ).fetchone()[0]
    conn.close()

    assert opportunity["stage"] == "won"
    assert opportunity["customer_po_no"] == "PO-42"
    assert opportunity["po_status"] == "received"
    assert opportunity["next_follow_up"] == "2026-10-06"
    assert quotation_status == "accepted"
    assert activity_count == 1


def test_crm_rejects_manufacturing_system_user(crm_client):
    client, _, _, manufacturing_user_id, _, _ = crm_client
    with client.session_transaction() as session:
        session.update(user_id=manufacturing_user_id, username="mfg_admin", full_name="MFG Admin", system="manufacturing")
    response = client.get("/crm")
    assert response.status_code == 302
    assert "/login?system=crm" in response.location


def test_crm_admin_can_log_in_and_change_temporary_password(crm_client):
    client, crm_user_id, _, _, _, _ = crm_client
    client.get("/logout")
    chooser = client.get("/login")
    assert b"system=crm" in chooser.data
    response = client.post(
        "/login",
        data={"system": "crm", "username": "crmadmin", "password": "CrmTemp2026!"},
    )
    assert response.status_code == 302
    assert response.location.endswith("/crm")
    team_page = client.get("/users")
    assert b"crm-users-page" in team_page.data
    assert b"Set up Projects portal" not in team_page.data

    response = client.post(
        f"/users/edit/{crm_user_id}",
        data={
            "full_name": "CRM Administrator",
            "role": "admin",
            "password": "ChangedTemp2026!",
            "is_active": "on",
        },
    )
    assert response.status_code == 302
    client.get("/logout")
    response = client.post(
        "/login",
        data={"system": "crm", "username": "crmadmin", "password": "ChangedTemp2026!"},
    )
    assert response.status_code == 302
    assert response.location.endswith("/crm")


def test_crm_team_limits_roles_and_normalizes_invalid_submissions(crm_client):
    client, _, _, _, _, _ = crm_client
    for path in ("/users", "/users/add"):
        response = client.get(path)
        assert response.status_code == 200
        for role in ("admin", "user", "external"):
            assert f'value="{role}"'.encode() in response.data
        assert b'value="sales"' not in response.data

    response = client.post(
        "/users/add",
        data={"username": "crm_rep", "password": "crm-password", "role": "accounts"},
    )
    assert response.status_code == 302
    conn = database.get_connection()
    user = conn.execute("SELECT id, role, system FROM users WHERE username = ?", ("crm_rep",)).fetchone()
    conn.close()
    assert user["role"] == "user"
    assert user["system"] == "crm"

    response = client.post(
        f"/users/edit/{user['id']}",
        data={"full_name": "CRM Rep", "role": "sales", "is_active": "on"},
    )
    assert response.status_code == 302
    conn = database.get_connection()
    role = conn.execute("SELECT role FROM users WHERE id = ?", (user["id"],)).fetchone()[0]
    conn.close()
    assert role == "user"


def test_crm_displays_all_read_only_dates_day_first(crm_client):
    client, user_id, _, _, customer_id, product_id = crm_client
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sales_quotations (quotation_no, quotation_date, customer_id) VALUES (?, ?, ?)",
        ("QT-DATE", "2024-01-05", customer_id),
    )
    quotation_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO sales_orders (order_no, order_date, customer_id) VALUES (?, ?, ?)",
        ("SO-DATE", "2024-01-05", customer_id),
    )
    cursor.execute(
        """INSERT INTO crm_opportunities
           (title, customer_id, product_id, quotation_id, stage, next_follow_up, created_by, owner_id)
           VALUES (?, ?, ?, ?, 'qualified', ?, ?, ?)""",
        ("Date format deal", customer_id, product_id, quotation_id, "2024-01-05", user_id, user_id),
    )
    opportunity_id = cursor.lastrowid
    cursor.execute(
        """INSERT INTO crm_activities
           (opportunity_id, activity_type, activity_date, notes, next_follow_up, created_by)
           VALUES (?, 'visit', ?, ?, ?, ?)""",
        (opportunity_id, "2024-01-05", "Date format check", "2024-01-05", user_id),
    )
    conn.commit()
    conn.close()

    pipeline = client.get("/crm").data
    detail = client.get(f"/crm/opportunities/{opportunity_id}").data
    assert b"05/01/2024" in pipeline
    assert b"<time>05/01/2024</time>" in detail
    assert detail.count(b"<br>05/01/2024") == 2
    assert b"Follow up: 05/01/2024" in detail
    assert b"css/day_first_dates.css" in detail
    assert b"js/day_first_dates.js" in detail