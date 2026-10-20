# SchoolHop APNs and handover release candidate

Prepared 20 October 2026 for review only, from baseline `bcbbd2dff2e8f14acffeba1753b7a5151b798171` in a separate detached worktree. No commit, push, deployment, production restart, upload, production data change or real push was performed.

## Candidate and preserved work

Candidate: `/opt/docker-apps/SchoolHop-release-candidates/apns-handover-20261020T061821Z/candidate`.

The original `/opt/docker-apps/SchoolHop` working tree remains intact. All 28 modified/untracked files were archived before preparing this candidate. `../preserved-work/manifest.json` records their SHA-256 hashes, the original Git status and baseline. `working-files.tar.gz` contains the exact working files; `uncommitted.patch` records tracked changes. The original files/status were verified against the manifest after implementation. Unrelated Android/FCM documentation edits are preserved there and in the original tree, outside this candidate.

The candidate's final `../apns-handover.patch` includes every release change, including new files. `../apns-handover-source.tar.gz` contains the baseline source plus release changes, excluding Git metadata, test dependencies, credentials and build outputs. `../release-manifest.json` records release files and hashes. Do not release the original mixed working tree.

## Included behavior

* Backend APNs defaults to the production endpoint, warns safely when unconfigured, sends an alert with sound and generic private lock-screen text, and preserves action links. These visible remote alerts are presented by iOS while locked/backgrounded; delivery does not depend on foreground JavaScript polling or a service worker. Actual device presentation remains an acceptance check.
* Capacitor owns the iOS notification delegate. Native token success/failure callbacks and existing presentation options remain present. This fixes foreground/tap routing ownership; it is not proof of the original background failure's cause.
* APNs uses the same guarded registration snapshot as Android FCM. Logout/reassignment/refresh serialize with an in-flight attempt; stale queued snapshots cannot send to a changed registration. Only `410 Unregistered` disables the matching snapshot. Configuration/topic/transient errors preserve the registration. Logs do not expose device tokens, private keys, raw provider URLs, child names or notes.
* Pickup trips render **Picked Up** as their only completion action; drop-off trips render **Dropped Off**. The existing **Absent** action is retained alongside that trip-specific completion action. Completed/absent actions are disabled; non-drivers cannot act. Server validation independently rejects the wrong trip-specific action with HTTP 409.
* A legacy absence is authoritative even when the old API wrote it into `pickup_status` for a drop-off trip. Detail and list responses expose the effective trip-specific status as absent, using the existing response fields. The private query flag is stripped. Stored history is not rewritten. Under the trip/child row locks, any existing handover prevents a conflicting new action. An identical retry returns the original record and note.
* New handover, child status and audit insertion share one database transaction/connection. An audit failure rolls all three back. Notification delivery happens after commit. Notification failure cannot erase the audit, and retries create no duplicate audits or notifications. Historical events do not receive fabricated audits.
* Android FCM sender behavior, credentials, project/channel, OAuth/retries, native code, Gradle/Firebase configuration and registration API contracts remain intact.

## Release files — complete change list

Application/configuration:

1. `.env.example` — APNs example configuration only; GPS default remains 240.
2. `app/main.py` — APNs transport/cleanup, handover validation/atomic audit, legacy absence read normalization.
3. `app/static/app.js` — handover controls only.
4. `app/static/index.html` — shell version 31.
5. `app/static/service-worker.js` — shell version 31, independent of deferred GPS version 30.
6. `docker-compose.yml` — production APNs default only.
7. `docker-compose.apns.yml` — optional read-only secret mount for an existing APNs key.
8. `ios/App/App/AppDelegate.swift` — Capacitor notification delegate ownership.

Tests:

9. `tests/test_apns.py`
10. `tests/test_fcm.py`
11. `tests/test_handover_actions.py`
12. `tests/test_postgres_notification_concurrency.py`
13. `tests/test_regressions.py` — handover fixtures only; baseline GPS/delay fixtures retained.
14. `tests/frontend/native-push.test.mjs`
15. `tests/frontend/handover-actions.test.mjs`
16. `tests/frontend/pwa.test.mjs` — shell version assertion only.
17. `tests/layout/handover.spec.mjs`
18. `docs/APNS_HANDOVER_RELEASE.md` — this report.

