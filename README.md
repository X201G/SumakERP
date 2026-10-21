# SUMAK ERP

## Runtime and deployment modes

**SQLite development:** leave `DATABASE_URL` unset. The shared `get_connection()`
function opens `DATABASE` (default `sumak_erp.db`), and `python app.py` runs the
SQLite-only `create_tables()` initializer before starting Flask's development
server. Do not use this initializer for PostgreSQL.

**PostgreSQL staging and tests:** configure `TEST_DATABASE_URL` for a dedicated,
disposable non-production PostgreSQL database, and set
`ALLOW_TEST_DATABASE_MIGRATIONS=YES`. The guarded test runner and integration
tests use only that URL. The host or database name must identify a non-production
target; if the provider uses the database name `postgres`, configure
`TEST_DATABASE_HOST_ALLOWLIST` with its exact, independently verified hostname.
PostgreSQL integration tests skip when the URL or opt-in is absent. Supplied but
unsafe configuration remains a test failure. Never use a production database
for integration tests.

**Explicit production migrations:** do not run migrations on application
startup. After reviewing the target, take a recoverable backup, and confirm the
database URL, exact host, database name, and schema out-of-band. Only then invoke
`python -m production_postgres_migrations` with
`ALLOW_PRODUCTION_DATABASE_MIGRATIONS=YES`,
`PRODUCTION_DATABASE_HOST_ALLOWLIST`, `PRODUCTION_DATABASE_NAME`, and
`PRODUCTION_DATABASE_SCHEMA` set for that one command. It uses a production-only
ledger, checksums, and advisory lock; it never seeds users or creates passwords.
The production command has not been run. It refuses to initialize a schema
containing application tables unless its own production ledger is already
present; adopting a schema previously touched by the test runner requires a
separately reviewed ledger-adoption procedure. It does not migrate data from an
existing SQLite installation, and a backup/restore and data migration procedure
must be established before a real cutover.

**SQLite-to-PostgreSQL transfer preparation:** take a consistent, restorable
SQLite backup before planning any transfer. The standalone utility opens the
source read-only, validates its 75-table inventory against migrations
`0001`–`0008`, checks columns/types/primary keys/foreign keys/unique constraints,
checks source foreign-key integrity, counts rows, and orders tables by
dependencies. It does not migrate schema or apply PostgreSQL migrations.

`python -m sqlite_to_postgres --source /secure/path/sumak_erp.db --dry-run`
performs offline source validation and prints row counts. This mode makes no
PostgreSQL connection and therefore cannot certify the destination schema.
The explicit `--apply` mode requires all of
`ALLOW_STAGING_DATA_TRANSFER=YES`,
`TRANSFER_DATABASE_DISPOSABLE=YES`,
`TRANSFER_DATABASE_URL`, `TRANSFER_DATABASE_HOST_ALLOWLIST`,
`TRANSFER_DATABASE_NAME`, and `TRANSFER_DATABASE_SCHEMA`. The destination URL
must require TLS, match the exact allowlisted host/database, identify a
non-production target, and be distinct from the configured production URL. The
tool also checks the connected database, requires the matching checksummed
migration ledger, checks live PostgreSQL columns and constraints, and refuses
non-empty application tables. It copies explicit IDs in foreign-key dependency
order in one transaction, advances identity sequences transactionally, and
validates row counts plus invoice/return/payment totals and stock movement
balances before commit. Any failure rolls back the destination transaction.
Sequence advancement preserves SQLite's recorded AUTOINCREMENT high-water mark,
even when the row with the highest allocated ID was deleted.
It never changes or deletes source data.

The offline plan and mocked destination tests are implemented. No actual
PostgreSQL transfer has been attempted; provider-specific schema compatibility,
permissions, backups/restores, sequence behavior, and real data values still
require an explicitly approved disposable staging run. The SQLite initializer
retains its legacy development administrator creation behavior; the transfer
preflight refuses a detected built-in development administrator password.
Change those accounts in SQLite before taking the transfer backup.

