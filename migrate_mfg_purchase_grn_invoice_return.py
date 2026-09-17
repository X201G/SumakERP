"""
SUMAK ERP - Manufacturing: Purchase GRN, Purchase Invoice, Purchase Return
(the rest of the Purchase module, mirroring Distribution's PO/GRN/Invoice/Return
structure - GRN is the stock-IN moment, Return is stock-OUT, Invoice is financial only)
Also adds a `discount` column to mfg_purchase_order_items for structural parity with
Distribution's item tables (was missing from the initial PO build).
Run once with: python migrate_mfg_purchase_grn_invoice_return.py
"""
from database import get_connection

conn = get_connection()
cursor = conn.cursor()

def add_column_if_missing(table, column, coltype):
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row["name"] for row in cursor.fetchall()]
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        print(f"✅ Added {column} to {table}")
    else:
        print(f"{column} already exists on {table}, skipping")

add_column_if_missing("mfg_purchase_order_items", "discount", "REAL DEFAULT 0")

cursor.execute('''
    CREATE TABLE IF NOT EXISTS mfg_purchase_grn (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grn_no TEXT NOT NULL UNIQUE,
    grn_date DATE NOT NULL,
    supplier_id INTEGER,
    order_id INTEGER,
    remarks TEXT,
    status TEXT DEFAULT 'pending',
    total_amount REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY (order_id) REFERENCES mfg_purchase_orders(id)
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS mfg_purchase_grn_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grn_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    item_id INTEGER,
    description TEXT NOT NULL,
    rod_grade TEXT,
    rod_diameter_mm REAL,
    rod_length_mm REAL,
    quantity REAL NOT NULL,
    unit_id INTEGER,
    rate REAL DEFAULT 0,
    discount REAL DEFAULT 0,
    hsn_code TEXT,
    tax_rate REAL DEFAULT 18.0,
    bef_tax_amt REAL DEFAULT 0,
    tax_amount REAL DEFAULT 0,
    amount REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (grn_id) REFERENCES mfg_purchase_grn(id),
    FOREIGN KEY (unit_id) REFERENCES units(id)
    )
''')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_grn_items_grn ON mfg_purchase_grn_items(grn_id)')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS mfg_purchase_invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no TEXT NOT NULL UNIQUE,
    invoice_date DATE NOT NULL,
    supplier_id INTEGER,
    order_id INTEGER,
    grn_id INTEGER,
    sup_invoice_no TEXT,
    sup_invoice_date DATE,
    cr_days INTEGER DEFAULT 0,
    due_date DATE,
    remarks TEXT,
    status TEXT DEFAULT 'unpaid',
    total_amount REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY (order_id) REFERENCES mfg_purchase_orders(id),
    FOREIGN KEY (grn_id) REFERENCES mfg_purchase_grn(id)
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS mfg_purchase_invoice_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    item_id INTEGER,
    description TEXT NOT NULL,
    rod_grade TEXT,
    rod_diameter_mm REAL,
    rod_length_mm REAL,
    quantity REAL NOT NULL,
    unit_id INTEGER,
    rate REAL DEFAULT 0,
    discount REAL DEFAULT 0,
    hsn_code TEXT,
    tax_rate REAL DEFAULT 18.0,
    bef_tax_amt REAL DEFAULT 0,
    tax_amount REAL DEFAULT 0,
    amount REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES mfg_purchase_invoices(id),
    FOREIGN KEY (unit_id) REFERENCES units(id)
    )
''')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_pinv_items_invoice ON mfg_purchase_invoice_items(invoice_id)')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS mfg_purchase_returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    return_no TEXT NOT NULL UNIQUE,
    return_date DATE NOT NULL,
    supplier_id INTEGER,
    invoice_id INTEGER,
    reason TEXT,
    remarks TEXT,
    status TEXT DEFAULT 'pending',
    total_amount REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY (invoice_id) REFERENCES mfg_purchase_invoices(id)
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS mfg_purchase_return_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    return_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    item_id INTEGER,
    description TEXT NOT NULL,
    rod_grade TEXT,
    rod_diameter_mm REAL,
    rod_length_mm REAL,
    quantity REAL NOT NULL,
    unit_id INTEGER,
    rate REAL DEFAULT 0,
    discount REAL DEFAULT 0,
    hsn_code TEXT,
    tax_rate REAL DEFAULT 18.0,
    bef_tax_amt REAL DEFAULT 0,
    tax_amount REAL DEFAULT 0,
    amount REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (return_id) REFERENCES mfg_purchase_returns(id),
    FOREIGN KEY (unit_id) REFERENCES units(id)
    )
''')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_pret_items_return ON mfg_purchase_return_items(return_id)')

conn.commit()
conn.close()
print("Migration done! GRN + Purchase Invoice + Purchase Return tables ready.")
