"""
SUMAK ERP - Add Manufacturing login support
Run once with: python migrate_manufacturing_login.py

What this does:
1. Adds a "system" column to the users table (values: "distribution" or "manufacturing")
   so a login can be restricted to one system or the other.
2. Marks all existing users as "distribution" (since that's the only system that existed
   before now) - no one's login breaks.
3. Creates a starter Manufacturing login: username "mfgadmin", password "mfg123"
   (change this later!) so you have something to log in with immediately.

Safe to re-run: skips steps that are already done.
"""

from database import get_connection
from werkzeug.security import generate_password_hash

conn = get_connection()
cursor = conn.cursor()

cursor.execute("PRAGMA table_info(users)")
columns = [row["name"] for row in cursor.fetchall()]

if "system" not in columns:
    cursor.execute("ALTER TABLE users ADD COLUMN system TEXT NOT NULL DEFAULT 'distribution'")
    print("✅ Added 'system' column to users")
else:
    print("'system' column already exists, skipping")

cursor.execute("SELECT COUNT(*) FROM users WHERE username = 'mfgadmin'")
if cursor.fetchone()[0] == 0:
    cursor.execute(
        "INSERT INTO users (username, password_hash, full_name, role, system) VALUES (?,?,?,?,?)",
        ("mfgadmin", generate_password_hash("mfg123"), "Manufacturing Administrator", "admin", "manufacturing")
    )
    print("✅ Manufacturing admin created -> username: mfgadmin | password: mfg123 (change this later!)")
else:
    print("mfgadmin already exists, skipping")

conn.commit()
conn.close()
print("Migration done!")
