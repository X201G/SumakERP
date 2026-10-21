import re
from datetime import date

from flask import g, has_request_context

from database import is_postgres_configured


_INSERT_PATTERN = re.compile(
    r"INSERT\s+INTO\s+[A-Za-z_][A-Za-z_0-9]*\s*"
    r"\((.*?)\)\s*VALUES\s*\((.*?)\)",
    re.IGNORECASE | re.DOTALL,
)
_PLACEHOLDER_PATTERN = re.compile(r"\?|%s")
_BIND_COLUMN_PATTERN = re.compile(
    r"([A-Za-z_][A-Za-z_0-9.]*)\s*(?:>=|<=|=|>|<|\+)\s*$",
    re.IGNORECASE,
)
_DATE_COLUMNS = {
    "batch_date",
    "dispatch_date",
    "delivery_date",
    "due_date",
    "grn_date",
    "invoice_date",
    "movement_date",
    "order_date",
    "payment_date",
    "po_date",
    "proforma_date",
    "quotation_date",
    "return_date",
    "receive_date",
    "rod_date",
    "sup_invoice_date",
    "valid_till",
    "challan_date",
}
_INTEGER_COLUMNS = {"cr_days", "limit", "offset"}
_INTEGER_TABLE_COLUMNS = {
    "mfg_rods": {"quantity"},
    "mfg_job_batches": {"quantity"},
    "mfg_job_stages": {"quantity_sent", "quantity_received"},
    "mfg_challan_items": {"quantity"},
    "mfg_customer_order_items": {"quantity", "billed_qty"},
    "mfg_resharpen_batches": {"quantity"},
    "mfg_resharpen_stages": {"quantity_sent", "quantity_received"},
    "mfg_resharpen_challan_items": {"quantity"},
}
_NUMERIC_COLUMNS = {
    "amount",
    "bef_tax_amt",
    "billed_qty",
    "cash_discount",
    "diameter_mm",
    "discount",
    "flute_length_mm",
    "length_mm",
    "length_needed_mm",
    "overall_length_mm",
    "purchase_discount_percent",
    "purchase_price",
    "quantity",
    "rate",
    "received_qty",
    "remaining_length_mm",
    "rod_diameter_mm",
    "rod_length_mm",
    "rod_returned_length_mm",
    "sales_discount_percent",
    "sales_price",
    "shank_diameter_mm",
    "tax_amount",
    "tax_rate",
    "total_amount",
    "weight_kg",
}
_DOCUMENT_NUMBER_SOURCES = {
    "sales_invoice": ("sales_invoices", "invoice_no"),
    "sales_quotation": ("sales_quotations", "quotation_no"),
    "sales_order": ("sales_orders", "order_no"),
    "proforma_invoice": ("proforma_invoices", "proforma_no"),
    "sales_challan": ("sales_challans", "challan_no"),
    "sales_return": ("sales_returns", "return_no"),
    "purchase_order": ("purchase_orders", "order_no"),
    "purchase_invoice": ("purchase_invoices", "invoice_no"),
    "purchase_grn": ("purchase_grn", "grn_no"),
    "purchase_return": ("purchase_returns", "return_no"),
}


def _parameter_columns(statement, parameter_count):
    columns = [None] * parameter_count
    insert_match = _INSERT_PATTERN.search(statement)
    if insert_match:
        insert_columns = [
            column.strip().split(".")[-1].strip('"').lower()
            for column in insert_match.group(1).split(",")
        ]
        value_expressions = insert_match.group(2).split(",")
        for index, expression in enumerate(value_expressions):
            if index < len(insert_columns) and _PLACEHOLDER_PATTERN.search(expression):
                columns[index] = insert_columns[index]
        return columns

    for match in _PLACEHOLDER_PATTERN.finditer(statement):
        index = len(_PLACEHOLDER_PATTERN.findall(statement[: match.start()]))
        prefix = statement[: match.start()]
        binding = _BIND_COLUMN_PATTERN.search(prefix)
        if binding and index < parameter_count:
            columns[index] = binding.group(1).split(".")[-1].lower()
        else:
            between_start = re.search(
                r"([A-Za-z_][A-Za-z_0-9.]*)\s+BETWEEN\s*$",
                prefix,
                re.IGNORECASE,
            )
            between = re.search(
                r"([A-Za-z_][A-Za-z_0-9.]*)\s+BETWEEN\b[^?]*"
                r"(?:\?|%s)\s+AND\s*$",
                prefix,
                re.IGNORECASE | re.DOTALL,
            )
            if between_start and index < parameter_count:
                columns[index] = between_start.group(1).split(".")[-1].lower()
            elif between and index < parameter_count:
                columns[index] = between.group(1).split(".")[-1].lower()
            keyword = re.search(r"\b(LIMIT|OFFSET)\s*$", prefix, re.IGNORECASE)
            if keyword and index < parameter_count:
                columns[index] = keyword.group(1).lower()
    return columns


