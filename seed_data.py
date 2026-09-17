"""
SUMAK ERP - Bulk dummy data generator
Run once with:  python seed_data.py

Adds (defaults):
  - 4 units, 5 categories, 6 HSN codes
  - 50 customers
  - 30 suppliers
  - 200 products (realistic YG1-style cutting tool descriptions)
  - 100 sales invoices (each 1-5 items)
  - 100 purchase orders (each 1-5 items)

Safe to run on existing DB - it only ADDS rows, never deletes.
All dummy docs use a "ZZ" tag in remarks so you can find/delete them later if needed.
"""

import random
from datetime import date, timedelta
from database import get_connection

random.seed(42)  # makes the data repeatable

conn = get_connection()
cursor = conn.cursor()

# ----------------------------------------------------------------------
# 1. UNITS
# ----------------------------------------------------------------------
units = ["Nos", "Box", "Set", "Pkt"]
unit_ids = []
for u in units:
    try:
        cursor.execute("INSERT INTO units (unit_name) VALUES (?)", (u,))
        unit_ids.append(cursor.lastrowid)
    except Exception:
        # already exists - fetch its id
        row = cursor.execute("SELECT id FROM units WHERE unit_name = ?", (u,)).fetchone()
        unit_ids.append(row["id"])
print(f"Units ready: {len(unit_ids)}")

# ----------------------------------------------------------------------
# 2. CATEGORIES
# ----------------------------------------------------------------------
categories = ["Taps", "Drills", "End Mills", "Inserts", "Reamers"]
cat_ids = []
for c in categories:
    try:
        cursor.execute("INSERT INTO categories (category_name) VALUES (?)", (c,))
        cat_ids.append(cursor.lastrowid)
    except Exception:
        row = cursor.execute("SELECT id FROM categories WHERE category_name = ?", (c,)).fetchone()
        cat_ids.append(row["id"])
print(f"Categories ready: {len(cat_ids)}")

# ----------------------------------------------------------------------
# 3. HSN CODES
# ----------------------------------------------------------------------
hsn_codes = [
    ("82075000", "Tools for drilling", 18.0),
    ("82076000", "Tools for boring/broaching", 18.0),
    ("82079000", "Interchangeable tools", 18.0),
    ("82090090", "Carbide inserts/plates", 18.0),
    ("82077000", "Tools for milling", 18.0),
    ("82073000", "Tools for pressing/stamping", 18.0),
]
hsn_ids = []
for code, desc, rate in hsn_codes:
    try:
        cursor.execute("INSERT INTO hsn (hsn_code, description, gst_rate) VALUES (?,?,?)", (code, desc, rate))
        hsn_ids.append(cursor.lastrowid)
    except Exception:
        row = cursor.execute("SELECT id FROM hsn WHERE hsn_code = ?", (code,)).fetchone()
        hsn_ids.append(row["id"])
print(f"HSN ready: {len(hsn_ids)}")

# ----------------------------------------------------------------------
# 4. CUSTOMERS
# ----------------------------------------------------------------------
cust_prefixes = ["SR", "Shree", "Sai", "Om", "Jay", "Maha", "Bharat", "Pune", "Krishna",
                 "Ganesh", "Laxmi", "Tirupati", "Sumit", "Deccan", "Pioneer", "Precision",
                 "Apex", "Mega", "Super", "United", "National", "Global", "Star", "Royal",
                 "Eagle", "Falcon", "Sun", "Moon", "Diamond", "Gold"]
cust_suffixes = ["Auto", "Engineering", "Industries", "Enterprises", "Tools", "Motors",
                 "Components", "Fabricators", "Works", "Trading", "Agencies", "Systems",
                 "Technologies", "Manufacturing", "Precision Works"]
cities = [("Pune", "Maharashtra"), ("Mumbai", "Maharashtra"), ("Nashik", "Maharashtra"),
          ("Aurangabad", "Maharashtra"), ("Kolhapur", "Maharashtra"), ("Nagpur", "Maharashtra"),
          ("Satara", "Maharashtra"), ("Sangli", "Maharashtra")]

customer_ids = []
for i in range(50):
    name = f"{random.choice(cust_prefixes)} {random.choice(cust_suffixes)}"
    city, state = random.choice(cities)
    phone = "9" + "".join(random.choice("0123456789") for _ in range(9))
    email = name.lower().replace(" ", "") + "@example.com"
    gst = f"27{''.join(random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ0123456789') for _ in range(13))}"
    addr = f"Plot {random.randint(1,300)}, MIDC, {city}"
    cursor.execute("""INSERT INTO customers (name, phone, email, address, city, state, gst_number)
                      VALUES (?,?,?,?,?,?,?)""", (name, phone, email, addr, city, state, gst))
    customer_ids.append(cursor.lastrowid)
