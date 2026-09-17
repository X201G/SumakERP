from database import get_connection

conn = get_connection()
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE stock_movements ADD COLUMN rate REAL DEFAULT 0")
    print("✅ Added rate to stock_movements")
except Exception as e:
    print("rate", e)

try:
    cursor.execute("ALTER TABLE stock_movements ADD COLUMN amount REAL DEFAULT 0")
    print("✅ Added amount to stock_movements")
except Exception as e:
    print("amount:", e)

conn.commit()
conn.close()
print("Migration done!")