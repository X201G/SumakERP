"""
SUMAK ERP - Manufacturing: track rod length precisely
Run once with: python migrate_mfg_rod_length.py

What this does:
1. Adds remaining_length_mm to mfg_rods (how much of this rod is left to use)
   and backfills it to the rod's full length for any rod that doesn't have
   it set yet.
2. Adds length_needed_mm to mfg_job_batches (how much rod length this batch
   is expected to use: (tool overall length + 4mm cutting allowance) x quantity).
3. Adds rod_returned_length_mm to mfg_job_stages (the actual remaining rod
   length reported when a Blank Making job comes back).

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

add_column_if_missing("mfg_rods", "remaining_length_mm", "REAL")
add_column_if_missing("mfg_job_batches", "length_needed_mm", "REAL")
add_column_if_missing("mfg_job_stages", "rod_returned_length_mm", "REAL")

cursor.execute("UPDATE mfg_rods SET remaining_length_mm = length_mm WHERE remaining_length_mm IS NULL")
print(f"✅ Backfilled remaining_length_mm for {cursor.rowcount} existing rod(s)")

conn.commit()
conn.close()
print("Migration done!")
