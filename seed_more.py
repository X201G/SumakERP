"""
SUMAK ERP - Extended dummy data (remaining modules)
Run AFTER seed_data.py, with:  python seed_more.py

Fills the modules the first seeder skipped:
  SALES:    quotations, orders, proforma, challans, returns (+items)
  PURCHASE: invoices, GRN, returns (+items)

Uses products/customers/suppliers that already exist in your DB.
All docs tagged "ZZ-seed" in remarks for easy cleanup.
Safe: only ADDS rows.
"""

import random
from datetime import date, timedelta
from database import get_connection

random.seed(99)

conn = get_connection()
cursor = conn.cursor()

# ----------------------------------------------------------------------
# Load existing masters
# ----------------------------------------------------------------------
customer_ids = [r["id"] for r in cursor.execute("SELECT id FROM customers").fetchall()]
supplier_ids = [r["id"] for r in cursor.execute("SELECT id FROM suppliers").fetchall()]
transporter_ids = [r["id"] for r in cursor.execute("SELECT id FROM transporters").fetchall()]

products = cursor.execute(
    "SELECT id, unit_id, hsn_id, tax_rate, sale_price, purchase_price FROM products"
).fetchall()
product_ids = [p["id"] for p in products]
pinfo = {p["id"]: (p["unit_id"], p["hsn_id"], p["tax_rate"] or 18.0,
                   p["sale_price"] or 100, p["purchase_price"] or 80) for p in products}

if not customer_ids or not product_ids:
    print("⚠ No customers/products found. Run seed_data.py first!")
    conn.close()
    raise SystemExit

def t_id():
    return random.choice(transporter_ids) if transporter_ids else None

def calc(qty, rate, discount, tax_rate):
    bef = qty * rate * (1 - discount / 100)
    tax = bef * tax_rate / 100
    return round(bef, 2), round(tax, 2), round(bef + tax, 2)

def rand_date(days=180):
    return (date.today() - timedelta(days=random.randint(0, days))).isoformat()

def next_no(table, prefix, dt):
    n = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] + 1
    return prefix + dt.replace("-", "") + "-" + str(n).zfill(4)

def make_items(n_min=1, n_max=5, sale=True):
    """Return list of (pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt) and total."""
    rows = []
    total = 0
    for _ in range(random.randint(n_min, n_max)):
        pid = random.choice(product_ids)
        unit, hsn, tax, sp, pp = pinfo[pid]
        qty = random.randint(1, 50)
        rate = sp if sale else pp
        disc = random.choice([0, 0, 5, 10])
        bef, taxa, amt = calc(qty, rate, disc, tax)
        total += amt
        rows.append((pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt))
    return rows, round(total, 2)

