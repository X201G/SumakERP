from flask import g, session, request, flash, redirect, url_for
from database import get_connection

# ============================================
# SECTION REGISTRY
# ============================================
# One row per feature. Keys are namespaced (mfg_ / resharpen_) so Distribution's
# own sections can be added into this same table/mechanism later without a rework.

SECTIONS = [
    ("mfg_dashboard", "Dashboard"),
    ("mfg_rods", "Rods"),
    ("mfg_tools", "Tools"),
    ("mfg_brands", "Brands"),
    ("mfg_item_categories", "Item Categories"),
    ("mfg_items", "Items"),
    ("mfg_transporters", "Transporters"),
    ("mfg_batches", "Job Work Batches"),
    ("mfg_challans", "Job Work Challans"),
    ("mfg_invoices", "Job Work Invoices"),
    ("mfg_orders", "Customer Orders"),
    ("mfg_stock", "Stock"),
    ("mfg_purchase_orders", "Purchase Orders"),
    ("mfg_purchase_grn", "Purchase GRN"),
    ("mfg_purchase_invoices", "Purchase Invoices"),
    ("mfg_purchase_returns", "Purchase Returns"),
    ("mfg_sales_payments", "Sales Payments"),
    ("mfg_purchase_payments", "Purchase Payments"),
    ("mfg_outstanding", "Outstanding / Day Book"),
    ("mfg_reports", "Reports"),
    ("resharpen_batches", "Resharpen Batches"),
    ("resharpen_challans", "Resharpen Challans"),
    ("resharpen_invoices", "Resharpen Invoices"),
    ("resharpen_stock", "Resharpen Stock"),
]

SECTION_LABELS = dict(SECTIONS)

# Same sections, grouped for the permissions grid UI so the admin isn't scanning
# one long flat list. Purely a display grouping - enforcement is still per-section.
SECTION_GROUPS = [
    ("General", ["mfg_dashboard"]),
    ("Masters", ["mfg_rods", "mfg_tools", "mfg_brands", "mfg_item_categories", "mfg_items", "mfg_transporters"]),
    ("Job Work (New Tool)", ["mfg_orders", "mfg_batches", "mfg_challans", "mfg_invoices"]),
    ("Purchasing", ["mfg_purchase_orders", "mfg_purchase_grn", "mfg_purchase_invoices", "mfg_purchase_returns"]),
    ("Stock", ["mfg_stock"]),
    ("Payments & Accounting", ["mfg_sales_payments", "mfg_purchase_payments", "mfg_outstanding"]),
    ("Reports", ["mfg_reports"]),
    ("Resharpening", ["resharpen_batches", "resharpen_challans", "resharpen_invoices", "resharpen_stock"]),
]

# Endpoints that any logged-in Manufacturing user may always reach, regardless of
# their permission grid - a safe landing page so a user with nothing granted yet
# (including no Dashboard access) never hits a redirect loop.
ALWAYS_ALLOWED_ENDPOINTS = {
    "manufacturing.no_access",
}

# ============================================
# EXACT ROUTE -> (section, action) MAP
# ============================================

