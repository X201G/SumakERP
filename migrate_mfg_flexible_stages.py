"""
SUMAK ERP - Manufacturing: flexible stage combinations
Run once with: python migrate_mfg_flexible_stages.py

Reworks mfg_job_stages so ANY combination of stages (Blank Making,
Tool Manufacturing, Coating - any 1, 2, or all 3) sent together is
stored as ONE record, not glued-together separate rows. Also updates
mfg_challan_items to reference that one record directly.

Only safe to run when mfg_job_stages is empty - checks first and
refuses to run otherwise, since this changes the table's shape
(unlike the other migrate_*.py scripts here which just add columns).
"""
from database import get_connection, create_tables

conn = get_connection()
cursor = conn.cursor()

cursor.execute("SELECT COUNT(*) as c FROM mfg_job_stages")
count = cursor.fetchone()["c"]

if count > 0:
    print(f"⚠ mfg_job_stages has {count} row(s) - this migration would lose data.")
    print("Wipe Manufacturing data first, then re-run this.")
else:
    cursor.execute("DROP TABLE IF EXISTS mfg_job_stages")
    cursor.execute("DROP TABLE IF EXISTS mfg_challan_items")
    conn.commit()
    conn.close()
    create_tables()
    print("✅ Rebuilt mfg_job_stages and mfg_challan_items with flexible stage combinations.")
