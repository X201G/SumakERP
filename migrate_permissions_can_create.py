from database import get_connection
conn = get_connection()
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE permissions ADD COLUMN can_create INTEGER NOT NULL DEFAULT 0")
    print("✅ Added can_create to permissions")
except Exception as e:
    print("permissions:", e)

conn.commit()
conn.close()
print("migration done!")