**Initial administrator provisioning:** `python -m initial_admin` is a
separate, explicit PostgreSQL-only command. It requires
`ALLOW_INITIAL_ADMIN_PROVISIONING=YES`,
`ADMIN_PROVISIONING_DATABASE_URL`,
`ADMIN_PROVISIONING_HOST_ALLOWLIST`,
`ADMIN_PROVISIONING_DATABASE_NAME`, and `ADMIN_PROVISIONING_SCHEMA`. The URL
must require TLS and match the exact host and database confirmations. Select
the system, username, and full name as command arguments; the password is
prompted twice without echo, must be at least 12 characters, and is stored only
as a Werkzeug password hash. An advisory lock serializes creation, and
provisioning refuses a system that already has an administrator. It never runs
during app startup and does not change another system's users or permissions.
This command has only been unit-tested with a mocked connection, not against
PostgreSQL.

**WSGI and frontend hosting:** this application is a server-rendered Flask
application. Flask/Jinja renders the pages and serves its files under `static/`;
there is no separate static-frontend build or frontend deployment target. For a
Bluehost Ubuntu VPS, host the full Flask application with Gunicorn behind Nginx.
Example service and reverse-proxy templates are in
[`deploy/`](./deploy/). They use placeholders and must be
reviewed and adapted to the actual domain, certificate, filesystem paths, Unix
user, resource limits, and Supabase connection budget before installation.

For production, set `DATABASE_URL`, `APP_ENV=production`, and a persistent,
random `FLASK_SECRET_KEY` through a protected service environment file or
secret manager. Keep that file readable only by root and the service account;
never put credentials in the repository, unit file, command history, or logs.
The provided production WSGI configuration marks session cookies Secure,
HttpOnly, and SameSite=Lax and trusts exactly one reverse proxy for forwarded
client, scheme, and host headers. The example binds Gunicorn to loopback; do not
expose it directly to the internet. Configure Nginx to set the forwarded
headers as shown, terminate HTTPS, and allow inbound traffic only for SSH and
HTTPS. Use a non-root service account and a dedicated virtual environment.

Run `gunicorn ... app:app` for WSGI startup. Importing `app.py` does not
initialize the schema or run migrations. Direct `python app.py` startup is
limited to SQLite development and refuses production or PostgreSQL
configuration, so it cannot invoke the legacy SQLite initializer on a
PostgreSQL target. PostgreSQL schema setup remains a separate, explicit
operator action; no migration command should run from WSGI startup.

For Supabase, configure the provider's supported PostgreSQL connection string
with TLS in the protected service environment. Select the endpoint mode
appropriate for the VPS network and Supabase connection budget. The application
uses a per-process Psycopg pool; account for every Gunicorn process and pool
limit when choosing worker counts. Provider connectivity, IPv4/IPv6 routing,
pooler compatibility, and the deployed environment still need staging
verification. These templates do not establish deployment or PostgreSQL
readiness.

PostgreSQL uses Psycopg 3 and a connection pool. Each caller owns and must close
its connection; PostgreSQL callers must explicitly commit successful writes.
Closing a connection rolls back uncommitted work before returning it to the
pool. Rows support both integer and column-name indexing. These features are
connectivity support, not proof of complete workflow compatibility.

## PostgreSQL migration slices

The reviewed migrations `0001`–`0008` define 75 application tables. The
table-to-migration inventory is:

| Migration | Tables |
|---|---|
| `0001_hiring_application_slice.sql` | `users`, `hiring_jobs`, `hiring_candidates` |
| `0002_delivery_workflow.sql` | `delivery_routes`, `delivery_route_stops`, `delivery_notifications`, `delivery_push_subscriptions` |
| `0003_projects_workflow.sql` | `projects`, `project_user_shares`, `project_tasks`, `project_task_notes` |
| `0004_distribution_stock.sql` | `categories`, `subcategories`, `units`, `hsn`, `suppliers`, `products`, `stock_movements` |
| `0005_distribution_documents.sql` | `customers`, `transporters`, `purchase_orders`, `purchase_order_items`, `purchase_invoices`, `purchase_invoice_items`, `purchase_grn`, `purchase_grn_items`, `purchase_returns`, `purchase_return_items`, `sales_invoices`, `sales_invoice_items`, `sales_quotations`, `sales_quotation_items`, `sales_orders`, `sales_order_items`, `proforma_invoices`, `proforma_invoice_items`, `sales_challans`, `sales_challan_items`, `sales_returns`, `sales_return_items` |
| `0006_distribution_payments.sql` | `sales_payments`, `purchase_payments` |
| `0007_manufacturing.sql` | `mfg_rods`, `mfg_brands`, `mfg_item_categories`, `mfg_items`, `mfg_purchase_orders`, `mfg_purchase_order_items`, `mfg_purchase_grn`, `mfg_purchase_grn_items`, `mfg_purchase_invoices`, `mfg_purchase_invoice_items`, `mfg_purchase_returns`, `mfg_purchase_return_items`, `mfg_tools`, `mfg_job_batches`, `mfg_job_stages`, `mfg_challans`, `mfg_challan_items`, `mfg_invoices`, `mfg_customer_orders`, `mfg_customer_order_items`, `mfg_invoice_items`, `mfg_stock_movements`, `mfg_sales_payments`, `mfg_purchase_payments`, `mfg_resharpen_batches`, `mfg_resharpen_stages`, `mfg_resharpen_challans`, `mfg_resharpen_challan_items`, `mfg_resharpen_invoices`, `mfg_resharpen_invoice_items`, `mfg_resharpen_sales_payments`, `permissions` |
| `0008_document_number_counters.sql` | `document_number_counters` |

