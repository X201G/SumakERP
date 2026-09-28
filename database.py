# ============================================
# SUMAK ERP - DATABASE CONFIGURATION
# ============================================

import sqlite3
from werkzeug.security import generate_password_hash
DATABASE = "sumak_erp.db"
def get_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# ============================================
# CREATE ALL TABLES
# ============================================

def create_tables():
    conn = get_connection()
    cursor = conn.cursor()

    ## User master (login + roles) - "system" says which portal this login belongs to: distribution or manufacturing
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        full_name TEXT,
        role TEXT NOT NULL DEFAULT 'staff',
        system TEXT NOT NULL DEFAULT 'distribution',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    ## Create default admin logins the very first time the app runs (one per system)
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name, role, system) VALUES (?,?,?,?,?)",
            ("admin", generate_password_hash("admin123"), "Administrator", "admin", "distribution")
        )
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name, role, system) VALUES (?,?,?,?,?)",
            ("mfgadmin", generate_password_hash("mfg123"), "Manufacturing Administrator", "admin", "manufacturing")
        )
        print("✅ Default admins created -> distribution: admin/admin123, manufacturing: mfgadmin/mfg123 (change these later!)")

    ## Unit master
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS units (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        unit_name TEXT NOT NULL UNIQUE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    ## Category Master
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_name TEXT NOT NULL UNIQUE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    ## Subcategory Master
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subcategories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER,
        subcategory_name TEXT NOT NULL UNIQUE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (category_id) REFERENCES categories(id)
        )
    ''')

    ## HSN Master
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hsn (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hsn_code TEXT NOT NULL UNIQUE,
        description TEXT,
        gst_rate REAL DEFAULT 18.0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    ## Customer Master
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        address TEXT,
        city TEXT,
        state TEXT,
        gst_number TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    ## Supplier Master
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        address TEXT,
        city TEXT,
        state TEXT,
        gst_number TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    ## Transporter master
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transporters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        address TEXT,
        city TEXT,
        state TEXT,
        gst_number TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    ## Product Master
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_name TEXT NOT NULL,
        print_description TEXT,
        category_id INTEGER,
        subcategory_id INTEGER,
        unit_id INTEGER,
        hsn_id INTEGER,
        tax_rate REAL DEFAULT 18.0,
        supplier_id INTEGER,
        sale_price REAL DEFAULT 0,
        sale_discount REAL DEFAULT 0,
        purchase_price REAL DEFAULT 0,
        purchase_discount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (category_id) REFERENCES categories(id),
        FOREIGN KEY (subcategory_id) REFERENCES subcategories(id),
        FOREIGN KEY (unit_id)     REFERENCES units(id),
        FOREIGN KEY (hsn_id)      REFERENCES hsn(id),
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
        )
    ''')

    ## Purchase order header
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS purchase_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT NOT NULL UNIQUE,
        order_date DATE NOT NULL,
        supplier_id INTEGER,
        transporter_id INTEGER,
        address TEXT,
        kind_attn TEXT,
        sup_ref_no TEXT,
        cr_days INTEGER DEFAULT 0,
        valid_till DATE,
        remarks TEXT,
        status TEXT DEFAULT 'pending',
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
        FOREIGN KEY (transporter_id) REFERENCES transporters(id)
        )
    ''')

    ## Purchase order items
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS purchase_order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        print_description TEXT,
        quantity REAL DEFAULT 0,
        unit_id INTEGER,
        rate REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        cash_discount REAL DEFAULT 0,
        hsn_id INTEGER,
        bef_tax_amt REAL DEFAULT 0,
        tax_rate REAL DEFAULT 18.0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        delivery_date DATE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (order_id) REFERENCES purchase_orders(id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (unit_id) REFERENCES units(id),
        FOREIGN KEY (hsn_id) REFERENCES hsn(id)
        )
    ''')

    ## Purchase Invoice Header
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS purchase_invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT NOT NULL UNIQUE,
        invoice_date DATE NOT NULL,
        supplier_id INTEGER NOT NULL,
        transporter_id INTEGER,
        order_id INTEGER,
        sup_invoice_no TEXT,
        sup_invoice_date DATE,
        cr_days INTEGER DEFAULT 0,
        due_date DATE,
        remarks TEXT,
        status TEXT DEFAULT 'unpaid',
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
        FOREIGN KEY (transporter_id) REFERENCES transporters(id),
        FOREIGN KEY (order_id) REFERENCES purchase_orders(id)
        )
    ''')

    ## Purchase Invoice Items
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS purchase_invoice_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        print_description TEXT,
        quantity REAL DEFAULT 0,
        unit_id INTEGER,
        rate REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        cash_discount REAL DEFAULT 0,
        hsn_id INTEGER,
        bef_tax_amt REAL DEFAULT 0,
        tax_rate REAL DEFAULT 18.0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (invoice_id) REFERENCES purchase_invoices(id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (unit_id) REFERENCES units(id),
        FOREIGN KEY (hsn_id) REFERENCES hsn(id)
        )
    ''')

    ## Purchase GRN Header
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS purchase_grn (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grn_no TEXT NOT NULL UNIQUE,
        grn_date DATE NOT NULL,
        supplier_id INTEGER NOT NULL,
        transporter_id INTEGER,
        order_id INTEGER,
        invoice_id INTEGER,
        remarks TEXT,
        status TEXT DEFAULT 'pending',
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
        FOREIGN KEY (transporter_id) REFERENCES transporters(id),
        FOREIGN KEY (order_id) REFERENCES purchase_orders(id),
        FOREIGN KEY (invoice_id) REFERENCES purchase_invoices(id)
        )
    ''')

    ## Purchase GRN Items
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS purchase_grn_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grn_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        print_description TEXT,
        quantity REAL DEFAULT 0,
        unit_id INTEGER,
        rate REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        hsn_id INTEGER,
        bef_tax_amt REAL DEFAULT 0,
        tax_rate REAL DEFAULT 18.0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (grn_id) REFERENCES purchase_grn(id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (unit_id) REFERENCES units(id),
        FOREIGN KEY (hsn_id) REFERENCES hsn(id)
        )
    ''')

    ## Purchase Return Header
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS purchase_returns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        return_no TEXT NOT NULL UNIQUE,
        return_date DATE NOT NULL,
        supplier_id INTEGER NOT NULL,
        transporter_id INTEGER,
        invoice_id INTEGER,
        reason TEXT,
        remarks TEXT,
        status TEXT DEFAULT 'pending',
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
        FOREIGN KEY (transporter_id) REFERENCES transporters(id),
        FOREIGN KEY (invoice_id) REFERENCES purchase_invoices(id)
        )
    ''')

    ## Purchase Return Items
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS purchase_return_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        return_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        print_description TEXT,
        quantity REAL DEFAULT 0,
        unit_id INTEGER,
        rate REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        hsn_id INTEGER,
        bef_tax_amt REAL DEFAULT 0,
        tax_rate REAL DEFAULT 18.0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        reason TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (return_id) REFERENCES purchase_returns(id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (unit_id) REFERENCES units(id),
        FOREIGN KEY (hsn_id) REFERENCES hsn(id)
        )
    ''')

    

    ## Sales Invoice Header
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT NOT NULL UNIQUE,
        invoice_date DATE NOT NULL,
        customer_id INTEGER NOT NULL,
        transporter_id INTEGER,
        po_no TEXT,
        po_date DATE,
        cr_days INTEGER DEFAULT 0,
        due_date DATE,
        remarks TEXT,
        status TEXT DEFAULT 'unpaid',
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id),
        FOREIGN KEY (transporter_id) REFERENCES transporters(id)
        )
    ''')

    ## Sales Invoice Items
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_invoice_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        unit_id INTEGER,
        hsn_id INTEGER,
        print_description TEXT,
        quantity REAL DEFAULT 0,
        rate REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        tax_rate REAL DEFAULT 18.0,
        bef_tax_amt REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (invoice_id) REFERENCES sales_invoices(id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (unit_id) REFERENCES units(id),
        FOREIGN KEY (hsn_id) REFERENCES hsn(id)
        )
    ''')

    ## Sales Quotation Header
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_quotations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quotation_no TEXT NOT NULL UNIQUE,
        quotation_date DATE NOT NULL,
        customer_id INTEGER NOT NULL,
        valid_till DATE,
        kind_attn TEXT,
        remarks TEXT,
        status TEXT DEFAULT 'open',
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    ''')

    ## Sales Quotation Items
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_quotation_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quotation_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        unit_id INTEGER,
        hsn_id INTEGER,
        print_description TEXT,
        quantity REAL DEFAULT 0,
        rate REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        tax_rate REAL DEFAULT 18.0,
        bef_tax_amt REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (quotation_id) REFERENCES sales_quotations(id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (unit_id) REFERENCES units(id),
        FOREIGN KEY (hsn_id) REFERENCES hsn(id)
        )
    ''')

    ## Sales Order Header
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT NOT NULL UNIQUE,
        order_date DATE NOT NULL,
        customer_id INTEGER NOT NULL,
        po_no TEXT,
        po_date DATE,
        kind_attn TEXT,
        remarks TEXT,
        status TEXT DEFAULT 'open',
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    ''')

    ## Sales Order Items
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        unit_id INTEGER,
        hsn_id INTEGER,
        print_description TEXT,
        quantity REAL DEFAULT 0,
        rate REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        tax_rate REAL DEFAULT 18.0,
        bef_tax_amt REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (order_id) REFERENCES sales_orders(id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (unit_id) REFERENCES units(id),
        FOREIGN KEY (hsn_id) REFERENCES hsn(id)
        )
    ''')

    ## Proforma Invoice Header
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS proforma_invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proforma_no TEXT NOT NULL UNIQUE,
        proforma_date DATE NOT NULL,
        customer_id INTEGER NOT NULL,
        transporter_id INTEGER,
        po_no TEXT,
        po_date DATE,
        kind_attn TEXT,
        remarks TEXT,
        status TEXT DEFAULT 'open',
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id),
        FOREIGN KEY (transporter_id) REFERENCES transporters(id)
        )
    ''')

    ## Proforma Invoice Items
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS proforma_invoice_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proforma_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        unit_id INTEGER,
        hsn_id INTEGER,
        print_description TEXT,
        quantity REAL DEFAULT 0,
        rate REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        tax_rate REAL DEFAULT 18.0,
        bef_tax_amt REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (proforma_id) REFERENCES proforma_invoices(id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (unit_id) REFERENCES units(id),
        FOREIGN KEY (hsn_id) REFERENCES hsn(id)
        )
    ''')

    ## Sales Challan Header
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_challans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        challan_no TEXT NOT NULL UNIQUE,
        challan_date DATE NOT NULL,
        customer_id INTEGER NOT NULL,
        transporter_id INTEGER,
        po_no TEXT,
        po_date DATE,
        kind_attn TEXT,
        remarks TEXT,
        status TEXT DEFAULT 'open',
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id),
        FOREIGN KEY (transporter_id) REFERENCES transporters(id)
        )
    ''')

    ## Sales Challan Items
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_challan_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        challan_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        unit_id INTEGER,
        hsn_id INTEGER,
        print_description TEXT,
        quantity REAL DEFAULT 0,
        rate REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        tax_rate REAL DEFAULT 18.0,
        bef_tax_amt REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (challan_id) REFERENCES sales_challans(id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (unit_id) REFERENCES units(id),
        FOREIGN KEY (hsn_id) REFERENCES hsn(id)
        )
    ''')

    ## Sales Return Header
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_returns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        return_no TEXT NOT NULL UNIQUE,
        return_date DATE NOT NULL,
        customer_id INTEGER NOT NULL,
        transporter_id INTEGER,
        invoice_id INTEGER,
        reason TEXT,
        remarks TEXT,
        status TEXT DEFAULT 'pending',
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id),
        FOREIGN KEY (transporter_id) REFERENCES transporters(id),
        FOREIGN KEY (invoice_id) REFERENCES sales_invoices(id)
        )
    ''')

    ## Sales Return Items
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_return_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        return_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        unit_id INTEGER,
        hsn_id INTEGER,
        print_description TEXT,
        quantity REAL DEFAULT 0,
        rate REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        tax_rate REAL DEFAULT 18.0,
        bef_tax_amt REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        reason TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (return_id) REFERENCES sales_returns(id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (unit_id) REFERENCES units(id),
        FOREIGN KEY (hsn_id) REFERENCES hsn(id)
        )
    ''')

        ## Sales Payments (money received from customers - may or may not be tied to one specific invoice)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER,
        customer_id INTEGER NOT NULL,
        payment_date DATE NOT NULL,
        amount REAL NOT NULL,
        payment_mode TEXT DEFAULT 'cash',
        reference_no TEXT,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (invoice_id) REFERENCES sales_invoices(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    ''')

    ## Purchase Payments (money paid to suppliers - may or may not be tied to one specific invoice)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS purchase_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER,
        supplier_id INTEGER NOT NULL,
        payment_date DATE NOT NULL,
        amount REAL NOT NULL,
        payment_mode TEXT DEFAULT 'cash',
        reference_no TEXT,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (invoice_id) REFERENCES purchase_invoices(id),
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
        )
    ''')

    ## MANUFACTURING - Raw Material (carbide rod) inward
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_rods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rod_date DATE NOT NULL,
        grade TEXT,
        diameter_mm REAL NOT NULL,
        length_mm REAL NOT NULL DEFAULT 330,
        weight_kg REAL,
        remaining_length_mm REAL,
        quantity INTEGER NOT NULL DEFAULT 1,
        supplier_id INTEGER,
        rate REAL DEFAULT 0,
        purchase_price REAL,
        purchase_discount_percent REAL DEFAULT 0,
        sales_price REAL,
        sales_discount_percent REAL DEFAULT 0,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
        )
    ''')

    ## MANUFACTURING - Brand Master (scoped per tool type - e.g. "Typhon" under Drill is separate from "Typhon" under Endmill)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_brands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tool_type TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_mfg_brands_type_name ON mfg_brands(tool_type, name)')

    ## MANUFACTURING - Item Category Master (own list, since Distribution's categories are tool-product-specific)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_item_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    ## MANUFACTURING - Item Master (grinding wheels, oils, other purchasable consumables - not rods, not tool designs)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_name TEXT NOT NULL,
        print_description TEXT,
        category_id INTEGER,
        unit_id INTEGER,
        hsn_code TEXT,
        tax_rate REAL DEFAULT 18.0,
        purchase_price REAL DEFAULT 0,
        purchase_discount_percent REAL DEFAULT 0,
        sales_price REAL,
        sales_discount_percent REAL DEFAULT 0,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (category_id) REFERENCES mfg_item_categories(id),
        FOREIGN KEY (unit_id) REFERENCES units(id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_items_category ON mfg_items(category_id)')

    ## MANUFACTURING - Purchase Order (covers rods, generic items, and bought-in finished tools -
    ## each line's item_type says which; rod lines carry their own grade/diameter/length since
    ## rods have no reusable spec master, unlike items (mfg_items) and tools (mfg_tools))
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_purchase_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT NOT NULL UNIQUE,
        order_date DATE NOT NULL,
        supplier_id INTEGER,
        transporter_id INTEGER,
        remarks TEXT,
        status TEXT DEFAULT 'pending',
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
        FOREIGN KEY (transporter_id) REFERENCES transporters(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_purchase_order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        item_type TEXT NOT NULL,
        item_id INTEGER,
        description TEXT NOT NULL,
        rod_grade TEXT,
        rod_diameter_mm REAL,
        rod_length_mm REAL,
        quantity REAL NOT NULL,
        unit_id INTEGER,
        rate REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        hsn_code TEXT,
        tax_rate REAL DEFAULT 18.0,
        bef_tax_amt REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        received_qty REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (order_id) REFERENCES mfg_purchase_orders(id),
        FOREIGN KEY (unit_id) REFERENCES units(id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_po_items_order ON mfg_purchase_order_items(order_id)')

    ## MANUFACTURING - Purchase GRN (goods receipt - the moment stock actually gets created:
    ## rod lines create a new mfg_rods row, item lines stock mfg_items in, tool lines (bought-in
    ## finished tools) post straight to Finished Tools stock, all via mfg_stock_movements)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_purchase_grn (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grn_no TEXT NOT NULL UNIQUE,
        grn_date DATE NOT NULL,
        supplier_id INTEGER,
        order_id INTEGER,
        transporter_id INTEGER,
        remarks TEXT,
        status TEXT DEFAULT 'pending',
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
        FOREIGN KEY (order_id) REFERENCES mfg_purchase_orders(id),
        FOREIGN KEY (transporter_id) REFERENCES transporters(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_purchase_grn_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grn_id INTEGER NOT NULL,
        item_type TEXT NOT NULL,
        item_id INTEGER,
        description TEXT NOT NULL,
        rod_grade TEXT,
        rod_diameter_mm REAL,
        rod_length_mm REAL,
        quantity REAL NOT NULL,
        unit_id INTEGER,
        rate REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        hsn_code TEXT,
        tax_rate REAL DEFAULT 18.0,
        bef_tax_amt REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (grn_id) REFERENCES mfg_purchase_grn(id),
        FOREIGN KEY (unit_id) REFERENCES units(id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_grn_items_grn ON mfg_purchase_grn_items(grn_id)')

    ## MANUFACTURING - Purchase Invoice (the supplier's bill - purely financial, no stock impact)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_purchase_invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT NOT NULL UNIQUE,
        invoice_date DATE NOT NULL,
        supplier_id INTEGER,
        order_id INTEGER,
        grn_id INTEGER,
        transporter_id INTEGER,
        sup_invoice_no TEXT,
        sup_invoice_date DATE,
        cr_days INTEGER DEFAULT 0,
        due_date DATE,
        remarks TEXT,
        status TEXT DEFAULT 'unpaid',
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
        FOREIGN KEY (order_id) REFERENCES mfg_purchase_orders(id),
        FOREIGN KEY (grn_id) REFERENCES mfg_purchase_grn(id),
        FOREIGN KEY (transporter_id) REFERENCES transporters(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_purchase_invoice_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER NOT NULL,
        item_type TEXT NOT NULL,
        item_id INTEGER,
        description TEXT NOT NULL,
        rod_grade TEXT,
        rod_diameter_mm REAL,
        rod_length_mm REAL,
        quantity REAL NOT NULL,
        unit_id INTEGER,
        rate REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        hsn_code TEXT,
        tax_rate REAL DEFAULT 18.0,
        bef_tax_amt REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (invoice_id) REFERENCES mfg_purchase_invoices(id),
        FOREIGN KEY (unit_id) REFERENCES units(id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_pinv_items_invoice ON mfg_purchase_invoice_items(invoice_id)')

    ## MANUFACTURING - Purchase Return (sending goods back to the supplier - stock OUT)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_purchase_returns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        return_no TEXT NOT NULL UNIQUE,
        return_date DATE NOT NULL,
        supplier_id INTEGER,
        invoice_id INTEGER,
        transporter_id INTEGER,
        reason TEXT,
        remarks TEXT,
        status TEXT DEFAULT 'pending',
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
        FOREIGN KEY (invoice_id) REFERENCES mfg_purchase_invoices(id),
        FOREIGN KEY (transporter_id) REFERENCES transporters(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_purchase_return_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        return_id INTEGER NOT NULL,
        item_type TEXT NOT NULL,
        item_id INTEGER,
        description TEXT NOT NULL,
        rod_grade TEXT,
        rod_diameter_mm REAL,
        rod_length_mm REAL,
        quantity REAL NOT NULL,
        unit_id INTEGER,
        rate REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        hsn_code TEXT,
        tax_rate REAL DEFAULT 18.0,
        bef_tax_amt REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (return_id) REFERENCES mfg_purchase_returns(id),
        FOREIGN KEY (unit_id) REFERENCES units(id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_pret_items_return ON mfg_purchase_return_items(return_id)')

    ## MANUFACTURING - Tool Master (drills, endmills, reamers, holemills, taps, special tools)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_tools (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_code TEXT,
        brand_id INTEGER,
        tool_name TEXT NOT NULL,
        tool_type TEXT NOT NULL,
        diameter_mm REAL,
        overall_length_mm REAL,
        flute_length_mm REAL,
        shank_diameter_mm REAL,
        customer_id INTEGER,
        purchase_price REAL,
        purchase_discount_percent REAL DEFAULT 0,
        sales_price REAL,
        sales_discount_percent REAL DEFAULT 0,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id),
        FOREIGN KEY (brand_id) REFERENCES mfg_brands(id)
        )
    ''')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_mfg_tools_item_code ON mfg_tools(item_code)')

    ## MANUFACTURING - Job Work Batch (a run of N pieces of one tool, cut from a rod)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_job_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_no TEXT NOT NULL UNIQUE,
        batch_date DATE NOT NULL,
        tool_id INTEGER NOT NULL,
        rod_id INTEGER,
        customer_id INTEGER,
        quantity INTEGER NOT NULL,
        length_needed_mm REAL,
        rod_override_reason TEXT,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tool_id) REFERENCES mfg_tools(id),
        FOREIGN KEY (rod_id) REFERENCES mfg_rods(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    ''')

    ## MANUFACTURING - Job Work Stage tracking (one row per shipment - can cover any combination of stages)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_job_stages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id INTEGER NOT NULL,
        stages TEXT NOT NULL,
        vendor_id INTEGER,
        dispatch_date DATE,
        quantity_sent INTEGER,
        receive_date DATE,
        quantity_received INTEGER,
        rod_returned_length_mm REAL,
        rate REAL,
        remarks TEXT,
        purchase_invoice_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (batch_id) REFERENCES mfg_job_batches(id),
        FOREIGN KEY (vendor_id) REFERENCES suppliers(id),
        FOREIGN KEY (purchase_invoice_id) REFERENCES mfg_purchase_invoices(id)
        )
    ''')

    ## MANUFACTURING - Challans (vendor job-work dispatch + customer delivery)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_challans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        challan_no TEXT NOT NULL UNIQUE,
        challan_date DATE NOT NULL,
        challan_type TEXT NOT NULL,
        party_type TEXT NOT NULL,
        party_id INTEGER NOT NULL,
        transporter_id INTEGER,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (transporter_id) REFERENCES transporters(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_challan_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        challan_id INTEGER NOT NULL,
        batch_id INTEGER NOT NULL,
        job_stage_id INTEGER,
        quantity INTEGER NOT NULL,
        FOREIGN KEY (challan_id) REFERENCES mfg_challans(id),
        FOREIGN KEY (batch_id) REFERENCES mfg_job_batches(id),
        FOREIGN KEY (job_stage_id) REFERENCES mfg_job_stages(id)
        )
    ''')

    ## MANUFACTURING - Invoices (billing the customer for finished tools)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT NOT NULL UNIQUE,
        invoice_date DATE NOT NULL,
        customer_id INTEGER NOT NULL,
        cr_days INTEGER DEFAULT 0,
        due_date DATE,
        remarks TEXT,
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_invoice_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER NOT NULL,
        batch_id INTEGER NOT NULL,
        order_item_id INTEGER,
        quantity REAL NOT NULL,
        rate REAL NOT NULL,
        tax_rate REAL DEFAULT 18.0,
        bef_tax_amt REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        FOREIGN KEY (invoice_id) REFERENCES mfg_invoices(id),
        FOREIGN KEY (batch_id) REFERENCES mfg_job_batches(id),
        FOREIGN KEY (order_item_id) REFERENCES mfg_customer_order_items(id)
        )
    ''')

    ## MANUFACTURING - Stock movement history (rods in mm, tools in pcs)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_stock_movements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_type TEXT NOT NULL,
        item_id INTEGER NOT NULL,
        movement_type TEXT NOT NULL,
        quantity REAL NOT NULL,
        source_type TEXT,
        source_id INTEGER,
        source_no TEXT,
        movement_date DATE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    ## MANUFACTURING - Customer Orders (what a customer has asked for, tracked independently of batches)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_customer_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT NOT NULL UNIQUE,
        order_date DATE NOT NULL,
        customer_id INTEGER NOT NULL,
        po_no TEXT,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_customer_order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        tool_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        rate REAL,
        billed_qty INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (order_id) REFERENCES mfg_customer_orders(id),
        FOREIGN KEY (tool_id) REFERENCES mfg_tools(id)
        )
    ''')

    ## MANUFACTURING - indexes (added for performance as data volume grows; purely additive, no data changes)
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_customer_orders_customer ON mfg_customer_orders(customer_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_customer_order_items_order ON mfg_customer_order_items(order_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_customer_order_items_tool ON mfg_customer_order_items(tool_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_rods_supplier ON mfg_rods(supplier_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_tools_customer ON mfg_tools(customer_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_tools_brand ON mfg_tools(brand_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_job_batches_tool ON mfg_job_batches(tool_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_job_batches_rod ON mfg_job_batches(rod_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_job_batches_customer ON mfg_job_batches(customer_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_job_stages_batch ON mfg_job_stages(batch_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_job_stages_vendor ON mfg_job_stages(vendor_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_challans_party ON mfg_challans(party_type, party_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_challan_items_challan ON mfg_challan_items(challan_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_challan_items_batch ON mfg_challan_items(batch_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_challan_items_stage ON mfg_challan_items(job_stage_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_invoices_customer ON mfg_invoices(customer_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_invoice_items_invoice ON mfg_invoice_items(invoice_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_invoice_items_batch ON mfg_invoice_items(batch_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_stock_movements_item ON mfg_stock_movements(item_type, item_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_stock_movements_source ON mfg_stock_movements(source_type, source_id)')

    ## MANUFACTURING - Sales Payments (money received from customers against mfg_invoices,
    ## invoice_id nullable so a payment can sit "on account" - same shape as Distribution's sales_payments)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_sales_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER,
        customer_id INTEGER NOT NULL,
        payment_date DATE NOT NULL,
        amount REAL NOT NULL,
        payment_mode TEXT DEFAULT 'cash',
        reference_no TEXT,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (invoice_id) REFERENCES mfg_invoices(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    ''')

    ## MANUFACTURING - Purchase Payments (money paid to suppliers against mfg_purchase_invoices)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_purchase_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER,
        supplier_id INTEGER NOT NULL,
        payment_date DATE NOT NULL,
        amount REAL NOT NULL,
        payment_mode TEXT DEFAULT 'cash',
        reference_no TEXT,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (invoice_id) REFERENCES mfg_purchase_invoices(id),
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_sales_payments_customer ON mfg_sales_payments(customer_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_sales_payments_invoice ON mfg_sales_payments(invoice_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_purchase_payments_supplier ON mfg_purchase_payments(supplier_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_purchase_payments_invoice ON mfg_purchase_payments(invoice_id)')

    ## RESHARPENING - kept as a fully separate set of tables from mfg_job_batches/mfg_challans/
    ## mfg_invoices/mfg_stock_movements on purpose (silent separation, by request) - only the
    ## Customer/Tool/Supplier masters are shared with the rest of Manufacturing. A resharpen
    ## batch starts from tools the CUSTOMER sends in (mirror image of a job batch, which starts
    ## from SUMAK's own rod stock).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_resharpen_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_no TEXT NOT NULL UNIQUE,
        batch_date DATE NOT NULL,
        tool_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tool_id) REFERENCES mfg_tools(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    ''')

    ## RESHARPENING - stage tracking (Resharpening, Recoating), same shape as mfg_job_stages -
    ## vendor_id nullable since a leg can be done in-house
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_resharpen_stages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id INTEGER NOT NULL,
        stages TEXT NOT NULL,
        vendor_id INTEGER,
        dispatch_date DATE,
        quantity_sent INTEGER,
        receive_date DATE,
        quantity_received INTEGER,
        rate REAL,
        remarks TEXT,
        purchase_invoice_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (batch_id) REFERENCES mfg_resharpen_batches(id),
        FOREIGN KEY (vendor_id) REFERENCES suppliers(id),
        FOREIGN KEY (purchase_invoice_id) REFERENCES mfg_purchase_invoices(id)
        )
    ''')

    ## RESHARPENING - challans (customer inward receipt, vendor job-work leg, customer delivery)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_resharpen_challans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        challan_no TEXT NOT NULL UNIQUE,
        challan_date DATE NOT NULL,
        challan_type TEXT NOT NULL,
        party_type TEXT NOT NULL,
        party_id INTEGER NOT NULL,
        transporter_id INTEGER,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (transporter_id) REFERENCES transporters(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_resharpen_challan_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        challan_id INTEGER NOT NULL,
        batch_id INTEGER NOT NULL,
        stage_id INTEGER,
        quantity INTEGER NOT NULL,
        FOREIGN KEY (challan_id) REFERENCES mfg_resharpen_challans(id),
        FOREIGN KEY (batch_id) REFERENCES mfg_resharpen_batches(id),
        FOREIGN KEY (stage_id) REFERENCES mfg_resharpen_stages(id)
        )
    ''')

    ## RESHARPENING - invoices (billing the customer for the resharpening service)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_resharpen_invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT NOT NULL UNIQUE,
        invoice_date DATE NOT NULL,
        customer_id INTEGER NOT NULL,
        cr_days INTEGER DEFAULT 0,
        due_date DATE,
        remarks TEXT,
        total_amount REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_resharpen_invoice_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER NOT NULL,
        batch_id INTEGER NOT NULL,
        quantity REAL NOT NULL,
        rate REAL NOT NULL,
        tax_rate REAL DEFAULT 18.0,
        bef_tax_amt REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        amount REAL DEFAULT 0,
        FOREIGN KEY (invoice_id) REFERENCES mfg_resharpen_invoices(id),
        FOREIGN KEY (batch_id) REFERENCES mfg_resharpen_batches(id)
        )
    ''')

    ## RESHARPENING - payments (its own table, not mfg_sales_payments, since invoice_id would
    ## otherwise be ambiguous between mfg_invoices and mfg_resharpen_invoices - kept fully
    ## separate from manufacturing_accounting.py's Outstanding/Day Book/Statement by request)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mfg_resharpen_sales_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER,
        customer_id INTEGER NOT NULL,
        payment_date DATE NOT NULL,
        amount REAL NOT NULL,
        payment_mode TEXT DEFAULT 'cash',
        reference_no TEXT,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (invoice_id) REFERENCES mfg_resharpen_invoices(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    ''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_resharpen_batches_tool ON mfg_resharpen_batches(tool_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_resharpen_batches_customer ON mfg_resharpen_batches(customer_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_resharpen_stages_batch ON mfg_resharpen_stages(batch_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_resharpen_stages_vendor ON mfg_resharpen_stages(vendor_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_resharpen_challans_party ON mfg_resharpen_challans(party_type, party_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_resharpen_challan_items_challan ON mfg_resharpen_challan_items(challan_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_resharpen_challan_items_batch ON mfg_resharpen_challan_items(batch_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_resharpen_invoices_customer ON mfg_resharpen_invoices(customer_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_resharpen_invoice_items_invoice ON mfg_resharpen_invoice_items(invoice_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_resharpen_invoice_items_batch ON mfg_resharpen_invoice_items(batch_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_resharpen_payments_customer ON mfg_resharpen_sales_payments(customer_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mfg_resharpen_payments_invoice ON mfg_resharpen_sales_payments(invoice_id)')


    ## Permissions (per-user, per-section, view/create/edit/delete grid)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        section TEXT NOT NULL,
        can_view INTEGER NOT NULL DEFAULT 0,
        can_create INTEGER NOT NULL DEFAULT 0,
        can_edit INTEGER NOT NULL DEFAULT 0,
        can_delete INTEGER NOT NULL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        UNIQUE(user_id, section)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_permissions_user ON permissions(user_id)')

    ## Projects portal
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        client TEXT,
        stage TEXT NOT NULL DEFAULT 'backlog',
        priority TEXT NOT NULL DEFAULT 'normal',
        owner_id INTEGER,
        start_date DATE,
        due_date DATE,
        created_by INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (owner_id) REFERENCES users(id),
        FOREIGN KEY (created_by) REFERENCES users(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS project_user_shares (
        project_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        shared_by INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (project_id, user_id),
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (shared_by) REFERENCES users(id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_project_user_shares_user ON project_user_shares(user_id)')
    cursor.execute(
        "UPDATE users SET role = 'user' WHERE system = 'projects' AND role NOT IN ('admin', 'user', 'external')"
    )
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS project_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        details TEXT,
        assigned_to INTEGER,
        due_date DATE,
        stage TEXT NOT NULL DEFAULT 'awaiting_response',
        is_complete INTEGER NOT NULL DEFAULT 0,
        created_by INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY (assigned_to) REFERENCES users(id),
        FOREIGN KEY (created_by) REFERENCES users(id)
        )
    ''')
    cursor.execute("PRAGMA table_info(project_tasks)")
    project_task_columns = {row["name"] for row in cursor.fetchall()}
    if "stage" not in project_task_columns:
        cursor.execute("ALTER TABLE project_tasks ADD COLUMN stage TEXT NOT NULL DEFAULT 'awaiting_response'")
    if "updated_at" not in project_task_columns:
        cursor.execute("ALTER TABLE project_tasks ADD COLUMN updated_at DATETIME")
    cursor.execute("""
        UPDATE project_tasks
        SET stage = CASE
            WHEN is_complete = 1 OR stage = 'completed' THEN 'completed'
            WHEN stage = 'backlog' THEN 'awaiting_response'
            WHEN stage = 'planning' THEN 'awaiting_quotation'
            WHEN stage = 'in_progress' THEN 'awaiting_visit'
            WHEN stage = 'on_hold' THEN 'negotiating'
            ELSE stage
        END,
        is_complete = CASE WHEN is_complete = 1 OR stage = 'completed' THEN 1 ELSE 0 END
        WHERE stage IN ('backlog', 'planning', 'in_progress', 'on_hold', 'completed')
           OR is_complete = 1
    """)
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_projects_stage ON projects(stage)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_project_tasks_project ON project_tasks(project_id)')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS project_task_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        note TEXT NOT NULL,
        created_by INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES project_tasks(id) ON DELETE CASCADE,
        FOREIGN KEY (created_by) REFERENCES users(id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_project_task_notes_task ON project_task_notes(task_id, created_at)')

    ## Stock Movements
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_movements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        movement_type TEXT NOT NULL,
        quantity REAL NOT NULL,
        source_type TEXT,
        source_id INTEGER,
        source_no TEXT,
        movement_date DATE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products(id)
        )
    ''')

    conn.commit()   
    conn.close()
    print("✅ All tables created successfully!")

# ============================================
# RUN DIRECTLY TO INITIALISE DATABASE
# ============================================

if __name__ == "__main__":
    create_tables()
