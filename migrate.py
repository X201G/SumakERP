from database import get_connection
conn = get_connection()
cursor = conn.cursor()
try:
    cursor.execute("ALTER TABLE sales_order_items ADD COLUMN billed_qty REAL DEFAULT 0")
    print("✅ Added billed_qty to sales_order_items")
except Exception as e:
    print("sales_order_items:", e)

try:
    cursor.execute("ALTER TABLE sales_challan_items ADD COLUMN billed_qty REAL DEFAULT 0")
    print("✅ Added billed_qty to sales_challan_items")
except Exception as e:
    print("sales_challan_items:", e)

try:
    cursor.execute("ALTER TABLE sales_invoice_items ADD COLUMN source_type TEXT")
    print("✅ Added source_type to sales_invoice_items")
except Exception as e:
    print("source_type:", e)

try:
    cursor.execute("ALTER TABLE sales_invoice_items ADD COLUMN source_item_id INTEGER")
    print("✅ Added source_item_id to sales_invoice_items")
except Exception as e:
    print("source_item_id:", e)
conn.commit()
conn.close()
print("migration done!")