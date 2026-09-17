from database import get_connection
conn = get_connection()
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE mfg_invoice_items ADD COLUMN order_item_id INTEGER")
    print("✅ Added order_item_id to mfg_invoice_items")
except Exception as e:
    print("mfg_invoice_items:", e)

conn.commit()
conn.close()
print("migration done!")