ENDPOINT_MAP = {
    # ---- manufacturing.py : dashboard ----
    "manufacturing.index": ("mfg_dashboard", "view"),

    # ---- manufacturing.py : masters ----
    "manufacturing.rods_list": ("mfg_rods", "view"),
    "manufacturing.add_rod": ("mfg_rods", "create"),
    "manufacturing.edit_rod": ("mfg_rods", "edit"),
    "manufacturing.delete_rod": ("mfg_rods", "delete"),
    "manufacturing.tools_list": ("mfg_tools", "view"),
    "manufacturing.add_tool": ("mfg_tools", "create"),
    "manufacturing.edit_tool": ("mfg_tools", "edit"),
    "manufacturing.delete_tool": ("mfg_tools", "delete"),
    "manufacturing.brands_list": ("mfg_brands", "view"),
    "manufacturing.add_brand": ("mfg_brands", "create"),
    "manufacturing.edit_brand": ("mfg_brands", "edit"),
    "manufacturing.delete_brand": ("mfg_brands", "delete"),
    "manufacturing.item_categories_list": ("mfg_item_categories", "view"),
    "manufacturing.add_item_category": ("mfg_item_categories", "create"),
    "manufacturing.edit_item_category": ("mfg_item_categories", "edit"),
    "manufacturing.delete_item_category": ("mfg_item_categories", "delete"),
    "manufacturing.items_list": ("mfg_items", "view"),
    "manufacturing.add_item": ("mfg_items", "create"),
    "manufacturing.edit_item": ("mfg_items", "edit"),
    "manufacturing.delete_item": ("mfg_items", "delete"),
    "manufacturing.transporters_list": ("mfg_transporters", "view"),
    "manufacturing.add_transporter": ("mfg_transporters", "create"),
    "manufacturing.edit_transporter": ("mfg_transporters", "edit"),
    "manufacturing.delete_transporter": ("mfg_transporters", "delete"),

    # ---- manufacturing.py : job-work batches / challans / invoices ----
    "manufacturing.batches_list": ("mfg_batches", "view"),
    "manufacturing.add_batch": ("mfg_batches", "create"),
    "manufacturing.batch_detail": ("mfg_batches", "view"),
    "manufacturing.edit_batch": ("mfg_batches", "edit"),
    "manufacturing.delete_batch": ("mfg_batches", "delete"),
    "manufacturing.send_operation": ("mfg_batches", "edit"),
    "manufacturing.receive_operation": ("mfg_batches", "edit"),
    "manufacturing.create_vendor_invoice": ("mfg_purchase_invoices", "create"),
    "manufacturing.generate_challan": ("mfg_challans", "create"),
    "manufacturing.pick_batch_for_delivery": ("mfg_challans", "view"),
    "manufacturing.challans_list": ("mfg_challans", "view"),
    "manufacturing.view_challan": ("mfg_challans", "view"),
    "manufacturing.edit_challan": ("mfg_challans", "edit"),
    "manufacturing.delete_challan": ("mfg_challans", "delete"),
    "manufacturing.add_customer_challan": ("mfg_challans", "create"),
    "manufacturing.print_challan": ("mfg_challans", "view"),
    "manufacturing.pick_batch_for_invoice": ("mfg_invoices", "view"),
    "manufacturing.invoices_list": ("mfg_invoices", "view"),
    "manufacturing.view_invoice": ("mfg_invoices", "view"),
    "manufacturing.edit_invoice": ("mfg_invoices", "edit"),
    "manufacturing.delete_invoice": ("mfg_invoices", "delete"),
    "manufacturing.print_invoice": ("mfg_invoices", "view"),
    "manufacturing.add_invoice": ("mfg_invoices", "create"),

    # ---- manufacturing.py : customer orders / stock ----
    "manufacturing.orders_list": ("mfg_orders", "view"),
    "manufacturing.add_order": ("mfg_orders", "create"),
    "manufacturing.view_order": ("mfg_orders", "view"),
    "manufacturing.edit_order": ("mfg_orders", "edit"),
    "manufacturing.delete_order": ("mfg_orders", "delete"),
    "manufacturing.stock_view": ("mfg_stock", "view"),
    "manufacturing.stock_rods_view": ("mfg_stock", "view"),
    "manufacturing.stock_ledger": ("mfg_stock", "view"),

    # ---- manufacturing.py : purchasing ----
    "manufacturing.purchase_orders_list": ("mfg_purchase_orders", "view"),
    "manufacturing.add_purchase_order": ("mfg_purchase_orders", "create"),
    "manufacturing.view_purchase_order": ("mfg_purchase_orders", "view"),
    "manufacturing.edit_purchase_order": ("mfg_purchase_orders", "edit"),
    "manufacturing.delete_purchase_order": ("mfg_purchase_orders", "delete"),
    "manufacturing.print_purchase_order": ("mfg_purchase_orders", "view"),
    "manufacturing.purchase_grn_list": ("mfg_purchase_grn", "view"),
    "manufacturing.add_purchase_grn": ("mfg_purchase_grn", "create"),
    "manufacturing.view_purchase_grn": ("mfg_purchase_grn", "view"),
    "manufacturing.edit_purchase_grn": ("mfg_purchase_grn", "edit"),
    "manufacturing.delete_purchase_grn": ("mfg_purchase_grn", "delete"),
    "manufacturing.print_purchase_grn": ("mfg_purchase_grn", "view"),
    "manufacturing.api_mfg_purchase_order": ("mfg_purchase_grn", "view"),
    "manufacturing.api_mfg_supplier_orders": ("mfg_purchase_grn", "view"),
    "manufacturing.purchase_invoices_list": ("mfg_purchase_invoices", "view"),
    "manufacturing.add_purchase_invoice": ("mfg_purchase_invoices", "create"),
    "manufacturing.view_purchase_invoice": ("mfg_purchase_invoices", "view"),
    "manufacturing.edit_purchase_invoice": ("mfg_purchase_invoices", "edit"),
    "manufacturing.delete_purchase_invoice": ("mfg_purchase_invoices", "delete"),
    "manufacturing.print_purchase_invoice": ("mfg_purchase_invoices", "view"),
    "manufacturing.api_mfg_purchase_grn": ("mfg_purchase_invoices", "view"),
    "manufacturing.api_mfg_supplier_grns": ("mfg_purchase_invoices", "view"),
    "manufacturing.purchase_returns_list": ("mfg_purchase_returns", "view"),
    "manufacturing.add_purchase_return": ("mfg_purchase_returns", "create"),
    "manufacturing.view_purchase_return": ("mfg_purchase_returns", "view"),
    "manufacturing.edit_purchase_return": ("mfg_purchase_returns", "edit"),
    "manufacturing.delete_purchase_return": ("mfg_purchase_returns", "delete"),
    "manufacturing.print_purchase_return": ("mfg_purchase_returns", "view"),

    # ---- manufacturing.py : reports ----
    "manufacturing.sales_report": ("mfg_reports", "view"),
    "manufacturing.margin_report": ("mfg_reports", "view"),
    "manufacturing.reports_home": ("mfg_reports", "view"),
    "manufacturing.purchase_report": ("mfg_reports", "view"),
    "manufacturing.stock_report": ("mfg_reports", "view"),

    # ---- resharpening.py ----
    "resharpening.batches_list": ("resharpen_batches", "view"),
    "resharpening.add_batch": ("resharpen_batches", "create"),
    "resharpening.api_tool_quota": ("resharpen_batches", "view"),
    "resharpening.batch_detail": ("resharpen_batches", "view"),
    "resharpening.edit_batch": ("resharpen_batches", "edit"),
    "resharpening.delete_batch": ("resharpen_batches", "delete"),
    "resharpening.create_vendor_invoice": ("mfg_purchase_invoices", "create"),
    "resharpening.send_operation": ("resharpen_batches", "edit"),
    "resharpening.receive_operation": ("resharpen_batches", "edit"),
    "resharpening.generate_challan": ("resharpen_challans", "create"),
    "resharpening.add_customer_challan": ("resharpen_challans", "create"),
    "resharpening.pick_batch_for_delivery": ("resharpen_challans", "view"),
    "resharpening.challans_list": ("resharpen_challans", "view"),
    "resharpening.view_challan": ("resharpen_challans", "view"),
    "resharpening.edit_challan": ("resharpen_challans", "edit"),
    "resharpening.delete_challan": ("resharpen_challans", "delete"),
    "resharpening.print_challan": ("resharpen_challans", "view"),
    "resharpening.pick_batch_for_invoice": ("resharpen_invoices", "view"),
    "resharpening.invoices_list": ("resharpen_invoices", "view"),
    "resharpening.view_invoice": ("resharpen_invoices", "view"),
    "resharpening.edit_invoice": ("resharpen_invoices", "edit"),
    "resharpening.delete_invoice": ("resharpen_invoices", "delete"),
    "resharpening.print_invoice": ("resharpen_invoices", "view"),
    "resharpening.add_invoice": ("resharpen_invoices", "create"),
    "resharpening.stock_view": ("resharpen_stock", "view"),

    # ---- manufacturing_accounting.py ----
    "manufacturing_accounting.sales_payments_list": ("mfg_sales_payments", "view"),
    "manufacturing_accounting.add_sales_payment": ("mfg_sales_payments", "create"),
    "manufacturing_accounting.edit_sales_payment": ("mfg_sales_payments", "edit"),
    "manufacturing_accounting.delete_sales_payment": ("mfg_sales_payments", "delete"),
    "manufacturing_accounting.api_customer_invoices": ("mfg_sales_payments", "view"),
    "manufacturing_accounting.purchase_payments_list": ("mfg_purchase_payments", "view"),
    "manufacturing_accounting.add_purchase_payment": ("mfg_purchase_payments", "create"),
    "manufacturing_accounting.edit_purchase_payment": ("mfg_purchase_payments", "edit"),
    "manufacturing_accounting.delete_purchase_payment": ("mfg_purchase_payments", "delete"),
    "manufacturing_accounting.api_supplier_invoices": ("mfg_purchase_payments", "view"),
    "manufacturing_accounting.outstanding_report": ("mfg_outstanding", "view"),
    "manufacturing_accounting.party_statement": ("mfg_outstanding", "view"),
    "manufacturing_accounting.outstanding_party_detail": ("mfg_outstanding", "view"),
    "manufacturing_accounting.day_book": ("mfg_outstanding", "view"),
    "manufacturing_accounting.sales_day_book": ("mfg_outstanding", "view"),
    "manufacturing_accounting.purchase_day_book": ("mfg_outstanding", "view"),
}


