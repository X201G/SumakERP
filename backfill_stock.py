"""
SUMAK ERP - Stock back-fill (with RATE + AMOUNT)
Run with:  python backfill_stock.py

1. Gives every product an OPENING stock of 500 (rate = purchase_price)
2. Replays all documents into stock_movements WITH rate + amount:
     Purchase GRN      -> IN
     Sales Challan     -> OUT
     Sales Return      -> IN
     Purchase Return   -> OUT
     Sales Invoice     -> OUT (only items NOT sourced from a challan)

Safe to re-run: WIPES stock_movements first, then rebuilds.
"""

from datetime import date
from database import get_connection

OPENING_QTY = 500

conn = get_connection()
cursor = conn.cursor()

cursor.execute("DELETE FROM stock_movements")
print("Cleared old stock_movements")

def move(product_id, mtype, qty, stype, sid, sno, mdate, rate=0):
    amount = (qty or 0) * (rate or 0)
    cursor.execute("""
        INSERT INTO stock_movements
        (product_id, movement_type, quantity, source_type, source_id, source_no, movement_date, rate, amount)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (product_id, mtype, qty, stype, sid, sno, mdate, rate, round(amount, 2)))

# 1. Opening stock (rate = product purchase_price)
today = date.today().isoformat()
products = cursor.execute("SELECT id, purchase_price FROM products").fetchall()
for p in products:
    move(p["id"], "IN", OPENING_QTY, "opening", None, "OPENING", today, p["purchase_price"] or 0)
print(f"Opening stock {OPENING_QTY} added for {len(products)} products")

# 2. Purchase GRN -> IN
rows = cursor.execute("""
    SELECT i.product_id, i.quantity, i.rate, g.id as gid, g.grn_no, g.grn_date
    FROM purchase_grn_items i JOIN purchase_grn g ON i.grn_id = g.id
""").fetchall()
for r in rows:
    move(r["product_id"], "IN", r["quantity"], "grn", r["gid"], r["grn_no"], r["grn_date"], r["rate"])
print(f"GRN movements: {len(rows)}")

# 3. Sales Challan -> OUT
rows = cursor.execute("""
    SELECT i.product_id, i.quantity, i.rate, c.id as cid, c.challan_no, c.challan_date
    FROM sales_challan_items i JOIN sales_challans c ON i.challan_id = c.id
""").fetchall()
for r in rows:
    move(r["product_id"], "OUT", r["quantity"], "challan", r["cid"], r["challan_no"], r["challan_date"], r["rate"])
print(f"Challan movements: {len(rows)}")

# 4. Sales Return -> IN
rows = cursor.execute("""
    SELECT i.product_id, i.quantity, i.rate, r.id as rid, r.return_no, r.return_date
    FROM sales_return_items i JOIN sales_returns r ON i.return_id = r.id
""").fetchall()
for r in rows:
    move(r["product_id"], "IN", r["quantity"], "sales_return", r["rid"], r["return_no"], r["return_date"], r["rate"])
print(f"Sales return movements: {len(rows)}")

# 5. Purchase Return -> OUT
rows = cursor.execute("""
    SELECT i.product_id, i.quantity, i.rate, r.id as rid, r.return_no, r.return_date
    FROM purchase_return_items i JOIN purchase_returns r ON i.return_id = r.id
""").fetchall()
for r in rows:
    move(r["product_id"], "OUT", r["quantity"], "purchase_return", r["rid"], r["return_no"], r["return_date"], r["rate"])
print(f"Purchase return movements: {len(rows)}")

# 6. Sales Invoice -> OUT (skip challan-sourced)
rows = cursor.execute("""
    SELECT i.product_id, i.quantity, i.rate, i.source_type, s.id as sid, s.invoice_no, s.invoice_date
    FROM sales_invoice_items i JOIN sales_invoices s ON i.invoice_id = s.id
""").fetchall()
count = 0
for r in rows:
    if (r["source_type"] or "") == "challan":
        continue
    move(r["product_id"], "OUT", r["quantity"], "sales_invoice", r["sid"], r["invoice_no"], r["invoice_date"], r["rate"])
    count += 1
print(f"Sales invoice movements: {count} (challan-sourced skipped)")

conn.commit()
conn.close()
print("\nStock back-fill (with rate) complete!")
