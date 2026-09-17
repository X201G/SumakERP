"""
SUMAK ERP - Add "on account" payment support
Run once with: python migrate_payments_on_account.py

What this does:
1. Adds a customer_id column to sales_payments (so a payment can exist even
   without picking one specific invoice - useful for "on account" payments,
   where a customer pays some amount without saying which invoice it's for).
2. Adds a supplier_id column to purchase_payments, same idea.
3. Removes the "must have an invoice" rule on both tables, so invoice_id
   can now be left empty.
4. Fills in customer_id / supplier_id on all EXISTING payment rows by
   looking up which invoice they belonged to - no existing data is lost.

Safe to re-run: if the columns already exist, it does nothing.
"""

from database import get_connection

conn = get_connection()
cursor = conn.cursor()

def column_exists(table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row["name"] == column for row in cursor.fetchall())

# ---------- sales_payments ----------
if column_exists("sales_payments", "customer_id"):
    print("sales_payments already migrated, skipping.")
else:
    print("Migrating sales_payments...")
    cursor.execute("""
        CREATE TABLE sales_payments_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER,
        customer_id INTEGER NOT NULL,
        payment_date DATE NOT NULL,
        amount REAL NOT NULL,
        payment_mode TEXT DEFAULT 'cash',
        reference_no TEXT,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (invoice_id) REFERENCES sales_invoices(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    """)
    cursor.execute("""
        INSERT INTO sales_payments_new
            (id, invoice_id, customer_id, payment_date, amount, payment_mode, reference_no, remarks, created_at)
        SELECT sp.id, sp.invoice_id, si.customer_id, sp.payment_date, sp.amount, sp.payment_mode, sp.reference_no, sp.remarks, sp.created_at
        FROM sales_payments sp
        JOIN sales_invoices si ON sp.invoice_id = si.id
    """)
    cursor.execute("DROP TABLE sales_payments")
    cursor.execute("ALTER TABLE sales_payments_new RENAME TO sales_payments")
    print("sales_payments migrated.")

# ---------- purchase_payments ----------
if column_exists("purchase_payments", "supplier_id"):
    print("purchase_payments already migrated, skipping.")
else:
    print("Migrating purchase_payments...")
    cursor.execute("""
        CREATE TABLE purchase_payments_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER,
        supplier_id INTEGER NOT NULL,
        payment_date DATE NOT NULL,
        amount REAL NOT NULL,
        payment_mode TEXT DEFAULT 'cash',
        reference_no TEXT,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (invoice_id) REFERENCES purchase_invoices(id),
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
        )
    """)
    cursor.execute("""
        INSERT INTO purchase_payments_new
            (id, invoice_id, supplier_id, payment_date, amount, payment_mode, reference_no, remarks, created_at)
        SELECT pp.id, pp.invoice_id, pi.supplier_id, pp.payment_date, pp.amount, pp.payment_mode, pp.reference_no, pp.remarks, pp.created_at
        FROM purchase_payments pp
        JOIN purchase_invoices pi ON pp.invoice_id = pi.id
    """)
    cursor.execute("DROP TABLE purchase_payments")
    cursor.execute("ALTER TABLE purchase_payments_new RENAME TO purchase_payments")
    print("purchase_payments migrated.")

conn.commit()
conn.close()
print("Migration done!")
