# Backend FCM HTTP v1

The feature branch uses the existing notification pipeline: an authenticated event
writes its in-app Alerts row, then attempts push for that recipient's enabled
devices. No separate Android event dispatcher or notification database is added.
APNs authentication, endpoints, payloads, and iOS workflows are preserved.

This implementation has not been deployed. No production configuration, credential,
database record, or container was changed, and validation sends no real messages.

## Events and privacy

Existing handlers retain their authorization, recipient filtering, routes, and
notification idempotency keys:

| Event | Notification kind | Recipients / route |
| --- | --- | --- |
| Driver assigned or reassigned | `driver_assignment` | Assigned driver; selected trip |
| Child assigned to a trip | `roster` | Child's parent; selected roster |
| Assignment accepted/declined | `roster_response` | Organiser; selected trip |
| Driver swap requested | `trip_swap_request` | Replacement driver and organiser; trip/swap action |
| Driver swap accepted/declined | `trip_swap_decision` | Existing affected recipients; selected trip |
| Trip started | `trip_start` | Participating children's parents; selected trip |
| Driver delay | `delay` | Existing affected recipients excluding the reporting driver; selected trip |
| Child pickup/dropoff/absence | `handover` | That child's parent; selected trip |

FCM uses `https://fcm.googleapis.com/v1/projects/schoolhop-1c00d/messages:send`
with a short-lived OAuth bearer token obtained from the service account using the
`https://www.googleapis.com/auth/firebase.messaging` scope. The signed JWT and
access token stay in backend memory. Concurrent sends share the cached access token;
it refreshes when less than 60 seconds of validity remain. Credential type, project,
and OAuth endpoint are checked before exchange. File rotation is picked up at the
next exchange; changing the configured path invalidates the cache immediately.

Android payloads use high transport priority, a 60-second FCM TTL, package restriction
`com.schoolhop.app`, private visibility, and channel `schoolhop_activity`. Automated
tests check both the backend payload and the manifest's default channel. Frontend
tests check that the same channel is created with importance 4 and Android's default
sound. Existing device channel preferences remain under user control.

Push title/body remain the generic SchoolHop update already used on this branch.
Child names, addresses, handover notes, and detailed event text remain in the
authenticated Alerts view. Existing action URLs and event kinds are retained.
Logs include provider, HTTP status, retry attempt, and delay; they do not include
device tokens, access tokens, credential contents, exception messages, or provider
response bodies.

## Failure and retry policy

- Each FCM device has a shared 45-second deadline covering OAuth, sends, and waits.
  HTTP operations have a 10-second timeout. OAuth and send operations each permit
  at most three attempts within the same deadline.
- Connection establishment/pool failures and HTTP 500/502/503/504 receive exponential backoff starting
  at 10 seconds, plus up to one second of jitter. Numeric and HTTP-date `Retry-After`
  values are honored. A retry must leave 10 seconds for the next request.
- Read/write timeouts, read/write errors, and protocol/close errors are not retried:
  Firebase may already have accepted the request. This avoids blindly resending an
  ambiguously accepted notification.
- HTTP 429 requires at least 60 seconds of backoff. This exceeds the inline budget,
  so the current push is abandoned without an early retry. Long `Retry-After`
  values are treated the same way. There is no durable retry queue or later replay.
- HTTP 400/401/403/404 are not retried. A send-time 401 evicts the rejected cached
  access token for the next event, without clearing a newer concurrently acquired
  token. Project, permission, credential, payload, quota, and temporary failures
  do not disable a device.
- A typed FCM `UNREGISTERED` HTTP 404 disables only that registration. HTTP 400 is
  cleaned up only when structured `google.rpc.BadRequest` field violations identify
  exclusively `message.token`. Ambiguous `INVALID_ARGUMENT` responses are retained
  because they can also mean a package/payload/configuration error. OAuth error
  responses can never trigger device cleanup.
