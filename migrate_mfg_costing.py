"""
SUMAK ERP - Manufacturing: cost tracking
Run once with: python migrate_mfg_costing.py

What this does:
1. Adds weight_kg to mfg_rods (optional, for your own reference when a rod
   arrives - not used in any calculation).
2. Adds rate to mfg_job_stages (what the vendor charged for that stage,
   entered when you mark it received).

Safe to re-run: skips any column that already exists.
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

add_column_if_missing("mfg_rods", "weight_kg", "REAL")
add_column_if_missing("mfg_job_stages", "rate", "REAL")

conn.commit()
conn.close()
print("Migration done!")
