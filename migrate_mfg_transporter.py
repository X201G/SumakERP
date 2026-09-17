from database import get_connection
conn = get_connection()
cursor = conn.cursor()

for table in ["mfg_challans", "mfg_resharpen_challans", "mfg_purchase_orders", "mfg_purchase_grn", "mfg_purchase_invoices", "mfg_purchase_returns"]:
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN transporter_id INTEGER")
        print(f"✅ Added transporter_id to {table}")
    except Exception as e:
        print(f"{table}:", e)

conn.commit()
conn.close()
print("migration done!")
