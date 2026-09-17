from database import get_connection
conn = get_connection()
cursor = conn.cursor()
try:
    cursor.execute("ALTER TABLE mfg_job_stages ADD COLUMN purchase_invoice_id INTEGER")
    print("✅ Added purchase_invoice_id to mfg_job_stages")
except Exception as e:
    print("mfg_job_stages:", e)

try:
    cursor.execute("ALTER TABLE mfg_resharpen_stages ADD COLUMN purchase_invoice_id INTEGER")
    print("✅ Added purchase_invoice_id to mfg_resharpen_stages")
except Exception as e:
    print("mfg_resharpen_stages:", e)

conn.commit()
conn.close()
print("migration done!")