No Android native source or migration files differ from the baseline.

## Explicitly excluded GPS changes

All of the following remain preserved for a later release, outside this candidate:

* Migration `013_location_capture_order.sql`; this candidate contains only the 12 baseline migrations and adds no schema migration.
* Capture-based GPS freshness, capture-first timestamp ordering, future/missing/timezone timestamp validation, index changes and GPS diagnostics.
* Changing `LOCATION_STALE_SECONDS` from 240 to 60. Existing configured production values must be left unchanged for this release.
* Delayed-trip active-tracking changes across backend, JavaScript and Android, including safety/conflict handling changes.
* Viewer lifecycle/network refresh, hidden polling changes, bounded GPS/route requests, local freshness aging, stale marker/ETA handling and stationary iframe preservation.
* GPS-related API/native cache headers, Android service/diagnostic/test edits, `tests/test_gps_freshness.py`, `tests/frontend/gps-freshness.test.mjs` and GPS/browser scenarios from `tests/layout/live-trip.spec.mjs`.
* The combined GPS/iOS/handover report and unrelated existing Android/FCM documentation changes.

Scope verification compared backend function bodies with baseline, confirmed every Android and migration file was unchanged, and confirmed every JavaScript byte outside the handover rendering region matched baseline. GPS defaults/order, input compatibility and delayed-trip implementation match baseline. See `../scope-verification.json`.

## Existing-client compatibility

No endpoint, authentication requirement, registration request/response, APNs payload field or handover input/output field was removed. Detail/list responses keep the existing child status schema. Supported handover types remain `picked_up`, `dropped_off`, `absent`. Valid old-client submissions and identical retries work; wrong-trip/conflicting requests now return HTTP 409, which existing clients already handle as action errors. Older native shells still show their bundled old controls until updated, while backend validation protects them immediately.

Existing iOS production registrations can receive backend alerts after the correct APNs configuration is provided. The native delegate correction and new controls need an updated iOS build. An older iOS build's tap/foreground behavior cannot gain the delegate fix through a backend deployment. Existing Android clients retain FCM compatibility; an Android asset rebuild/update is needed to display the new bundled controls, without native/Firebase configuration changes. Browser/PWA clients receive shell version 31. No builds were created or uploaded in this task.

Compatibility was checked in source/contracts and simulated native bridges; installed physical iOS/Android binaries were not exercised.

## Focused validation results

All final executed checks passed. The complete test suite was not rerun.

| Check | Result | Log |
| --- | --- | --- |
| Backend APNs/FCM/handover plus related notification/route/delay/location regressions | 76 passed, 52 deselected | `../logs/backend-unit.log` |
| Additional list-response legacy/client compatibility regression | 1 passed | `../logs/list-compatibility.log` |
| Real PostgreSQL FCM/handover concurrency, conflicting legacy actions, audit rollback and post-commit notification failure | 15 passed | `../logs/postgres-regressions.log` |
| Revised actual legacy serializer query and APNs row-lock tests | 3 passed, 14 deselected; one replaces/rechecks the prior serializer test, two are additional APNs tests | `../logs/postgres-extra.log` |
| Frontend APNs/Android registration/logout, handover controls and existing PWA regressions | 35 passed | `../logs/frontend.log` |
| Chromium handover scenarios with simulated iOS and Android bridges | 6 passed | `../logs/browser-chromium.log` |
| WebKit handover scenarios with simulated iOS and Android bridges | 6 passed | `../logs/browser-webkit-final.log` |
| ESLint, TypeScript, Ruff, diff whitespace | Passed | `../logs/lint-final.log`, `../logs/typecheck.log`, `../logs/ruff-final.log` |
| Base + FCM + APNs Compose configuration, using placeholder key paths | Passed | `../logs/compose.log` |
| iOS plist parsing and source production/development entitlement/callback checks | Passed | Source validation; no Xcode compilation |