- Cleanup compares the registration ID, original owner, provider, token, and
  `last_seen_at` snapshot. A token refreshed or reassigned during a request is not
  disabled. Database cleanup/lookup failures and provider failures leave the Alerts
  row intact and do not prevent attempts to subsequent devices, including APNs.

Push remains best effort and is awaited by the existing pipeline: an outage can add
up to 45 seconds per FCM device to an event request. A 5xx response can still
leave acceptance ambiguous, so retry delivery is not exactly once. Existing
Alerts idempotency still prevents duplicate inbox rows and repeat event dispatch;
it does not provide exactly-once delivery across FCM retries. Durable retries and
moving delivery to a worker would be separate work.

References: [HTTP v1 authorization](https://firebase.google.com/docs/cloud-messaging/send/v1-api),
[FCM error codes](https://firebase.google.com/docs/cloud-messaging/error-codes),
[retry guidance](https://firebase.google.com/docs/cloud-messaging/scale-fcm#handling_retries).

## Exact configuration for a separately approved production deployment

The existing credential remains at:

```text
/home/spela/firebase-server/service-account.json
```

Do not copy it into this repository, a Docker build context, an image, an environment
variable containing JSON, or the Android app. Keep its existing restrictive host
permissions. The backend process must be able to read the mounted file. No credential
creation or rotation is needed for this change.

1. During the approved deployment only, add these non-secret settings to the
   production environment used for Compose interpolation:

   ```dotenv
   FCM_PROJECT_ID=schoolhop-1c00d
   FCM_SERVICE_ACCOUNT_FILE=/home/spela/firebase-server/service-account.json
   ```

2. Include the checked-in opt-in override in the production Compose invocation:

   ```text
   docker compose -f docker-compose.yml -f docker-compose.fcm.yml
   ```

   The override mounts the existing file read-only as a Compose file secret at
   `/run/secrets/schoolhop_fcm_service_account` and sets the API container environment:

   ```dotenv
   FCM_SERVICE_ACCOUNT_PATH=/run/secrets/schoolhop_fcm_service_account
   ```

   This is a runtime file mount, not an encrypted secret store managed by Compose.
   File contents are not placed in container environment variables or image layers.

3. The approved deployment must build the reviewed backend source and recreate only
   the API service with that override. No PostgreSQL restart or new schema migration
   is required by this change. Existing `DATABASE_URL`, `APNS_*`, and other settings
   remain as they are. Remove the retired `FCM_SERVER_KEY` entry if present; HTTP v1
   does not use it. Android's client `google-services.json` is a different file and
   must not be substituted for the server credential.

4. The existing account needs FCM send permission (`cloudmessaging.messages.create`)
   and the Firebase Cloud Messaging API enabled in `schoolhop-1c00d`. No IAM/API or
   credential changes were performed here. Real delivery verification requires
   separate approval.

The base Compose file keeps `FCM_SERVICE_ACCOUNT_PATH` empty by default and mounts
no Firebase secret. With an empty path, Android sending is disabled while Alerts and
APNs continue normally. For a later rollback, remove the override and keep that path
empty when recreating the API service under separate approval; do not delete keys
or device registrations.

## Deployment and rollback commands (not executed)

Only run these after separate deployment approval. Set `FCM_RELEASE_SHA` to the
exact commit SHA in the review report. These commands leave `.env` and the existing
credential untouched and recreate only the API service. The API's existing startup
migration runner still runs; this change adds no migrations. Normal production
traffic can use the sender as soon as the approved deployment starts.

```bash
set -e
cd /opt/docker-apps/SchoolHop
export FCM_RELEASE_SHA=PASTE_COMMIT_SHA_FROM_REVIEW
test "$(git branch --show-current)" = feature/schoolhop-my-carpool-ui
test "$(git rev-parse HEAD)" = "$FCM_RELEASE_SHA"
test -z "$(git status --porcelain)"
export FCM_ROLLBACK_IMAGE="schoolhop-api:before-fcm-$FCM_RELEASE_SHA"
# Capture once, before building/recreating. Fail rather than overwrite a saved image.
test -z "$(docker image ls --quiet "$FCM_ROLLBACK_IMAGE")"
docker image tag "$(docker inspect schoolhop-api-1 --format '{{.Image}}')" "$FCM_ROLLBACK_IMAGE"
export FCM_PROJECT_ID=schoolhop-1c00d
export FCM_SERVICE_ACCOUNT_FILE=/home/spela/firebase-server/service-account.json
docker compose -p schoolhop --env-file .env -f docker-compose.yml -f docker-compose.fcm.yml config --quiet
docker compose -p schoolhop --env-file .env -f docker-compose.yml -f docker-compose.fcm.yml build api
docker compose -p schoolhop --env-file .env -f docker-compose.yml -f docker-compose.fcm.yml up -d --no-deps --no-build --force-recreate --pull never --wait --wait-timeout 120 api
# Read-only inspection: this secret mount must report RW=false.
docker inspect schoolhop-api-1 --format '{{range .Mounts}}{{if eq .Destination "/run/secrets/schoolhop_fcm_service_account"}}RW={{.RW}}{{end}}{{end}}'
```

Retain the saved rollback image until the deployment has been accepted. Do not
repeat the snapshot step after deploying, since the current image would then be
the new version. The feature branch must already be at the reviewed commit; these
instructions do not switch branches, merge, or reset the repository.

For rollback, use the same reviewed commit SHA. Restore the saved image without
rebuilding, omit the FCM override, and explicitly empty the sender path:

```bash
set -e
cd /opt/docker-apps/SchoolHop
export FCM_RELEASE_SHA=PASTE_COMMIT_SHA_FROM_REVIEW
export FCM_ROLLBACK_IMAGE="schoolhop-api:before-fcm-$FCM_RELEASE_SHA"
docker image inspect "$FCM_ROLLBACK_IMAGE" --format '{{.Id}}'
docker image tag "$FCM_ROLLBACK_IMAGE" schoolhop-api:latest
FCM_SERVICE_ACCOUNT_PATH= docker compose -p schoolhop --env-file .env -f docker-compose.yml up -d --no-deps --no-build --force-recreate --pull never --wait --wait-timeout 120 api
```

No `down`, database restore, key removal,
credential edit, or database-service restart is part of either command sequence.

The override uses Compose's short `secrets` syntax, which mounts files read-only
under `/run/secrets/` ([Docker reference](https://docs.docker.com/reference/compose-file/services/#secrets)).
The API is its only consumer. Static configuration was checked without creating a
container; the post-deployment inspection above verifies the eventual live mount.

## Offline validation

Backend tests use fake credentials, mocked HTTP responses, and fake database objects.
They do not read the existing server credential, access production PostgreSQL, send
FCM/APNs requests, or start containers. Coverage includes OAuth caching and refresh,
credential validation, channel/TTL/privacy, event routes and recipient scoping,
transient retries, Retry-After, quotas, timeouts, token cleanup races, malformed
provider responses, Alerts persistence/idempotency, and APNs payload/auth preservation.

Compose can be validated without reading credentials or applying configuration:

```bash
docker compose --env-file /dev/null config --no-env-resolution --quiet
FCM_SERVICE_ACCOUNT_FILE=/nonexistent/validation-only.json \
  docker compose --env-file /dev/null \
  -f docker-compose.yml -f docker-compose.fcm.yml config --no-env-resolution --quiet
```

`.dockerignore` restricts the build context to backend inputs and additionally
excludes common credential/key files inside those directories. The Dockerfile copies
only requirements, application source, and migrations. No image was built as part
of this validation.

Validation results: 113 backend tests passed (40 FCM tests), 29 frontend tests
passed, and frontend typechecking/lint, Python compilation, both Compose variants,
and `git diff --check` passed. All provider and database interactions in backend
tests were mocked.