Shared dependencies are created by their earlier migrations, not duplicated
by `0007`. This inventory is versioned in `migrations/postgres/` and separate
from SQLite's `create_tables()`; migrations are not run when Flask is imported.
Applied migration files are immutable: the runner stores SHA-256 checksums and
refuses edited or missing applied migrations. Each migration and its ledger
entry run in one transaction under a PostgreSQL advisory lock and are scoped to
the explicit target schema.

The migrations are versioned schema definitions, not a data migration or a
deployment-readiness guarantee. The guarded test runner
(`python -m postgres_migrations`) reads only `TEST_DATABASE_URL`; it never falls
back to `DATABASE_URL`. Integration tests create and drop uniquely named schemas
inside that test database. Historical SQLite migration scripts must never be
run against PostgreSQL.

Migration `0008_document_number_counters.sql` adds the counter table used by
Distribution sales and purchasing documents. Number allocation is serialized
within the document transaction, preserves the existing document type formats,
uses a daily scope for purchase orders and global series for other document
types, and does not reuse committed numbers when a document is deleted. Existing
SQLite databases gain the corresponding table during explicit SQLite
initialization. Fresh and existing SQLite schemas now include the Distribution
stock rate/amount, invoice source references, and sales order/challan billed
quantity columns used by posting workflows. Existing SQLite rows are preserved
by adding missing columns rather than rebuilding those tables.

### Future operator command examples — not executed

Use a dedicated disposable database for staging/integration checks. Replace the
placeholders locally; do not put a real URL in source control:

```sh
export TEST_DATABASE_URL='postgresql://<user>:<password>@<test-host>:5432/<test-database>?sslmode=require'
export ALLOW_TEST_DATABASE_MIGRATIONS=YES
python -m postgres_migrations
pytest tests/test_postgres_migrations.py tests/test_masters_postgres.py
```

The production command is a separate operation. First verify the intended
production host, database, schema, backup, and restore plan out-of-band. Supply
these values only in a protected operator shell or secret manager, then invoke
the command explicitly:

```sh
export DATABASE_URL='postgresql://<user>:<password>@<production-host>:5432/<production-database>?sslmode=require'
export ALLOW_PRODUCTION_DATABASE_MIGRATIONS=YES
export PRODUCTION_DATABASE_HOST_ALLOWLIST='<exact-production-host>'
export PRODUCTION_DATABASE_NAME='<exact-production-database>'
export PRODUCTION_DATABASE_SCHEMA='<existing-schema>'
python -m production_postgres_migrations
```

These are examples only; neither staging nor production commands have been run
as part of this work.

For SQLite transfer preparation, use a consistent backup copy and start with
the offline source-only plan:

```sh
python -m sqlite_to_postgres --source /secure/path/sumak_erp-backup.db --dry-run
```

After the separate staging schema has been migrated and verified, a disposable
staging transfer can be explicitly opted into. The destination must be empty;
the command does not run migrations:

```sh
export TRANSFER_DATABASE_URL='postgresql://<user>:<password>@<staging-host>:5432/<sumak_stage>?sslmode=require'
export TRANSFER_DATABASE_HOST_ALLOWLIST='<exact-staging-host>'
export TRANSFER_DATABASE_NAME='<sumak_stage>'
export TRANSFER_DATABASE_SCHEMA='<existing-schema>'
export TRANSFER_DATABASE_DISPOSABLE=YES
export ALLOW_STAGING_DATA_TRANSFER=YES
python -m sqlite_to_postgres --source /secure/path/sumak_erp-backup.db --apply
```

