"""
SUMAK ERP - Manufacturing: Purchase Orders (covers rods, generic items, and bought-in
finished tools - each PO line's item_type says which).
Run once with: python migrate_mfg_purchase_orders.py
"""
from database import get_connection

conn = get_connection()
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS mfg_purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL UNIQUE,
    order_date DATE NOT NULL,
    supplier_id INTEGER,
    remarks TEXT,
    status TEXT DEFAULT 'pending',
    total_amount REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS mfg_purchase_order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    item_id INTEGER,
    description TEXT NOT NULL,
    rod_grade TEXT,
    rod_diameter_mm REAL,
    rod_length_mm REAL,
    quantity REAL NOT NULL,
    unit_id INTEGER,
    rate REAL DEFAULT 0,
    hsn_code TEXT,
    tax_rate REAL DEFAULT 18.0,
    bef_tax_amt REAL DEFAULT 0,
    tax_amount REAL DEFAULT 0,
    amount REAL DEFAULT 0,
    received_qty REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES mfg_purchase_orders(id),
    FOREIGN KEY (unit_id) REFERENCES units(id)
    )
''')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_po_items_order ON mfg_purchase_order_items(order_id)')

conn.commit()
conn.close()
print("Migration done! mfg_purchase_orders + mfg_purchase_order_items ready.")
