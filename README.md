# SchoolHop

Standalone school carpool MVP. This app is intentionally separate from Family AI: separate source tree, Docker Compose project, containers, environment variables, database, and database user.

## Current Stack

- FastAPI on Python 3.12
- PostgreSQL 17
- Installable Safari/Chrome Progressive Web App served by FastAPI
- Docker Compose project name: `schoolhop`
- SQL migrations in `migrations/`

The neighboring VPS has Caddy on the shared `ai-net`, and Family AI uses FastAPI/Postgres. This project does not import Family AI application code and does not modify Family AI containers or data.

## Local Setup

```bash
cp .env.example .env
docker compose up --build
```

Open http://127.0.0.1:8088.

Seed sample development data:

```bash
docker compose exec api python -m app.seed
```

Sample logins:

- `ava@example.com` / `schoolhop-dev`
- `ben@example.com` / `schoolhop-dev`

## Environment

- `DATABASE_URL`: SchoolHop database connection string.
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`: local database container credentials.
- `JWT_SECRET`: signing secret for bearer tokens. Replace for every environment.
- `CORS_ORIGINS`: comma-separated allowed browser origins.
- `RAW_LOCATION_RETENTION_MINUTES`: short retention period for raw GPS updates. Default: 120.
- `LOCATION_STALE_SECONDS`: age after which GPS is flagged stale. Default: 90.
- `SAFETY_TIMEOUT_MINUTES`: maximum active trip sharing window. Default: 180.
- `ENVIRONMENT`, `EXPOSE_DEVELOPMENT_VERIFICATION_CODES`: allow local/test-only exposure of verification codes when email is not configured.
- `EMAIL_PROVIDER=agentmail`, `AGENTMAIL_API_KEY`, `AGENTMAIL_FROM_EMAIL`: AgentMail delivery for account/security email only, including email verification and forgot/reset password.
- `EMAIL_PROVIDER=smtp`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_USE_TLS`: SMTP fallback for the same account/security email workflow.
- `FCM_SERVER_KEY`, `APNS_*`: reserved for production mobile push credentials, separate from Family AI.
- `PUBLIC_SITE_URL`: canonical HTTPS URL for phone testing. Use `https://schoolhop.shourish.com`.
- `GOOGLE_MAPS_BROWSER_KEY`: browser-visible Maps JavaScript API key. Restrict it to SchoolHop HTTP referrers such as `https://schoolhop.shourish.com/*`.
- `GOOGLE_ROUTES_API_KEY`: server-side Routes API key. Keep it out of source control and browser responses.

## Git Workflow

SchoolHop is maintained in GitHub at `https://github.com/shourish17/SchoolHop.git`.

- Default branch: `main`.
- Keep the working tree free of secrets. `.env`, `.env.*`, local backups, database dumps, certificates, npm tokens, SMTP passwords, AgentMail keys, APNS/FCM keys, and Google server keys must stay out of Git.
- Use `.env.example` for variable names and GitHub Actions secrets for sensitive CI/deployment values.
- Open feature work from topic branches and merge back through pull requests when possible.
- Run the validation suite before pushing:

```bash
npm install
npm test
npm run typecheck
npm run lint
python -m unittest discover -s tests -v
python -m py_compile app/main.py app/seed.py
docker compose config --quiet
```

GitHub Actions runs the same PWA/backend validation on pushes to `main`, pull requests, and manual dispatch. The workflow intentionally does not deploy SchoolHop; production deploys must be a separate, explicit operation.

## MVP Journey

1. Register with email verification or sign in as a parent.
2. Add/select a school.
3. Create a private invitation-only carpool group.
4. Invite another parent by email.
5. Add child profiles with only carpool-relevant details.
6. Approve which group drivers may transport each child.
7. Create roster trips with driver, date, type, children, and expected time.
8. Assigned drivers accept or decline the assignment.
9. Driver starts a trip, explicitly permits browser GPS, posts foreground location updates, records pickups/drop-offs/absences, reports delays or unavailability, and ends the trip.
10. Parents see current and future trips that include their child, or trips they drive/create. Completed, cancelled, unavailable, and past trips move to History.

## Permission Model

The server enforces:

