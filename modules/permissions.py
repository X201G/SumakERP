from functools import wraps
from flask import g, session, request, flash, redirect, url_for
from database import get_connection

# ============================================
# SECTION REGISTRY
# ============================================
# One row per feature. Keys are namespaced (mfg_ / resharpen_ / dist_).
# SECTIONS stays Manufacturing-only on purpose: manufacturing.py loops over it when
# saving a user's grid, so adding Distribution keys here would pollute that grid.
# Distribution has its own DIST_SECTIONS below.

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

# ---- Distribution ----

DIST_SECTIONS = [
    ("dist_dashboard", "Dashboard"),
    ("dist_products", "Products"),
    ("dist_categories", "Categories & Subcategories"),
    ("dist_units", "Units"),
    ("dist_hsn", "HSN Codes"),
    ("dist_customers", "Customers"),
    ("dist_suppliers", "Suppliers"),
    ("dist_transporters", "Transporters"),
    ("dist_sales_quotations", "Sales Quotations"),
    ("dist_sales_orders", "Sales Orders"),
    ("dist_proforma_invoices", "Proforma Invoices"),
    ("dist_sales_challans", "Sales Challans"),
    ("dist_sales_invoices", "Sales Invoices"),
    ("dist_sales_returns", "Sales Returns"),
    ("dist_purchase_orders", "Purchase Orders"),
    ("dist_purchase_grn", "Purchase GRN"),
    ("dist_purchase_invoices", "Purchase Invoices"),
    ("dist_purchase_returns", "Purchase Returns"),
    ("dist_stock", "Stock"),
    ("dist_sales_payments", "Sales Payments"),
    ("dist_purchase_payments", "Purchase Payments"),
    ("dist_outstanding", "Outstanding / Day Book"),
]

DIST_SECTION_LABELS = dict(DIST_SECTIONS)

DIST_SECTION_GROUPS = [
    ("General", ["dist_dashboard"]),
    ("Masters", ["dist_products", "dist_categories", "dist_units", "dist_hsn",
                 "dist_customers", "dist_suppliers", "dist_transporters"]),
    ("Sales", ["dist_sales_quotations", "dist_sales_orders", "dist_proforma_invoices",
               "dist_sales_challans", "dist_sales_invoices", "dist_sales_returns"]),
    ("Purchasing", ["dist_purchase_orders", "dist_purchase_grn",
                    "dist_purchase_invoices", "dist_purchase_returns"]),
    ("Stock", ["dist_stock"]),
    ("Payments & Accounting", ["dist_sales_payments", "dist_purchase_payments", "dist_outstanding"]),
]


def sections_for_system(system):
    return DIST_SECTIONS if system == "distribution" else SECTIONS


def section_groups_for_system(system):
    return DIST_SECTION_GROUPS if system == "distribution" else SECTION_GROUPS


def section_labels_for_system(system):
    return DIST_SECTION_LABELS if system == "distribution" else SECTION_LABELS


def no_access_endpoint(system):
    """Where a user with no grant for a page is sent, per portal."""
    return "manufacturing.no_access" if system == "manufacturing" else "no_access"


# Endpoints that any logged-in user may always reach, regardless of their permission
# grid - a safe landing page so a user with nothing granted yet (including no
# Dashboard access) never hits a redirect loop.
ALWAYS_ALLOWED_ENDPOINTS = {
    "manufacturing.no_access",
    "no_access",
}

# ============================================
# EXACT ROUTE -> (section, action) MAP
# ============================================

# Some read-only lookup endpoints feed forms in several sections. A value in ENDPOINT_MAP
# may therefore be a LIST of (section, action) options - any one of them is enough.
def _either(section):
    return [(section, "view"), (section, "create")]

