"""
SUMAK ERP - Manufacturing: Sales Payments + Purchase Payments
Adds mfg_sales_payments (money received from customers against mfg_invoices)
and mfg_purchase_payments (money paid to suppliers against mfg_purchase_invoices),
mirroring Distribution's sales_payments/purchase_payments shape exactly.
Purely additive - CREATE TABLE IF NOT EXISTS, safe to re-run.
Run once with: python migrate_mfg_payments.py
"""
from database import get_connection

conn = get_connection()
cursor = conn.cursor()

def add_column_if_missing(table, column, coltype):
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row["name"] for row in cursor.fetchall()]
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        print(f"Added {column} to {table}")
    else:
        print(f"{column} already exists on {table}, skipping")

add_column_if_missing("mfg_invoices", "cr_days", "INTEGER DEFAULT 0")
add_column_if_missing("mfg_invoices", "due_date", "DATE")

cursor.execute('''
    CREATE TABLE IF NOT EXISTS mfg_sales_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER,
    customer_id INTEGER NOT NULL,
    payment_date DATE NOT NULL,
    amount REAL NOT NULL,
    payment_mode TEXT DEFAULT 'cash',
    reference_no TEXT,
    remarks TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES mfg_invoices(id),
    FOREIGN KEY (customer_id) REFERENCES customers(id)
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS mfg_purchase_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER,
    supplier_id INTEGER NOT NULL,
    payment_date DATE NOT NULL,
    amount REAL NOT NULL,
    payment_mode TEXT DEFAULT 'cash',
    reference_no TEXT,
    remarks TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES mfg_purchase_invoices(id),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    )
''')

cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_sales_payments_customer ON mfg_sales_payments(customer_id)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_sales_payments_invoice ON mfg_sales_payments(invoice_id)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_purchase_payments_supplier ON mfg_purchase_payments(supplier_id)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_purchase_payments_invoice ON mfg_purchase_payments(invoice_id)')

conn.commit()
conn.close()
print("Migration done! mfg_sales_payments + mfg_purchase_payments tables ready.")
