# Android FCM integration report

Work is confined to the existing `feature/schoolhop-my-carpool-ui` branch. The reviewed integration is committed and pushed on this branch, then validated by manually dispatching Android Build with `require_firebase=true`. Production deployment/restarts, database or backend configuration changes, server-side Firebase credentials, Play uploads, signing-key changes, iOS workflow changes, and merging into main are outside this task.

## Existing implementation and gaps

The maintained Android app is the root Capacitor project (`android/`), not the archived React Native prototype under `mobile/`. Capacitor and Gradle already use `com.schoolhop.app`. Google Services Gradle support, the Capacitor push dependency, Android 13 notification permission, a Firebase resource check, and a native startup guard were already present.

The frontend already requested notification permission after authentication, created `schoolhop_activity`, listened for registration events, and posted Android tokens as `provider=fcm`, `platform=android`. Capacitor's native messaging service forwards `onNewToken` to that registration listener. The backend already had authenticated registration, unique `(provider, token)` ownership reassignment, and enabled-device lookup. Existing activity handlers write an in-app notification and call the shared push sender with recipient filtering and idempotency.

The gaps addressed here were:

- No secure config staging or GitHub Actions Firebase secret integration.
- Guarded `register`/`unregister` overrides lacked `@PluginMethod`, so Capacitor could not export them to JavaScript.
- The backend sender still used legacy FCM server-key authentication and `/fcm/send`.
- No authenticated device removal or Sign Out cleanup; refresh/account-change races and failed refresh cleanup were unhandled.
- Some events lacked a push `action_url` fallback, URLs lacked an origin restriction, and cold-launch taps were not forwarded by the stock plugin.
- Notification bodies/titles could expose child names or free-text notes; the channel had public visibility.
- Provider authentication/configuration errors could incorrectly disable valid devices.

## Resulting behavior

| Event | Existing notification kind | Destination |
| --- | --- | --- |
| Driver assignment | `driver_assignment` | Assigned trip |
| Parent trip assignment | `roster` | Roster with selected trip |
| Driver accept/decline | `roster_response` | Selected trip, using new fallback |
| Swap request | `trip_swap_request` | Trip; replacement driver's link includes `swap_request` |
| Swap accept/decline | `trip_swap_decision` | Trip |
| Delay | `delay` | Trip |
| Child pickup/dropoff/absence | `handover` | Trip |
| Group invitation for a registered invitee | `invitation` | Group invitation acceptance |

These handlers already existed and retain their recipient and authorization rules. A recipient must have an enabled device registration and allow notifications. Invitations to an email without a SchoolHop account cannot produce push to an account that does not exist.

The native plugin retains cold-launch tap events for the frontend. Navigation accepts relative app routes, the configured API origin, the SchoolHop HTTPS origin, and the existing `schoolhop:` scheme. Pending trip/swap/invite navigation survives the login flow. Backend APIs still authorize access to the linked entities.

Registration callbacks are serialized with refresh and Sign Out. Tokens are saved locally before registration requests so ambiguous failures can be cleaned up. Old tokens remain recorded until removal succeeds. App resume rechecks permission without prompting and retries registration; listeners are installed once. Sign Out disables every recorded device token for the authenticated owner, waits for native FCM deletion, clears delivered notifications, then clears authentication. If device removal fails, it retains the session and offers a retry. A native build without a registered token can still sign out offline.

`DELETE /api/mobile/devices` accepts the same provider/token/platform JSON as registration. Its update is scoped by authenticated `user_id`, provider, and token, so a previous owner cannot disable a token reassigned to another account. No schema migration is needed.

Push previews now say only “SchoolHop” and “You have a new SchoolHop update. Open the app to view it.” Full titles, child information, and notes remain in the authenticated inbox. Android uses private visibility and an explicit `schoolhop_activity` channel. Existing installations may retain earlier channel visibility settings; payload previews are generic in either case. Shared APNs previews receive the same privacy protection; iOS workflows are unchanged.

The sender uses FCM HTTP v1 with scoped, short-lived OAuth access tokens, cached until shortly before expiry. It uses existing PyJWT, cryptography, and httpx dependencies. Only FCM `UNREGISTERED` responses disable tokens; permission, authentication, payload, and temporary provider failures do not. Provider error bodies, credentials, and device tokens are not logged. Delivery remains best effort; there is no new durable push retry queue, and in-app notifications remain available if a send fails.