print(f"Customers added: {len(customer_ids)}")

# ----------------------------------------------------------------------
# 5. SUPPLIERS
# ----------------------------------------------------------------------
supp_names = ["YG1 India", "Sandvik Coromant", "Kennametal India", "Mitsubishi Materials",
              "Taegutec India", "Walter Tools", "Guhring India", "OSG India", "Dormer Pramet",
              "Widia India", "Iscar India", "Sumitomo Tools", "Kyocera Tools", "Tungaloy India",
              "Seco Tools", "Emuge Franken", "Vergnano Tools", "Addison Tools", "Miranda Tools",
              "Indian Tool Mfrs", "Totem Tools", "Forbes Tools", "JK Files", "Birla Tools",
              "Precision Carbide", "Nachi India", "Hitachi Tools", "Ceratizit India",
              "Hoffmann Group", "MMC Hardmetal"]
supplier_ids = []
for nm in supp_names:
    city, state = random.choice(cities)
    phone = "9" + "".join(random.choice("0123456789") for _ in range(9))
    email = nm.lower().replace(" ", "") + "@supplier.com"
    gst = f"27{''.join(random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ0123456789') for _ in range(13))}"
    addr = f"Industrial Area, {city}"
    cursor.execute("""INSERT INTO suppliers (name, phone, email, address, city, state, gst_number)
                      VALUES (?,?,?,?,?,?,?)""", (nm, phone, email, addr, city, state, gst))
    supplier_ids.append(cursor.lastrowid)
print(f"Suppliers added: {len(supplier_ids)}")

# ----------------------------------------------------------------------
# 6. PRODUCTS  (realistic YG1-style cutting tool descriptions)
# ----------------------------------------------------------------------
tap_sizes = ["M3x0.5", "M4x0.7", "M5x0.8", "M6x1.0", "M8x1.25", "M10x1.5", "M12x1.75",
             "M14x2.0", "M16x2.0", "M20x2.5", "M6x0.75", "M8x1.0", "M10x1.25", "M12x1.25"]
tap_types = ["SPFL", "SPPT", "STI", "HAND", "MF"]

drill_dia = ["3.0", "3.5", "4.0", "4.2", "5.0", "6.0", "6.8", "8.0", "8.5", "10.0",
             "10.2", "12.0", "13.0", "14.0", "16.0", "20.0"]
drill_types = ["HSS", "HSSE", "CARBIDE", "COBALT"]

em_dia = ["4", "6", "8", "10", "12", "16", "20", "25"]
em_flutes = ["2FL", "3FL", "4FL"]

insert_codes = ["TNMG160404", "TNMG160408", "CNMG120408", "CNMG120404", "DNMG150608",
                "WNMG080408", "VNMG160404", "SNMG120408", "CCMT09T304", "DCMT11T304",
                "TPMR160308", "APMT1604PDER"]
insert_grades = ["YG3115", "YG6020", "YG1010", "YG3010", "PC5300", "TT8125"]

reamer_dia = ["5.0", "6.0", "8.0", "10.0", "12.0", "16.0", "20.0"]

def rand_code(prefix):
    return prefix + "".join(random.choice("0123456789") for _ in range(7))

product_rows = []
# Build a big pool of unique descriptions
descs = set()
while len(descs) < 200:
    kind = random.choice(["tap", "drill", "em", "insert", "reamer"])
    if kind == "tap":
        d = f"YG1 {random.choice(tap_types)} {random.choice(tap_sizes)} TAP {rand_code('TD')}"
        cat = cat_ids[0]; hsn = hsn_ids[2]
    elif kind == "drill":
        d = f"YG1 D{random.choice(drill_dia)} {random.choice(drill_types)} DRILL {rand_code('DR')}"
        cat = cat_ids[1]; hsn = hsn_ids[0]
    elif kind == "em":
        d = f"YG1 D{random.choice(em_dia)} {random.choice(em_flutes)} CARBIDE END MILL {rand_code('EM')}"
        cat = cat_ids[2]; hsn = hsn_ids[4]
    elif kind == "insert":
        d = f"YG1 {random.choice(insert_codes)} {random.choice(insert_grades)} INSERT {rand_code('2200')}"
        cat = cat_ids[3]; hsn = hsn_ids[3]
    else:
        d = f"YG1 D{random.choice(reamer_dia)} HSS REAMER {rand_code('RM')}"
        cat = cat_ids[4]; hsn = hsn_ids[1]
    if d in descs:
        continue
    descs.add(d)
    product_rows.append((d, cat, hsn))