Unique backend coverage: 77 unit/regression tests and 17 PostgreSQL integration tests. The extra PostgreSQL run rechecked the serializer after replacing a duplicated query with the actual application query. Database tests used a newly created disposable PostgreSQL 17 container with networking disabled, no published ports, synthetic data and a temporary Unix socket. It was removed after validation. All Apple/Firebase provider traffic was mocked. No production DSN or credentials were used.

WebKit's first launch timed out while creating a page because the host EGL environment was incomplete; one test failed during setup, one was interrupted and four did not run. Pointing EGL explicitly to the already extracted temporary Mesa vendor library resolved startup. The final six tests passed. Only Playwright's host dependency manifest check was bypassed; no tests were bypassed and no system packages were installed. These are browser engine tests, not physical WKWebView/device delivery tests.

Existing Python `crypt`/passlib and pytest-asyncio fixture-scope deprecation warnings remain. Android native files match baseline, so Android compilation/unit suites were not rerun; Android FCM backend, contract, frontend and lock regressions were run.

## TestFlight credentials and configuration — required, not applied

Use an **existing APNs-enabled authentication signing key** authorized for the production environment and `com.schoolhop.app` (a legacy dual-environment key is also suitable if still valid). The App Store Connect upload API key is not automatically an APNs key. No new key was created and no real private key was read.

| Setting | Exact requirement |
| --- | --- |
| `APNS_KEY_ID` | The 10-character Key ID of that APNs `.p8` signing key. |
| `APNS_TEAM_ID` | The 10-character Apple Developer Team ID that owns the app/key. |
| `APNS_BUNDLE_ID` | `com.schoolhop.app`; must match the installed app and APNs topic. |
| `APNS_USE_SANDBOX` | Explicitly `false` for TestFlight/App Store, selecting `https://api.push.apple.com`. Defaults do not override an existing explicit `true`. |
| `APNS_PRIVATE_KEY_FILE` | With the optional Compose overlay, an existing external `.p8` file, readable by the container's runtime user and mounted read-only. |
| `APNS_PRIVATE_KEY_PATH` | The overlay sets `/run/secrets/schoolhop_apns_private_key`. Without the overlay, use the equivalent existing secure mount/path. |
| `APNS_PRIVATE_KEY` | Leave unset when using the mounted file: an inline value takes precedence over the file. An existing secure inline secret mechanism is an alternative, never a repository/image secret. |

Retain the current FCM overlay and `FCM_PROJECT_ID`, external service-account mount and path unchanged. Ensure outbound TLS/HTTP2 access to APNs on port 443 and a correct server clock for ES256 JWT timestamps. Preserve all GPS settings, including any explicit `LOCATION_STALE_SECONDS` value.

An Apple administrator must verify Push Notifications for the existing App ID and confirm **production `aps-environment` in the signed distribution app/profile**. The checked source Release setting is production; only a Mac/Xcode/signed artifact can verify the actual entitlement. Do not change signing identities or profiles as part of this candidate. Local development should use its isolated sandbox backend and a matching development credential/token.

Physical-device release acceptance remains required after separately approved staging/build distribution: fresh APNs registration, notification authorization with Notification Centre/Lock Screen enabled, assignment/swap/start/delay/handover alerts while locked/backgrounded, foreground presentation, cold/warm notification taps, logout/account switching, and continued Android FCM receipt. Check Focus/Scheduled Summary settings during this controlled test. A registration or provider HTTP 200 alone does not prove Notification Centre presentation.

References: [Apple APNs token authentication](https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns), [Apple APNs registration and entitlements](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns), [Apple notification permission](https://developer.apple.com/documentation/usernotifications/asking-permission-to-use-notifications), [Apple APNs request format](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns).

## Review decision

The APNs/handover candidate is prepared and locally validated for review. Real iOS compilation, signed-profile verification and locked/background Notification Centre delivery remain release gates; this report does not claim end-to-end device acceptance. No GPS improvement or migration 013 is required or included. Stop here for review; do not deploy from the mixed original tree.