# ======================================================================
# SALES QUOTATIONS (60)
# ======================================================================
for _ in range(60):
    dt = rand_date()
    no = next_no("sales_quotations", "QT-", dt)
    cursor.execute("""INSERT INTO sales_quotations
        (quotation_no, quotation_date, customer_id, valid_till, kind_attn, remarks, status, total_amount)
        VALUES (?,?,?,?,?,?,?,?)""",
        (no, dt, random.choice(customer_ids), dt, "", "ZZ-seed", "open", 0))
    hid = cursor.lastrowid
    rows, total = make_items()
    for pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt in rows:
        cursor.execute("""INSERT INTO sales_quotation_items
            (quotation_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (hid, pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt))
    cursor.execute("UPDATE sales_quotations SET total_amount=? WHERE id=?", (total, hid))
print("Sales quotations: 60")

# ======================================================================
# SALES ORDERS (80)  -- items get billed_qty 0 (pending)
# ======================================================================
for _ in range(80):
    dt = rand_date()
    no = next_no("sales_orders", "SO-", dt)
    cursor.execute("""INSERT INTO sales_orders
        (order_no, order_date, customer_id, po_no, po_date, kind_attn, remarks, status, total_amount)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (no, dt, random.choice(customer_ids), f"CPO{random.randint(100,999)}", dt, "", "ZZ-seed", "open", 0))
    hid = cursor.lastrowid
    rows, total = make_items()
    for pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt in rows:
        cursor.execute("""INSERT INTO sales_order_items
            (order_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount, billed_qty)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hid, pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt, 0))
    cursor.execute("UPDATE sales_orders SET total_amount=? WHERE id=?", (total, hid))
print("Sales orders: 80")

# ======================================================================
# PROFORMA INVOICES (50)
# ======================================================================
for _ in range(50):
    dt = rand_date()
    no = next_no("proforma_invoices", "PF-", dt)
    cursor.execute("""INSERT INTO proforma_invoices
        (proforma_no, proforma_date, customer_id, transporter_id, po_no, po_date, kind_attn, remarks, status, total_amount)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (no, dt, random.choice(customer_ids), t_id(), f"CPO{random.randint(100,999)}", dt, "", "ZZ-seed", "open", 0))
    hid = cursor.lastrowid
    rows, total = make_items()
    for pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt in rows:
        cursor.execute("""INSERT INTO proforma_invoice_items
            (proforma_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (hid, pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt))
    cursor.execute("UPDATE proforma_invoices SET total_amount=? WHERE id=?", (total, hid))
print("Proforma invoices: 50")

# ======================================================================
# SALES CHALLANS (70) -- billed_qty 0 (pending)
# ======================================================================
for _ in range(70):
    dt = rand_date()
    no = next_no("sales_challans", "CH-", dt)
    cursor.execute("""INSERT INTO sales_challans
        (challan_no, challan_date, customer_id, transporter_id, po_no, po_date, kind_attn, remarks, status, total_amount)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (no, dt, random.choice(customer_ids), t_id(), f"CPO{random.randint(100,999)}", dt, "", "ZZ-seed", "open", 0))
    hid = cursor.lastrowid
    rows, total = make_items()
    for pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt in rows:
        cursor.execute("""INSERT INTO sales_challan_items
            (challan_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount, billed_qty)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hid, pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt, 0))
    cursor.execute("UPDATE sales_challans SET total_amount=? WHERE id=?", (total, hid))
print("Sales challans: 70")

# ======================================================================
# SALES RETURNS (30) -- link to a random existing invoice
# ======================================================================
inv_ids = [r["id"] for r in cursor.execute("SELECT id FROM sales_invoices").fetchall()]
reasons = ["Damaged", "Wrong item", "Excess supply", "Quality issue", "Customer cancelled"]
for _ in range(30):
    dt = rand_date()
    no = next_no("sales_returns", "SR-", dt)
    inv = random.choice(inv_ids) if inv_ids else None
    cursor.execute("""INSERT INTO sales_returns
        (return_no, return_date, customer_id, transporter_id, invoice_id, reason, remarks, status, total_amount)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (no, dt, random.choice(customer_ids), t_id(), inv, random.choice(reasons), "ZZ-seed", "pending", 0))
    hid = cursor.lastrowid
    rows, total = make_items(1, 3)
    for pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt in rows:
        cursor.execute("""INSERT INTO sales_return_items
            (return_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate, bef_tax_amt, tax_amount, amount, reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hid, pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt, random.choice(reasons)))
    cursor.execute("UPDATE sales_returns SET total_amount=? WHERE id=?", (total, hid))
print("Sales returns: 30")

# ======================================================================
# PURCHASE INVOICES (80)
# ======================================================================
for _ in range(80):
    dt = rand_date()
    no = next_no("purchase_invoices", "PI-", dt)
    cursor.execute("""INSERT INTO purchase_invoices
        (invoice_no, invoice_date, supplier_id, transporter_id, order_id, sup_invoice_no, sup_invoice_date, cr_days, due_date, remarks, status, total_amount)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (no, dt, random.choice(supplier_ids), t_id(), None, f"SUP{random.randint(1000,9999)}", dt, 30, dt, "ZZ-seed", "unpaid", 0))
    hid = cursor.lastrowid
    rows, total = make_items(sale=False)
    for pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt in rows:
        cursor.execute("""INSERT INTO purchase_invoice_items
            (invoice_id, product_id, print_description, quantity, unit_id, rate, discount, cash_discount, hsn_id, bef_tax_amt, tax_rate, tax_amount, amount)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hid, pid, "", qty, unit, rate, disc, 0, hsn, bef, tax, taxa, amt))
    cursor.execute("UPDATE purchase_invoices SET total_amount=? WHERE id=?", (total, hid))
print("Purchase invoices: 80")

# ======================================================================
# PURCHASE GRN (80)
# ======================================================================
for _ in range(80):
    dt = rand_date()
    no = next_no("purchase_grn", "GRN-", dt)
    cursor.execute("""INSERT INTO purchase_grn
        (grn_no, grn_date, supplier_id, transporter_id, order_id, invoice_id, remarks, status, total_amount)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (no, dt, random.choice(supplier_ids), t_id(), None, None, "ZZ-seed", "pending", 0))
    hid = cursor.lastrowid
    rows, total = make_items(sale=False)
    for pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt in rows:
        cursor.execute("""INSERT INTO purchase_grn_items
            (grn_id, product_id, print_description, quantity, unit_id, rate, discount, hsn_id, bef_tax_amt, tax_rate, tax_amount, amount)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hid, pid, "", qty, unit, rate, disc, hsn, bef, tax, taxa, amt))
    cursor.execute("UPDATE purchase_grn SET total_amount=? WHERE id=?", (total, hid))
print("Purchase GRN: 80")

# ======================================================================
# PURCHASE RETURNS (30)
# ======================================================================
pinv_ids = [r["id"] for r in cursor.execute("SELECT id FROM purchase_invoices").fetchall()]
for _ in range(30):
    dt = rand_date()
    no = next_no("purchase_returns", "PR-", dt)
    inv = random.choice(pinv_ids) if pinv_ids else None
    cursor.execute("""INSERT INTO purchase_returns
        (return_no, return_date, supplier_id, transporter_id, invoice_id, reason, remarks, status, total_amount)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (no, dt, random.choice(supplier_ids), t_id(), inv, random.choice(reasons), "ZZ-seed", "pending", 0))
    hid = cursor.lastrowid
    rows, total = make_items(1, 3, sale=False)
    for pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt in rows:
        cursor.execute("""INSERT INTO purchase_return_items
            (return_id, product_id, print_description, quantity, unit_id, rate, discount, hsn_id, bef_tax_amt, tax_rate, tax_amount, amount, reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hid, pid, "", qty, unit, rate, disc, hsn, bef, tax, taxa, amt, random.choice(reasons)))
    cursor.execute("UPDATE purchase_returns SET total_amount=? WHERE id=?", (total, hid))
print("Purchase returns: 30")

conn.commit()
conn.close()
print("\nAll remaining modules seeded successfully!")