After migrations are present, provision an administrator only for a portal
without an existing admin account. Set the exact target variables in a
protected operator environment; the password is entered interactively:

```sh
export ADMIN_PROVISIONING_DATABASE_URL='postgresql://<user>:<password>@<database-host>:5432/<database>?sslmode=require'
export ADMIN_PROVISIONING_HOST_ALLOWLIST='<exact-database-host>'
export ADMIN_PROVISIONING_DATABASE_NAME='<database>'
export ADMIN_PROVISIONING_SCHEMA='<existing-schema>'
export ALLOW_INITIAL_ADMIN_PROVISIONING=YES
python -m initial_admin --system manufacturing --username '<operator-name>' --full-name '<operator-full-name>'
```

These command examples have not been executed. A staging transfer is not a
production cutover plan; production import needs a reviewed backup/restore,
data reconciliation, admin provisioning, rollback, and approval procedure.

The public hiring routes now bind PostgreSQL parameters with Psycopg syntax
while retaining SQLite placeholders on SQLite. They close every acquired
connection, preserve explicit commits, roll back database failures, and return
a generic service-unavailable response without logging database messages or
submitted applicant details.

The Distribution Stock list, ledger and products-with-stock API have also been
adapted for PostgreSQL. The new stock migration uses `DOUBLE PRECISION` for
SQLite `REAL` columns to retain the application's existing floating-point
behavior. Sales and Purchases posting was not included in the Stock slice;
Manufacturing stock remains separate and out of scope.

The Sales and Purchases blueprints use a module-local DB-API adapter for
PostgreSQL placeholders, typed date/numeric parameters and generated IDs.
Migration `0005_distribution_documents.sql` adds their customer/transporter
dependencies and document headers/items. Sales invoices, challans and returns,
and purchase invoices, GRNs and returns post through the shared Distribution
stock ledger within the same explicit transaction as their document lines.
Document numeric fields use `DOUBLE PRECISION` to match the existing SQLite
`REAL` calculations rather than changing monetary rounding semantics in this
compatibility slice.
The blueprints close outstanding connections during request teardown so an
exception rolls back uncommitted work before a pooled connection is reused.
Customer, supplier, product and transporter records are not copied by these
schema migrations. Payment tables are introduced separately by
`0006_distribution_payments.sql`.
Guarded PostgreSQL integration tests must pass against the configured
non-production database before claiming staging verification.

The Accounting blueprint's sales and purchase payment routes, invoice balance
APIs, outstanding reports, statements and daybook use the shared PostgreSQL
adapter while retaining SQLite behavior. Payment amounts use `DOUBLE PRECISION`
to preserve SQLite `REAL` behavior. These payment changes have local regression
coverage; PostgreSQL staging verification is still required before claiming
production compatibility.

Migration `0007_manufacturing.sql` adds the 31 existing Manufacturing and
Resharpening tables plus the `permissions` table. It reuses shared tables
introduced by earlier migrations, including users, customers, suppliers,
transporters and units; it does not recreate those tables. The Manufacturing,
Manufacturing Accounting and Resharpening blueprints use the existing document
database adapter for PostgreSQL placeholders, generated IDs, date/numeric
bindings, case-insensitive searches and request connection cleanup. The scope
covers these modules' existing workflows and separate Manufacturing and
Resharpening stock/payment tables; SQLite remains supported.

Run PostgreSQL integration checks only through the guarded staging/test
workflow. The migration runner rejects unsafe targets and scopes application
to its explicit schema. PostgreSQL compatibility has not been verified
end-to-end against a confirmed safe PostgreSQL target.

## Ubuntu VPS deployment preparation

No deployment has been performed. CI currently runs Python 3.10; select Python
3.10 or newer for the VPS and run the full regression suite on the exact
interpreter before release. Install the repository in a dedicated virtual
environment as a non-root application user. The example commands below are
operator guidance, not executed deployment commands:

