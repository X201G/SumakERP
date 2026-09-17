"""
SUMAK ERP - Manufacturing: item codes + Brand Master on Tool Master
(e.g. DR0001, or DRSA0001 for a tool with a brand starting "Sa...")
Run once with: python migrate_mfg_tool_item_code.py
"""
from database import get_connection

TOOL_TYPE_CODE_PREFIX = {
    "Drill": "DR",
    "Endmill": "EM",
    "Reamer": "RM",
    "Holemill": "HM",
    "Tap": "TP",
    "Special Tool": "ST",
}

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

cursor.execute('''
    CREATE TABLE IF NOT EXISTS mfg_brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_type TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
''')
cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_mfg_brands_type_name ON mfg_brands(tool_type, name)")
print("mfg_brands table ensured (scoped per tool type)")

add_column_if_missing("mfg_tools", "item_code", "TEXT")
add_column_if_missing("mfg_tools", "brand_id", "INTEGER")
cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_mfg_tools_item_code ON mfg_tools(item_code)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_mfg_tools_brand ON mfg_tools(brand_id)")

cursor.execute("""
    SELECT mfg_tools.id, mfg_tools.tool_type, mfg_brands.name as brand_name
    FROM mfg_tools
    LEFT JOIN mfg_brands ON mfg_tools.brand_id = mfg_brands.id
    WHERE mfg_tools.item_code IS NULL
    ORDER BY mfg_tools.id
""")
missing = cursor.fetchall()
if missing:
    print(f"Backfilling item codes for {len(missing)} existing tool(s)...")
    for row in missing:
        prefix = TOOL_TYPE_CODE_PREFIX.get(row["tool_type"], "XX")
        brand_code = row["brand_name"][:2].upper() if row["brand_name"] else ""
        code_base = f"{prefix}{brand_code}"
        cursor.execute("SELECT item_code FROM mfg_tools WHERE item_code LIKE ?", (f"{code_base}%",))
        existing_numbers = []
        for r in cursor.fetchall():
            try:
                existing_numbers.append(int(r["item_code"][len(code_base):]))
            except (ValueError, TypeError):
                pass
        next_number = (max(existing_numbers) + 1) if existing_numbers else 1
        item_code = f"{code_base}{str(next_number).zfill(4)}"
        cursor.execute("UPDATE mfg_tools SET item_code = ? WHERE id = ?", (item_code, row["id"]))
        print(f"  tool id {row['id']} ({row['tool_type']}) -> {item_code}")
else:
    print("No existing tools missing an item code.")

conn.commit()
conn.close()
print("Migration done!")