product_ids = []
product_info = {}  # id -> (unit_id, hsn_id, tax_rate, sale_price, purchase_price)
for desc, cat, hsn in product_rows:
    unit = unit_ids[0]  # Nos
    tax = 18.0
    purchase_price = round(random.uniform(50, 5000), 2)
    sale_price = round(purchase_price * random.uniform(1.15, 1.6), 2)
    supplier = random.choice(supplier_ids)
    cursor.execute("""INSERT INTO products
        (product_name, print_description, category_id, subcategory_id, unit_id, hsn_id,
         tax_rate, supplier_id, sale_price, sale_discount, purchase_price, purchase_discount)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (desc, desc, cat, None, unit, hsn, tax, supplier, sale_price, 0, purchase_price, 0))
    pid = cursor.lastrowid
    product_ids.append(pid)
    product_info[pid] = (unit, hsn, tax, sale_price, purchase_price)
print(f"Products added: {len(product_ids)}")

conn.commit()

# ----------------------------------------------------------------------
# helper for line math
# ----------------------------------------------------------------------
def calc(qty, rate, discount, tax_rate):
    bef = qty * rate * (1 - discount / 100)
    tax = bef * tax_rate / 100
    amt = bef + tax
    return round(bef, 2), round(tax, 2), round(amt, 2)

def rand_date(start_days_ago=180):
    return (date.today() - timedelta(days=random.randint(0, start_days_ago))).isoformat()

# ----------------------------------------------------------------------
# 7. SALES INVOICES (100)
# ----------------------------------------------------------------------
si_count = cursor.execute("SELECT COUNT(*) FROM sales_invoices").fetchone()[0]
for n in range(100):
    cust = random.choice(customer_ids)
    inv_date = rand_date()
    si_count += 1
    inv_no = "SI-" + inv_date.replace("-", "") + "-" + str(si_count).zfill(4)
    cursor.execute("""INSERT INTO sales_invoices
        (invoice_no, invoice_date, customer_id, transporter_id, po_no, po_date, cr_days, due_date, remarks, status)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (inv_no, inv_date, cust, None, f"CPO{random.randint(100,999)}", inv_date, 30, inv_date,
         "ZZ-seed", "unpaid"))
    inv_id = cursor.lastrowid
    total = 0
    for _ in range(random.randint(1, 5)):
        pid = random.choice(product_ids)
        unit, hsn, tax, sale_price, _ = product_info[pid]
        qty = random.randint(1, 50)
        rate = sale_price
        disc = random.choice([0, 0, 5, 10])
        bef, taxa, amt = calc(qty, rate, disc, tax)
        total += amt
        cursor.execute("""INSERT INTO sales_invoice_items
            (invoice_id, product_id, unit_id, hsn_id, quantity, rate, discount, tax_rate,
             bef_tax_amt, tax_amount, amount)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (inv_id, pid, unit, hsn, qty, rate, disc, tax, bef, taxa, amt))
    cursor.execute("UPDATE sales_invoices SET total_amount=? WHERE id=?", (round(total, 2), inv_id))
print("Sales invoices added: 100")

# ----------------------------------------------------------------------
# 8. PURCHASE ORDERS (100)
# ----------------------------------------------------------------------
po_count = cursor.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0]
for n in range(100):
    supp = random.choice(supplier_ids)
    o_date = rand_date()
    po_count += 1
    po_no = "PO-" + o_date.replace("-", "") + "-" + str(po_count).zfill(4)
    cursor.execute("""INSERT INTO purchase_orders
        (order_no, order_date, supplier_id, transporter_id, address, kind_attn, sup_ref_no,
         cr_days, valid_till, remarks, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (po_no, o_date, supp, None, "", "", "", 30, o_date, "ZZ-seed", "pending"))
    o_id = cursor.lastrowid
    total = 0
    for _ in range(random.randint(1, 5)):
        pid = random.choice(product_ids)
        unit, hsn, tax, _, purchase_price = product_info[pid]
        qty = random.randint(5, 100)
        rate = purchase_price
        disc = random.choice([0, 0, 5, 10])
        bef, taxa, amt = calc(qty, rate, disc, tax)
        total += amt
        cursor.execute("""INSERT INTO purchase_order_items
            (order_id, product_id, print_description, quantity, unit_id, rate, discount,
             cash_discount, hsn_id, bef_tax_amt, tax_rate, tax_amount, amount)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (o_id, pid, "", qty, unit, rate, disc, 0, hsn, bef, tax, taxa, amt))
    cursor.execute("UPDATE purchase_orders SET total_amount=? WHERE id=?", (round(total, 2), o_id))
print("Purchase orders added: 100")

conn.commit()
conn.close()
print("\nâ All bulk data inserted successfully!")
print("Dummy transactions are tagged 'ZZ-seed' in remarks if you ever want to find them.")
