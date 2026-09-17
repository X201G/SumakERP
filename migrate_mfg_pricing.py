from database import get_connection
conn = get_connection()
cursor = conn.cursor()

columns = [
    ("mfg_rods", "purchase_price", "REAL"),
    ("mfg_rods", "purchase_discount_percent", "REAL DEFAULT 0"),
    ("mfg_rods", "sales_price", "REAL"),
    ("mfg_rods", "sales_discount_percent", "REAL DEFAULT 0"),
    ("mfg_tools", "purchase_price", "REAL"),
    ("mfg_tools", "purchase_discount_percent", "REAL DEFAULT 0"),
    ("mfg_tools", "sales_price", "REAL"),
    ("mfg_tools", "sales_discount_percent", "REAL DEFAULT 0"),
    ("mfg_items", "purchase_discount_percent", "REAL DEFAULT 0"),
    ("mfg_items", "sales_price", "REAL"),
    ("mfg_items", "sales_discount_percent", "REAL DEFAULT 0"),
]

for table, col, coltype in columns:
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
        print(f"✅ Added {col} to {table}")
    except Exception as e:
        print(f"{table}.{col}:", e)

conn.commit()
conn.close()
print("migration done!")