Reference: [Firebase HTTP v1 authorization](https://firebase.google.com/docs/cloud-messaging/send/v1-api), [Google service-account OAuth flow](https://developers.google.com/identity/protocols/oauth2/service-account), and [FCM error codes](https://firebase.google.com/docs/cloud-messaging/error-codes).

## Files changed

- `.gitignore`: ignore Firebase Android JSON, common service-account JSON filenames, and local application-default credentials anywhere in the repository; credential directories and key files are also ignored.
- `scripts/configure-android-firebase.js`: stage external-file or base64-secret input, validate the registered project/package, enforce mode 600, and hide contents/errors containing JSON.
- `package.json`: run Firebase staging in `android:sync`.
- `.github/workflows/android-build.yml`: consume the encrypted secret, require Firebase by default, allow explicit development opt-out, and clean up staged JSON even on failure.
- `android/app/build.gradle`: direct messaging dependency for the guarded plugin, conditional Google Services plugin, and required-config failure.
- `android/app/src/main/AndroidManifest.xml`: default FCM notification channel.
- `android/app/src/main/java/com/schoolhop/app/SchoolHopPushNotificationsPlugin.java`: exported guarded methods, cold-launch tap handling, and awaited token deletion.
- `app/static/app.js`: refresh/cleanup serialization, resume retry, Sign Out cleanup, private channel, foreground refresh, and trusted navigation.
- `app/main.py`: HTTP v1 sender, generic previews, route fallbacks, token-error handling, and owner-scoped unregister endpoint.
- `tests/frontend/native-push.test.mjs`: executable regression tests for config validation, missing config, denied permission, refresh, cleanup races/failures, account switching, and routes.
- `tests/test_fcm.py`: HTTP v1 payload/privacy, OAuth caching, missing credentials, registration/unregister ownership, error handling, and activity routes.
- `android/app/src/test/java/com/schoolhop/app/SchoolHopFirebaseConfigTest.java`: Capacitor export regression alongside missing-config tests.
- `README.md`, `docs/ANDROID.md`, `docs/ANDROID_FCM.md`: setup and audit documentation.

## Validation

| Check | Result |
| --- | --- |
| `npm test` | Passed: 28 tests |
| `npm run lint` | Passed |
| `npm run typecheck` | Passed |
| `npm run android:sync` without Firebase | Passed |
| `ANDROID_GOOGLE_SERVICES_FILE=... ANDROID_REQUIRE_FIREBASE=true npm run android:sync` | Passed with existing external config |
| Gradle `assembleDebug :app:testDebugUnitTest :app:assembleDebugAndroidTest` without Firebase | Passed |
| Same Gradle tasks with `ANDROID_REQUIRE_FIREBASE=true` and staged Firebase config | Passed; four app JVM tests, including three Firebase/plugin tests |
| Python `unittest discover -s tests -v`, isolated test environment | Passed: 79 tests |
| `npm run test:layout` | Passed: seven browser tests with API fixtures |
| `git diff --check` | Passed |

The first Gradle attempt found the missing direct Firebase compile dependency introduced by awaited token deletion. That dependency was added and both final build modes passed. Instrumentation APK compilation passed; instrumentation/device execution and real FCM delivery have not been performed. No Samsung was attached. The Firebase-enabled APK is `android/app/build/outputs/apk/debug/app-debug.apk`.

## Configured GitHub Actions secret

The following repository secret is configured for a Firebase-enabled debug APK:

| Name | Source | Purpose |
| --- | --- | --- |
| `ANDROID_GOOGLE_SERVICES_JSON_BASE64` | Base64 of `/home/spela/google_android/google-services.json` | Client Firebase resources for `schoolhop-1c00d`, `com.schoolhop.app` |

GitHub encrypts the secret; base64 is only the transport encoding. The user confirmed creation, and `gh secret list --repo shourish17/SchoolHop` verified its name and update timestamp without retrieving its value. The file's contents must not be pasted into logs or committed. The original mode-600 file remains outside the repository. No repository secret was created or replaced during this continuation. The Android Build job consumes the repository secret directly and has no GitHub environment binding.

No Android signing secrets are needed for the debug APK. Existing release-signing and Play-upload workflows/secrets were not changed, used, or created.

## Build a Firebase-enabled APK

With JDK 21 and Android SDK platform 36/build-tools 35.0.0 configured:

```bash
git switch feature/schoolhop-my-carpool-ui
npm ci
ANDROID_GOOGLE_SERVICES_FILE=/home/spela/google_android/google-services.json \
ANDROID_REQUIRE_FIREBASE=true npm run android:sync
cd android
ANDROID_REQUIRE_FIREBASE=true ./gradlew --no-daemon assembleDebug :app:testDebugUnitTest
```

The build stages a mode-600 ignored copy at `android/app/google-services.json`. The original file is never moved or edited. Remove the staged copy after the build if desired; subsequent configured builds must stage it again. Generated Firebase resources are intentionally compiled into the APK. No server service-account key belongs in the APK.

For a development build without push, remove only the staged `android/app/google-services.json`, unset Firebase input/requirement variables, run `npm run android:sync`, and build normally. The checked-in `.example` file is a placeholder and must not be copied as real configuration.

For GitHub Actions, run **Android Build** on the feature branch with `require_firebase=true`:

```bash
gh workflow run android-build.yml --repo shourish17/SchoolHop \
  --ref feature/schoolhop-my-carpool-ui -f require_firebase=true
```

The workflow's actual inputs are `require_firebase`, `version_name`, and `version_code`; there is no separate checkout-ref input. `actions/checkout` uses the dispatch ref by default. Verify the resulting run's `headSha` matches the pushed feature-branch commit and download `schoolhop-android-debug-<commit SHA>`. Do not run Android Play Internal Testing Upload.

## Backend activation — separate approval required

The client JSON alone cannot authorize backend sends. Before live delivery tests, separately approve:

1. Verify the Firebase Cloud Messaging API is enabled for `schoolhop-1c00d`; enable it only if authorized.
2. Select an existing service account with FCM send permission in that project. Obtain a server credential through the administrator's approved process; creating a service account, IAM grant, or private key is an external configuration change requiring approval.
3. Store that credential outside Git and mount it read-only into the backend runtime. Set `FCM_SERVICE_ACCOUNT_PATH` to the runtime path and `FCM_PROJECT_ID=schoolhop-1c00d`. This is server configuration, not a GitHub Android-build secret. `FCM_SERVER_KEY` is no longer used by the code.
4. Deploy the reviewed backend code and settings through a separately authorized deployment. The new unregister endpoint and private previews require that backend version; a new APK alone does not update production. With the old backend, device cleanup can fail and Sign Out will ask for a retry.

None of these external actions has been performed. A later production rollout of the shared frontend must also advance the PWA service-worker/app asset version through the release process; this task builds the Android bundle directly and does not deploy the browser app.

## Physical Samsung Galaxy test checklist

Use test accounts and synthetic child/trip data on an approved test backend. The APK built here uses the repository's default production API URL; for staging, rebuild with `SCHOOLHOP_CAPACITOR_API_BASE_URL=https://your-staging-host.example` alongside the Firebase variables. Do not create production activity as part of this task.

1. Connect the Galaxy with USB debugging enabled and a functioning Google Play services installation. Verify it appears in `adb devices`, then install with `adb install -r android/app/build/outputs/apk/debug/app-debug.apk`. A differently signed existing install may need a separately approved uninstall; no signing keys were changed here.
2. Start the app and sign in. Grant Android 13+ notification permission. Check Samsung Settings → Apps → SchoolHop → Notifications and the SchoolHop activity category; enable category controls if One UI hides them. Confirm one enabled FCM registration through authorized test tooling, without copying its token into logs or screenshots.
3. Deny permission on a fresh test install and verify login, dashboard, and Sign Out remain safe. Grant it in Settings and resume the app; verify registration retries without another prompt.
4. Trigger assignments, driver accept/decline, swap requests/accept/decline, delays, pickup/dropoff/absence, and a registered-user group invitation. Check intended recipients receive one notification and unrelated users receive none.
5. Repeat delivery with the app foregrounded, backgrounded, and closed normally, with the phone locked. Check previews contain no child names, addresses, notes, or contacts. Android force-stop suppresses delivery until the app is reopened; distinguish that from a normal closed app.
6. Tap every event type from a running app and a cold launch. Verify selected trip/roster, replacement-driver swap controls, and invitation acceptance. Verify a pending tap is restored after login and old swap links cannot decide completed requests.
7. Force token renewal using approved device/test tooling (for example a test reinstall), then sign in again. Verify a new token registers and the old token is disabled or pruned on FCM `UNREGISTERED`. Exercise a refresh while the app is running to verify listener cleanup.
8. Sign Out online; verify the account's recorded device registrations are disabled and delivered notifications clear. Send another event for that account and confirm this Galaxy receives none. Sign in as a second test account and verify no notifications cross accounts. Test offline Sign Out with a registered token: it must retain the session and allow retry when connected.
9. Check delivery under Samsung battery/background restrictions and Do Not Disturb; adjust test-device settings only with the owner's permission. Capture results without child information, device tokens, or Firebase credential values.

Real delivery, permission UI, Samsung power management, and cold-tap behavior remain device acceptance work; passing mocked tests and APK builds does not establish end-to-end delivery.
