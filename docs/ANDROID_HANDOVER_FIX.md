# Android handover fix — 21 October 2026

Local branch: `fix/android-handover-20261021`, based on Android release 1.0.26
(12), commit `0d2a885`. No deployment, production data changes, push, signing-key
change or Play upload was performed. The original dirty workspace is untouched.

## Root cause and bundle comparison

Inspected the actual release-12 AAB from Actions run `39548466210` and the iOS
Build 10 IPA from run `39491956703` (reverified/uploaded by `39494477348`).
Both bundles match their respective reviewed release source. Android is not
shipping an accidentally old copy of `app.js`.

| Bundled asset | Android release 12 | iOS Build 10 |
| --- | --- | --- |
| `app.js` SHA-256 | `c0182561f60adfed13cda7de7414881668f2f790c3fd219702f3f322fe86a5bd` | `35ef6844ccc1b830fd1065b5ccb6e7a099d3e7bd199398e2952c869b621f2a68` |
| Handover submit handler | Identical to iOS | Identical to Android |
| Handover button logic | Same trip-specific actions and server-status checks; lowercase labels | Same logic; title-case labels |
| Native configuration | Bundled UI, production API, Capacitor HTTP and FCM plugin | Bundled UI, production API, Capacitor HTTP and APNs plugin |

The JavaScript differences include Android's newer GPS freshness/resume fixes;
replacing the whole UI with the iOS file would discard those fixes. CSS is
identical. Both native configurations have no remote `server.url`; handover
actions are implemented in JavaScript rather than native platform code.

The existing handler locks only one button's DOM node. GPS rendering recreates
both buttons from the still-pending server snapshot; the handler also unlocks
before the detail GET finishes. A successful POST followed by a failed GET
displays failure and leaves pending state actionable. Browser regressions
reproduced these failures against the unmodified release-12 source. These are
shared latent weaknesses, not proof of a distinct iOS-native implementation.
No physical-device reproduction or installed-build inspection was performed.

The map/ETA/viewer polling path also ignores a parent's child completion status.
This was reproduced separately. Service-worker registration is already disabled
in native apps. Capacitor's native bridge forwards request headers but drops
the `fetch` cache option, so Android now explicitly sends `Cache-Control:
no-store`; no stale HTTP response was observed or claimed as the root cause.

## Minimal Android change

* Keep only `Picked Up` + `Absent` for pickup, or `Dropped Off` + `Absent` for
  drop-off, matching Build 10's labels.
* Lock each account/trip/child across both actions and all renders. Preserve a
  server-acknowledged final status for the current session, even if detail
  refresh fails or an older snapshot arrives. Reopening reads the persisted
  server status. Reconcile POST failures before allowing another action.
* Hide that parent's map, ETA and tracking entry and stop viewer polling when
  none of their own children on the trip remain pending. Pending siblings and
  other parents retain tracking. The driver's GPS uploader is not stopped.
* Keep the existing handover endpoint and parent-notification path. All changed
  runtime behaviour is guarded to Android; iOS/web behaviour is retained.

The currently deployed backend source was copied read-only for local synthetic
tests. It already locks the trip/child, validates the action against trip type,
persists status, normalizes legacy absence, rejects contradictory actions and
deduplicates retries/parent notifications. No backend changes are needed for
this Android UI fix. Hiding the UI does not change backend location permissions.

## Focused validation

* 47 frontend tests; frontend lint and typecheck.
* 22 Chromium checks, including 15 handover/cache/iOS regression scenarios:
  pickup/drop-off, absence, rerenders during POST, duplicate/contradictory taps,
  failed detail GET, stale snapshots, lost POST response, refresh/reopening,
  siblings, other parents, ongoing driver GPS and existing iOS behaviour.
* 55 Android release JVM tests; release lint (zero errors, 16 warnings), release
  manifest processing and GPS deadline backport compatibility.
* 9 backend handover tests against both the iOS release source and a read-only
  copy of the deployed backend, using fake databases and mocked delivery. These
  verify the respective parent, one notification on retry and conflict handling.
* Rebuilt/synced Android assets match the fixed source. Native GPS, FCM plugin,
  manifest, signing, deep links, minSdk 24 and version settings are unchanged.

Physical-device handover, FCM delivery and locked-screen GPS still require
acceptance testing. No real notification was sent. This needs an Android-only
release with the next unused versionCode when authorized; no backend deployment
or iOS release is needed.