- groups are private and invitation-based;
- one email account can have only one active group membership and must leave before joining or creating another group;
- every group read/write requires active `group_members` membership;
- trip reads require either assigned driver status, child parent status for an assigned child, or group creator status;
- child handover and live GPS writes require the assigned driver;
- driver assignment requires explicit approval from each child's parent;
- child detail visibility is limited to the assigned driver, group creator, or that child's parent for the trip.

Roster changes and handovers write `audit_records`. Notifications are stored in the app database for invitations, roster updates, trip start/end, delays, cancellation, driver unavailability, handovers, and roster responses. Normal SchoolHop activity does not send email; it uses in-app notifications and configured APNS/FCM push notification when provider credentials and device tokens are present. Email is reserved for account/security functions such as email verification and forgot/reset password.

## PWA GPS Tracking Limitation

The PWA uses browser geolocation only after an assigned driver explicitly starts a trip. This supports the pilot flow in Safari on iPhone and Chrome on Android while the browser allows foreground location.

Do not present this PWA phase as Uber-style continuous tracking. iOS and Android browsers may pause JavaScript timers and geolocation when the phone is locked, the browser is backgrounded, battery saver is enabled, or permission is revoked. Reliable locked-screen tracking remains a later native app phase. See `mobile/background-location-notes.md` and the untouched `mobile/schoolhop-mobile` React Native prototype.

The API accepts driver location only during an active trip, exposes latest trip location only to authorized parents/drivers, reports freshness, stops after trip end or safety timeout, and keeps raw updates only for the explicit short retention period.

## PWA Setup

The PWA is served by the existing FastAPI app. It includes:

- `app/static/manifest.webmanifest` for installation.
- `app/static/service-worker.js` for app-shell caching only.
- no caching for `/api/*`, account, child, trip, notification, or GPS responses.

Phone GPS requires HTTPS. Use `https://schoolhop.shourish.com` for iPhone Safari and Android Chrome testing after the site is deployed by a separate deployment change.

### Google Maps and Routes

Create separate Google Cloud keys:

- Browser Maps key: enable Maps JavaScript API, restrict by HTTP referrer to `https://schoolhop.shourish.com/*` and any explicit staging origin, and set `GOOGLE_MAPS_BROWSER_KEY`.
- Server Routes key: enable Routes API, keep it only in `.env`/server secrets as `GOOGLE_ROUTES_API_KEY`, and do not expose it in source control or browser JavaScript.

Google Maps Platform requires billing for Maps JavaScript API and Routes API. Set daily quota limits and budget alerts in Google Cloud Console before a phone pilot. The PWA calls Routes from the backend for selected-trip ETA to the group's school address, because child home addresses are not stored in the current SchoolHop data model.

### Phone Checklist

1. Deploy SchoolHop separately to HTTPS at `https://schoolhop.shourish.com`; do not change Family AI, DNS, or Caddy as part of local PWA development.
2. Open the site in iPhone Safari and Android Chrome.
3. Sign in or create two parent accounts.
4. Create a school with a real street address, then create a private group.
5. Invite the second parent and accept the invitation token from that account.
6. Add children and approve trusted drivers.
7. Create a pickup/drop-off roster trip.
8. As the assigned driver, open the Trip tab, tap Start trip, and allow precise location.
9. Keep the PWA visible and confirm the parent device sees the map marker, freshness, route/ETA when configured, and handover/status notifications.
10. Lock the driver phone and background the browser briefly to observe browser limitations; location updates may pause.
11. End the trip and confirm tracking stops and no new GPS updates are sent.

Web push is intentionally not part of the core pilot PWA flow yet. In-app notifications poll the existing `/api/notifications` endpoint. Add standards-based Web Push later only after adding VAPID subscription storage and delivery support to the backend.

## VPS/Caddy Notes

Do not deploy or change the live VPS without explicit instruction.

For a later VPS deployment, use a dedicated SchoolHop DB/user. If reusing a shared PostgreSQL host, create only a `schoolhop` database and `schoolhop` user for this app, then point `DATABASE_URL` at that host. Do not connect to Family AI's application database.

Caddy can reverse proxy to the SchoolHop API container after the container is joined to the shared proxy network. That should be done in a deployment change set, not by this local MVP setup.
