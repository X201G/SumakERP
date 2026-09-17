"""
SUMAK ERP - Manufacturing: customer on Tool Master
Run once with: python migrate_mfg_tool_customer.py
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

add_column_if_missing("mfg_tools", "customer_id", "INTEGER")

conn.commit()
conn.close()
print("Migration done!")
