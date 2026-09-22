## Plan: Link ERP Logo to Dashboard

Wrap the standard **SUMAK ERP SYSTEM** branding in the shared sidebar with a link to `/`, preserving its current appearance.

**Steps**
1. Update [templates/base.html](templates/base.html#L224-L227) so the logo area links to `/`.
2. Preserve existing styling and leave the manufacturing sidebar unchanged.
3. Validate the template and existing dashboard route in [app.py](app.py#L25-L52).

**Verification**
1. Confirm the complete logo is inside an anchor with `href="/"`.
2. Run the available focused template or application validation.

**Scope**
Only the standard **SUMAK ERP SYSTEM** logo is included.