# ============================================
# LOOKUP / ENFORCEMENT
# ============================================

def _load_permissions(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT section, can_view, can_create, can_edit, can_delete FROM permissions WHERE user_id=?",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return {
        row["section"]: {
            "view": bool(row["can_view"]),
            "create": bool(row["can_create"]),
            "edit": bool(row["can_edit"]),
            "delete": bool(row["can_delete"]),
        }
        for row in rows
    }


def get_user_permissions():
    """Per-request cached permission grid for the logged-in user. None means unrestricted (admin)."""
    if session.get("role") == "admin":
        return None
    if not hasattr(g, "_mfg_permissions"):
        g._mfg_permissions = _load_permissions(session.get("user_id"))
    return g._mfg_permissions


def has_permission(section, action="view"):
    if session.get("role") == "admin":
        return True
    perms = get_user_permissions()
    entry = perms.get(section) if perms else None
    if not entry:
        return False
    return entry.get(action, False)


def enforce_permissions():
    """Blueprint-level guard: attach with `blueprint.before_request(enforce_permissions)`,
    after `require_system(...)` so login/system checks run first."""
    endpoint = request.endpoint
    if not endpoint or endpoint in ALWAYS_ALLOWED_ENDPOINTS:
        return
    if session.get("role") == "admin":
        return
    mapping = ENDPOINT_MAP.get(endpoint)
    if not mapping:
        flash("That section isn't part of your access yet - ask your admin if you need it.", "info")
        return redirect(url_for("manufacturing.no_access"))
    section, action = mapping
    if not has_permission(section, action):
        flash("That section isn't part of your access yet - ask your admin if you need it.", "info")
        return redirect(url_for("manufacturing.no_access"))
