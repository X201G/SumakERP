# SUMAK ERP

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