```sh
sudo adduser --system --group --home /opt/sumakerp sumakerp
sudo install -d -o sumakerp -g sumakerp /opt/sumakerp
sudo -u sumakerp python3 -m venv /opt/sumakerp/.venv
sudo -u sumakerp /opt/sumakerp/.venv/bin/pip install -r /opt/sumakerp/requirements.txt
```

Use a protected systemd environment file such as
`/etc/sumakerp/app.env`, owned by root and readable only by root. Provide
`APP_ENV=production`, a persistent random `FLASK_SECRET_KEY`, and the selected
PostgreSQL `DATABASE_URL` there. Add the Delivery VAPID variables only if push
notifications are enabled. Do not place production migration opt-in values in
the WSGI service environment. A representative unit-file shape is:

```ini
[Unit]
Description=SumakERP Flask application
After=network-online.target
Wants=network-online.target

[Service]
User=sumakerp
Group=sumakerp
WorkingDirectory=/opt/sumakerp
EnvironmentFile=/etc/sumakerp/app.env
ExecStart=/opt/sumakerp/.venv/bin/gunicorn --workers 1 --threads 4 --bind 127.0.0.1:8000 --access-logfile - --error-logfile - app:app
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Terminate HTTPS at Nginx or an equivalent maintained reverse proxy and proxy
only to Gunicorn on loopback. Configure certificate renewal, request size and
timeout limits, and only forward trusted proxy headers. Keep SSH restricted,
disable password-based root login, apply Ubuntu security updates, and expose
only SSH and HTTPS at the firewall. Route systemd stdout/stderr to the journal,
restrict journal access, set retention, and monitor disk, memory, process
restarts, reverse-proxy errors, and database connectivity. There is not yet a
dedicated unauthenticated readiness endpoint; until one is designed, use
systemd process state plus a controlled authenticated smoke test rather than
presenting a static liveness response as proof that the database is ready.

The application pool currently permits up to 10 database connections per
process, with a 10-second acquisition timeout. Gunicorn workers each create
their own pool. For approximately 15 users, set a total connection budget across
all workers, background processes, and administration tools that is within the
selected Supabase plan's limits, then measure request latency and pool waits
under representative load. Do not infer capacity from the number of user
accounts. Prefer a TLS-enabled direct or session-pooler connection appropriate
for the VPS; confirm the provider's pooler mode, network access rules, startup
parameters, and connection limits before choosing it. The production migration
command and runtime connection mode have not been verified against Supabase.

For staging CI, keep the ordinary SQLite job independent of PostgreSQL secrets.
If a later dedicated PostgreSQL job is approved, provide a disposable database
URL as a protected GitHub Actions secret and set the explicit test opt-in and
exact host allowlist in that job only. Use a separate database that can safely
be destroyed; never add production secrets to CI. The current workflow does not
configure or run that integration job.

## SQLite data transfer and cutover plan

Migrations create schema only; they do not copy SQLite records. The
`sqlite_to_postgres` utility now implements an offline source plan and an
explicit, guarded transfer to an already-migrated, empty disposable staging
schema. It has been exercised against temporary SQLite sources and mocked
destinations only. It has not connected to PostgreSQL or transferred business
data.

1. Put the application in a maintenance window and create a protected,
   integrity-checked copy of the SQLite database. Test restoration of that copy
   to a separate temporary path; never use the live source file as a work area.
2. Run `python -m sqlite_to_postgres --source <backup> --dry-run`. It validates
   source tables, columns, compatible type families, nullability, primary keys,
   foreign keys/actions, unique constraints/collations, row counts, dependency
   ordering, scalar date/numeric/boolean/text/binary conversions, and SQLite
   foreign-key integrity. It refuses data still using the built-in development
   administrator password. This is an offline check only; it cannot validate
   the live target.
3. Create and migrate a disposable staging schema separately using the
   approved PostgreSQL test/production migration workflow as appropriate.
   Review the migration-ledger/checksum match and ensure every application
   table in the transfer schema is empty. The transfer tool never runs
   migrations and refuses a non-empty schema.
4. After explicit approval, configure only the dedicated staging URL, exact
   host allowlist, database/schema confirmation, disposable-target confirmation,
   and transfer opt-in. Run `--apply` against the protected backup. It validates
   the connected database and live columns, nullability, primary/foreign keys,
   FK actions, unique constraints, and migration ledger; copies parents before
   dependents with explicit IDs; converts NULL, dates/timestamps, booleans,
   numeric values, and byte strings; and advances identity sequences inside the
   same transaction. It does not update, delete, or write to the SQLite source.
5. Review the transfer report and independently reconcile all row counts,
   per-invoice and per-return amounts, per-payment records, stock movement
   balances, outstanding balances, and document references. Current automated
   business checks compare table counts, invoice/return/payment keys and totals,
   and stock quantities grouped by item and movement type. They are not a
   substitute for accounting sign-off or end-to-end workflow verification.
6. Run the complete SQLite suite and the guarded PostgreSQL integration suite
   against the destination before cutover. Keep the source backup read-only.
   Switch the service only after application, accounting, and business owners
   approve the checks; retain the old SQLite deployment and backup for the
   agreed rollback window.
7. Define rollback triggers and who authorizes them in advance. If PostgreSQL
   writes have begun, do not simply point the application back at the stale
   SQLite file; freeze writes and reconcile the post-cutover delta first.

PostgreSQL migrations do not create a default account. The explicit
`initial_admin` command prompts for a new password in an interactive terminal,
hashes it with the application password-hashing library, and uses system scope.
It has only been tested with a mocked connection; production/staging use still
requires operator review, an approved secret-handling procedure, and successful
database verification. Do not use the development-only `create_tables()`
seeding path in production.

## Production readiness checklist

- [ ] Choose the Supabase connection endpoint/mode and confirm TLS, DNS,
  outbound firewall, startup-parameter, and connection-budget requirements.
- [ ] Review all migrations, the production ledger policy, target schema, and
  checksums; confirm backup and tested restore before any production migration.
- [ ] Resolve migration-ledger adoption if the target schema already contains
  application tables.
- [ ] Review and explicitly approve the transfer safety configuration and
  backup/restore process.
- [ ] Execute and reconcile the transfer against a disposable PostgreSQL
  staging database; confirm all table counts, foreign keys, identities,
  accounting totals, stock balances, and document references.
- [ ] Provision and verify the system-scoped initial administrator against the
  intended PostgreSQL target using the interactive command.
- [ ] Configure protected environment secrets, HTTPS, systemd hardening, reverse
  proxy, firewall, log retention, and monitoring.
- [ ] Decide on a database-backed readiness check and verify alerts without
  exposing credentials or internal errors.
- [ ] Run full SQLite regressions and the guarded PostgreSQL suite on a confirmed
  disposable target; review every skip and failure.
- [ ] Obtain business approval for final smoke tests, maintenance window,
  rollback criteria, and cutover.

The ERP is not declared PostgreSQL- or deployment-ready. PostgreSQL integration
was not run because no test database URL, explicit migration opt-in, or safe
target allowlist is configured in this environment.

## Delivery browser and OS push notifications

The Delivery portal sends Web Push notifications to driver browsers and installed
Progressive Web Apps (PWAs). A driver must open the portal on each device and
select **Enable push notifications**. The browser asks for permission and
registers that device. Route assignment then sends a push alert that opens the
assigned route. Push payloads contain the route title and date, not customer
contact information.

### Server configuration

Web Push requires a VAPID key pair and an HTTPS deployment (localhost is suitable
for local development). Generate the key files in a secure directory:

```sh
vapid --gen
vapid --private-key ./private_key.pem --applicationServerKey
```

The second command prints the browser public key after `Application Server Key =`.
Configure these environment variables for the application process:

```text
DELIVERY_VAPID_PUBLIC_KEY=<Application Server Key value>
DELIVERY_VAPID_PRIVATE_KEY=<absolute path to private_key.pem>
DELIVERY_VAPID_SUBJECT=mailto:delivery-ops@example.com
```

Keep `private_key.pem` private, do not commit it, and use the same VAPID key pair
for the lifetime of registered subscriptions. If keys are rotated, drivers need
to enable push again on each device. Install the Python dependencies
from `requirements.txt` and restart the application after setting the variables.
If push is unavailable or a driver has not enabled it, route assignments still
appear in the driver's in-app notifications and the dispatcher sees the status.

For OS notifications on a phone, use a browser that supports Web Push and allow
notifications; installing the Delivery PWA from the browser is recommended.
Support and installation requirements vary by browser and operating system.
This is web/PWA push, not a separate native iOS or Android app integration.