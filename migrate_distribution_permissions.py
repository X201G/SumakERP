"""
SUMAK ERP - Move Distribution logins onto the permissions grid

Run once with: python migrate_distribution_permissions.py
Run it right after deploying the new code, on EACH database (local and the live server).

Why this keeps everyone working:
Until now every Distribution login could reach every Distribution screen, whatever
its role. So every existing non-admin Distribution user is given FULL access to every
Distribution section - exactly what they had before. You then tighten each person
from Users -> Edit Grid.

What this does:
1. For each non-admin Distribution user who has no Distribution permissions yet,
   writes full view/create/edit/delete on every Distribution section.
2. Changes their role to "staff" (the old sales/purchase/store/accounts roles are retired).
3. Admins are untouched (admins always have full access).

Safe to re-run: users who already have any Distribution permission rows are skipped,
so a grid you've already tightened is never overwritten.
"""

from database import get_connection
from modules.permissions import DIST_SECTIONS

section_keys = [key for key, _ in DIST_SECTIONS]

conn = get_connection()
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='permissions'")
if not cursor.fetchone():
    raise SystemExit("No 'permissions' table found - run the app once (create_tables) first.")

placeholders = ",".join("?" * len(section_keys))
cursor.execute("SELECT id, username, role FROM users WHERE system = 'distribution' AND role != 'admin'")
moved, skipped = [], []
for user in cursor.fetchall():
    cursor.execute(
        f"SELECT COUNT(*) FROM permissions WHERE user_id = ? AND section IN ({placeholders})",
        (user["id"], *section_keys),
    )
    if cursor.fetchone()[0] > 0:
        skipped.append(user["username"])
        continue
    for section in section_keys:
        cursor.execute(
            "INSERT INTO permissions (user_id, section, can_view, can_create, can_edit, can_delete) VALUES (?,?,?,?,?,?)",
            (user["id"], section, 1, 1, 1, 1),
        )
    cursor.execute("UPDATE users SET role = 'staff' WHERE id = ?", (user["id"],))
    moved.append((user["username"], user["role"]))

conn.commit()
conn.close()

for username, role in moved:
    print(f"✅ {username} (was '{role}'): full access kept, role is now 'staff' - tighten from Users -> Edit Grid")
for username in skipped:
    print(f"↩️  {username}: already has a Distribution grid, left as is")
print("Migration done!")
