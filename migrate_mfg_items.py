"""
SUMAK ERP - Manufacturing: Item Master (grinding wheels, oils, other purchasable consumables)
Category is Manufacturing's own list (Distribution's shared categories are tool-product-specific,
not appropriate for classifying purchase items). HSN is free text, not a dropdown - Kiran wants
to type it directly, with smart suggestions to come later. Unit still reuses Distribution's
shared Unit master, same pattern as sharing Customers/Suppliers.
Run once with: python migrate_mfg_items.py
"""
from database import get_connection

conn = get_connection()
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS mfg_item_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
''')

cursor.execute("SELECT COUNT(*) c FROM mfg_items")
count = cursor.fetchone()["c"]
if count == 0:
    cursor.execute("DROP TABLE IF EXISTS mfg_items")
    print("dropped empty mfg_items table (rebuilding with new category/HSN shape)")
else:
    print(f"mfg_items has {count} row(s) - NOT dropping, leaving existing table as-is")

cursor.execute('''
    CREATE TABLE IF NOT EXISTS mfg_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_name TEXT NOT NULL,
    print_description TEXT,
    category_id INTEGER,
    unit_id INTEGER,
    hsn_code TEXT,
    tax_rate REAL DEFAULT 18.0,
    purchase_price REAL DEFAULT 0,
    remarks TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES mfg_item_categories(id),
    FOREIGN KEY (unit_id) REFERENCES units(id)
    )
''')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_items_category ON mfg_items(category_id)')

conn.commit()
conn.close()
print("Migration done!")
