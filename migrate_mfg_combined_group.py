"""
SUMAK ERP - Manufacturing: track which stages were sent together
Run once with: python migrate_mfg_combined_group.py

Adds combined_group to mfg_job_stages - when several stages are sent
together as "Full Manufacturing", they all get the same combined_group
value (the id of the first stage in that group), so the batch page can
show them as one line instead of one per stage.

Safe to re-run: skips if the column already exists.
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

add_column_if_missing("mfg_job_stages", "combined_group", "INTEGER")

conn.commit()
conn.close()
print("Migration done!")