def _postgres_parameters(statement, parameters):
    parameters = tuple(parameters or ())
    columns = _parameter_columns(statement, len(parameters))
    table_match = re.search(
        r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|FROM)\s+"
        r"([A-Za-z_][A-Za-z_0-9]*)",
        statement,
        re.IGNORECASE,
    )
    table = table_match.group(1).lower() if table_match else None
    converted = []
    for column, value in zip(columns, parameters):
        if value is None:
            converted.append(value)
        elif column in _DATE_COLUMNS:
            if isinstance(value, date):
                converted.append(value)
            elif value == "":
                converted.append(None)
            else:
                converted.append(date.fromisoformat(value))
        elif column and (
            column == "id"
            or column.endswith("_id")
            or column in _INTEGER_COLUMNS
            or column
            in _INTEGER_TABLE_COLUMNS.get(table, set())
            or (
                table == "permissions"
                and column in {"can_view", "can_create", "can_edit", "can_delete"}
            )
        ):
            converted.append(None if value == "" else int(value))
        elif column in _NUMERIC_COLUMNS:
            converted.append(None if value == "" else float(value))
        else:
            converted.append(value)
    return tuple(converted)


class _DocumentCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self._postgres = is_postgres_configured()
        self._lastrowid = None

    def execute(self, statement, parameters=None):
        self._lastrowid = None
        if not self._postgres:
            self._cursor.execute(statement, parameters or ())
            return self

        if re.match(r"\s*BEGIN\s+IMMEDIATE\s*;?\s*$", statement, re.IGNORECASE):
            self._cursor.execute("SELECT pg_advisory_xact_lock(%s)", (721946315028411,))
            return self

        parameters = _postgres_parameters(statement, parameters)
        statement = statement.replace("?", "%s")
        statement = re.sub(r"\bLIKE\b", "ILIKE", statement, flags=re.IGNORECASE)
        statement = re.sub(
            r"\bGROUP_CONCAT\s*\(\s*([^()]+?)\s*\)",
            r"STRING_AGG((\1)::text, ',')",
            statement,
            flags=re.IGNORECASE,
        )
        is_insert = re.match(r"\s*INSERT\s+INTO\b", statement, re.IGNORECASE)
        has_returning = re.search(r"\bRETURNING\b", statement, re.IGNORECASE)
        if is_insert and not has_returning:
            self._cursor.execute(statement + " RETURNING id", parameters)
            row = self._cursor.fetchone()
            self._lastrowid = row[0] if row is not None else None
        else:
            self._cursor.execute(statement, parameters)
        return self

    def executemany(self, statement, parameters):
        if not self._postgres:
            self._cursor.executemany(statement, parameters)
            return self
        for row in parameters:
            self.execute(statement, row)
        return self

    @property
    def lastrowid(self):
        if self._postgres:
            return self._lastrowid
        return self._cursor.lastrowid

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _DocumentConnection:
    def __init__(self, connection):
        self._connection = connection
        self._closed = False

    def cursor(self, *args, **kwargs):
        return _DocumentCursor(self._connection.cursor(*args, **kwargs))

    def execute(self, statement, parameters=None):
        cursor = self.cursor()
        cursor.execute(statement, parameters)
        return cursor

    def executemany(self, statement, parameters):
        cursor = self.cursor()
        return cursor.executemany(statement, parameters)

    def close(self):
        if not self._closed:
            self._closed = True
            self._connection.close()

    def __getattr__(self, name):
        return getattr(self._connection, name)


def managed_document_connection(connection_factory):
    def get_connection():
        connection = _DocumentConnection(connection_factory())
        if has_request_context():
            g.setdefault("_document_database_connections", []).append(connection)
        return connection

    return get_connection


def next_document_number(cursor, document_type, scope, prefix, existing_prefix):
    """Allocate a durable document suffix while holding the module write lock."""
    if document_type not in _DOCUMENT_NUMBER_SOURCES:
        raise ValueError(f"Unsupported document-number type: {document_type}")

    table, column = _DOCUMENT_NUMBER_SOURCES[document_type]
    cursor.execute("BEGIN IMMEDIATE")
    row = cursor.execute(
        """SELECT last_value FROM document_number_counters
           WHERE document_type = ? AND scope = ?""",
        (document_type, scope),
    ).fetchone()
    if row is None:
        existing_numbers = cursor.execute(
            f"SELECT {column} FROM {table}"
        ).fetchall()
        last_value = 0
        for existing in existing_numbers:
            number = existing[0]
            if not isinstance(number, str) or not number.startswith(existing_prefix):
                continue
            suffix = number[len(existing_prefix):]
            if not suffix.isdigit():
                raise ValueError(
                    f"Invalid existing {document_type} number suffix."
                )
            last_value = max(last_value, int(suffix))
        last_value += 1
        cursor.execute(
            """INSERT INTO document_number_counters
               (document_type, scope, last_value) VALUES (?, ?, ?)""",
            (document_type, scope, last_value),
        )
    else:
        last_value = int(row[0]) + 1
        cursor.execute(
            """UPDATE document_number_counters SET last_value = ?
               WHERE document_type = ? AND scope = ?""",
            (last_value, document_type, scope),
        )
    return f"{prefix}{last_value:04d}"


def install_document_connection_cleanup(blueprint):
    @blueprint.teardown_request
    def close_document_connections(_error=None):
        for connection in reversed(g.pop("_document_database_connections", [])):
            connection.close()