_PRODUCT_PICKER = [
    (s, "create") for s in (
        "dist_sales_quotations", "dist_sales_orders", "dist_proforma_invoices",
        "dist_sales_challans", "dist_sales_invoices", "dist_sales_returns",
        "dist_purchase_orders", "dist_purchase_grn", "dist_purchase_invoices", "dist_purchase_returns",
    )
] + [("dist_products", "view"), ("dist_stock", "view")]

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

    # ============================================
    # DISTRIBUTION
    # (the dashboard route "/" lives in app.py and uses @permission_required)
    # ============================================

    # ---- masters.py ----
    "masters.products_list": ("dist_products", "view"),
    "masters.add_products": ("dist_products", "create"),
    "masters.delete_product": ("dist_products", "delete"),
    "masters.categories_list": ("dist_categories", "view"),
    "masters.add_category": ("dist_categories", "create"),
    "masters.edit_category": ("dist_categories", "edit"),
    "masters.delete_category": ("dist_categories", "delete"),
    "masters.subcategories_list": ("dist_categories", "view"),
    "masters.add_subcategory": ("dist_categories", "create"),
    "masters.edit_subcategory": ("dist_categories", "edit"),
    "masters.delete_subcategory": ("dist_categories", "delete"),
    "masters.units_list": ("dist_units", "view"),
    "masters.add_unit": ("dist_units", "create"),
    "masters.edit_unit": ("dist_units", "edit"),
    "masters.delete_unit": ("dist_units", "delete"),
    "masters.hsn_list": ("dist_hsn", "view"),
    "masters.add_hsn": ("dist_hsn", "create"),
    "masters.edit_hsn": ("dist_hsn", "edit"),
    "masters.delete_hsn": ("dist_hsn", "delete"),
    "masters.customers_list": ("dist_customers", "view"),
    "masters.add_customers": ("dist_customers", "create"),
    "masters.edit_customer": ("dist_customers", "edit"),
    "masters.delete_customer": ("dist_customers", "delete"),
    "masters.suppliers_list": ("dist_suppliers", "view"),
    "masters.add_suppliers": ("dist_suppliers", "create"),
    "masters.delete_supplier": ("dist_suppliers", "delete"),
    "masters.transporters_list": ("dist_transporters", "view"),
    "masters.add_transporter": ("dist_transporters", "create"),
    "masters.delete_transporter": ("dist_transporters", "delete"),

    # ---- purchases.py ----
    "purchases.purchase_orders_list": ("dist_purchase_orders", "view"),
    "purchases.add_purchase_order": ("dist_purchase_orders", "create"),
    "purchases.view_purchase_order": ("dist_purchase_orders", "view"),
    "purchases.delete_purchase_order": ("dist_purchase_orders", "delete"),
    "purchases.purchase_grn_list": ("dist_purchase_grn", "view"),
    "purchases.add_purchase_grn": ("dist_purchase_grn", "create"),
    "purchases.view_purchase_grn": ("dist_purchase_grn", "view"),
    "purchases.delete_purchase_grn": ("dist_purchase_grn", "delete"),
    "purchases.api_purchase_order": _either("dist_purchase_grn"),
    "purchases.api_supplier_orders": _either("dist_purchase_grn"),
    "purchases.purchase_invoices_list": ("dist_purchase_invoices", "view"),
    "purchases.add_purchase_invoice": ("dist_purchase_invoices", "create"),
    "purchases.view_purchase_invoice": ("dist_purchase_invoices", "view"),
    "purchases.delete_purchase_invoice": ("dist_purchase_invoices", "delete"),
    "purchases.api_purchase_grn": _either("dist_purchase_invoices"),
    "purchases.api_supplier_grns": _either("dist_purchase_invoices"),
    "purchases.purchase_returns_list": ("dist_purchase_returns", "view"),
    "purchases.add_purchase_return": ("dist_purchase_returns", "create"),
    "purchases.view_purchase_return": ("dist_purchase_returns", "view"),
    "purchases.delete_purchase_return": ("dist_purchase_returns", "delete"),

    # ---- sales.py ----
    "sales.sales_quotations_list": ("dist_sales_quotations", "view"),
    "sales.add_sales_quotation": ("dist_sales_quotations", "create"),
    "sales.view_sales_quotation": ("dist_sales_quotations", "view"),
    "sales.delete_sales_quotation": ("dist_sales_quotations", "delete"),
    "sales.sales_orders_list": ("dist_sales_orders", "view"),
    "sales.add_sales_order": ("dist_sales_orders", "create"),
    "sales.view_sales_order": ("dist_sales_orders", "view"),
    "sales.delete_sales_order": ("dist_sales_orders", "delete"),
    "sales.api_pending_items": [("dist_sales_invoices", "create")],  # only the Add Sales Invoice form calls this
    "sales.proforma_invoices_list": ("dist_proforma_invoices", "view"),
    "sales.add_proforma_invoice": ("dist_proforma_invoices", "create"),
    "sales.view_proforma_invoice": ("dist_proforma_invoices", "view"),
    "sales.delete_proforma_invoice": ("dist_proforma_invoices", "delete"),
    "sales.sales_challans_list": ("dist_sales_challans", "view"),
    "sales.add_sales_challan": ("dist_sales_challans", "create"),
    "sales.view_sales_challan": ("dist_sales_challans", "view"),
    "sales.delete_sales_challan": ("dist_sales_challans", "delete"),
    "sales.sales_invoices_list": ("dist_sales_invoices", "view"),
    "sales.add_sales_invoice": ("dist_sales_invoices", "create"),
    "sales.view_sales_invoice": ("dist_sales_invoices", "view"),
    "sales.delete_sales_invoice": ("dist_sales_invoices", "delete"),
    "sales.sales_returns_list": ("dist_sales_returns", "view"),
    "sales.add_sales_return": ("dist_sales_returns", "create"),
    "sales.view_sales_return": ("dist_sales_returns", "view"),
    "sales.delete_sales_return": ("dist_sales_returns", "delete"),

    # ---- stock.py ----
    "stock.stock_list": ("dist_stock", "view"),
    "stock.stock_ledger": ("dist_stock", "view"),
    "stock.api_products_with_stock": _PRODUCT_PICKER,  # product picker used by every sales/purchase form

    # ---- accounting.py ----
    "accounting.sales_payments_list": ("dist_sales_payments", "view"),
    "accounting.add_sales_payment": ("dist_sales_payments", "create"),
    "accounting.edit_sales_payment": ("dist_sales_payments", "edit"),
    "accounting.delete_sales_payment": ("dist_sales_payments", "delete"),
    "accounting.api_customer_invoices": _either("dist_sales_payments"),
    "accounting.purchase_payments_list": ("dist_purchase_payments", "view"),
    "accounting.add_purchase_payment": ("dist_purchase_payments", "create"),
    "accounting.edit_purchase_payment": ("dist_purchase_payments", "edit"),
    "accounting.delete_purchase_payment": ("dist_purchase_payments", "delete"),
    "accounting.api_supplier_invoices": _either("dist_purchase_payments"),
    "accounting.outstanding_report": ("dist_outstanding", "view"),
    "accounting.party_statement": ("dist_outstanding", "view"),
    "accounting.outstanding_party_detail": ("dist_outstanding", "view"),
    "accounting.day_book": ("dist_outstanding", "view"),
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


def _allowed(mapping):
    options = mapping if isinstance(mapping, list) else [mapping]
    return any(has_permission(section, action) for section, action in options)


def _deny():
    flash("That section isn't part of your access yet - ask your admin if you need it.", "info")
    return redirect(url_for(no_access_endpoint(session.get("system"))))


def enforce_permissions():
    """Blueprint-level guard: attach with `blueprint.before_request(enforce_permissions)`,
    after `require_system(...)` so login/system checks run first."""
    endpoint = request.endpoint
    if not endpoint or endpoint in ALWAYS_ALLOWED_ENDPOINTS:
        return
    if session.get("role") == "admin":
        return
    mapping = ENDPOINT_MAP.get(endpoint)
    if not mapping or not _allowed(mapping):
        return _deny()


def permission_required(section, action="view"):
    """Route decorator for routes that live outside a guarded blueprint (e.g. "/" in app.py).
    Put it BELOW @system_required so login/system checks run first."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not has_permission(section, action):
                return _deny()
            return view(*args, **kwargs)
        return wrapped
    return decorator
