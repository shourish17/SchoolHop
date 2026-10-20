# Android locked-screen GPS review

Implemented locally on `feature/schoolhop-my-carpool-ui`, based on
`6a8240962ce308785867c23e01524a8601fdf585`. The Android changes are prepared for a reviewed feature-branch commit and debug APK build. The backend is unchanged.
No production data, service, Firebase setting, signing key or iOS native code was
changed. A new Android APK is required. No backend change or API deployment is needed
for locked-screen tracking while the trip is `started`. A separate existing backend
delay-state limitation is documented below and is not resolved by this Android fix.

## Findings

The previous native service already used Fused Location Provider and directly
authenticated `POST /api/trips/{trip_id}/locations` requests. Android GPS collection
did not depend on JavaScript timers. Merely moving the upload out of the WebView
would therefore not have addressed the confirmed defects.

| Confirmed defect | Resulting risk | Local fix |
| --- | --- | --- |
| 25 m minimum movement, no fresh stationary updates | Server's 240-second age threshold can expire while parked or moving slowly | Fresh native fixes requested every 10 seconds, without a distance gate; no historical initial fix/batching |
| Throttled queued fix had no drain timer | Last fix could remain queued indefinitely when no new callback arrived | Native monotonic scheduler drains the latest queued sample |
| No scheduled network recovery/backoff | Failed upload was abandoned until another GPS callback; failures could repeatedly burst | Latest-sample queue; bounded 5/10/20/30-second backoff; revalidation on network availability |
| Queue and late completion callbacks survived stop/trip changes | Old queued data or results could affect a later session | Generation checks, immutable per-request session, cancellation and queue clearing |
| Trip/auth checks depended on a future GPS POST | Stationary or offline service could retain collection after a remote trip end | Read-only active-driver validation every 30 seconds; 120-second authorization lease, then collection pauses |
| Sticky recovery trusted stored trip state before contacting server | Restart could collect for an ended trip; while-in-use permission alone is insufficient for a new background service | Every recovery validates first; Android 11+ background recovery requires existing background permission; otherwise foreground user action is required |
| Permission alias required both precise and approximate permission | Android 12 approximate-only selection could be rejected | Accept fine OR coarse, while requesting the pair together |
| Plugin start did not check visible activity/catch background FGS rejection | Lock during the trip-start HTTP request could leave a started trip without GPS | Require resumed activity before requesting startup, handle Android rejection, resolve only after server validation and subscription readiness |
| Stop operation cold-started a foreground service | Background stop could itself violate Android 12 restrictions or a foreground-promotion deadline | Clear saved intent and stop the existing service directly |
| WebView reload lost its native tracking flag | Sign Out could miss the surviving native service | Android always requests native stop; logout waits for completion before clearing its session |
| Android manual GPS used an independent WebView upload | Parallel native/manual uploads could duplicate the same fix | Android manual button requests a fresh native fix through the same queue |
| Capacitor's default debug bridge logs include method arguments | Tracking token and HTTP payloads could enter generic bridge logs | Set only `android.loggingBehavior` to `none`; explicit sanitized GPS diagnostics remain available |

These are source-level and automated-test findings. No live Galaxy trace was
provided or collected. The exact reason for any particular historical Samsung
screen-lock incident remains unconfirmed. Samsung sleeping-app policies, deep Doze,
Battery Saver, GPS signal loss, disabled location, process death and force-stop
remain possible independent causes.

One additional **confirmed backend contract defect** remains: `delay_trip` allows
an already started trip to transition to `delayed`, but `post_location` only accepts
`started` and returns 409 otherwise. Thus reporting a delay after starting can stop
GPS on both existing Android and iOS clients, regardless of screen lock. The new
Android service deliberately follows that existing server contract; it does not
silently restart a trip or bypass the rejection. Supporting continuous GPS through
that delay transition requires a separately reviewed backend state-machine fix.
No backend code or production configuration was changed here.

## Runtime behavior and privacy bounds

1. The driver follows the existing Accept / Start Trip workflow while SchoolHop is
   visible. A delayed trip-start request followed by lock cannot silently start a
   new location service in the background; reopen and tap Start/resume GPS instead.
2. The native service immediately promotes itself with type `location`, using the
   existing low-importance channel `schoolhop_active_trip_location` and an ongoing
   notification. A **Stop sharing** action stops GPS without ending the trip.
3. Before requesting GPS, the service reads the existing trip endpoint using the
   current bearer credential. It requires `status=started`, matching driver identity,
   no `ended_at`, and a future server `safety_timeout_at`. The server verifies the
   bearer signature; locally parsed JWT subject/expiry are only additional bounds.
4. New fixes are requested approximately every 10 seconds. Uploads are serialized,
   at least four seconds apart, and duplicate capture timestamps are suppressed.
   Only the newest pending sample is kept; no location history is persisted.
5. A monotonic fix age greater than 30 seconds is discarded, including after network
   loss. An ambiguously accepted fix is never replayed. Subsequent fresh fixes can
   be uploaded after backoff. Native manual GPS shares this exact path.
6. The service performs a read-only trip check every 30 seconds. A rejection or
   ended/reassigned trip stops it. If validation is unavailable for 120 seconds,
   collection pauses and pending locations are discarded. A valid new response can
   resume collection while the existing service remains alive. Initial startup is
   bounded; the plugin cancels unready startup after 30 seconds.
7. Known JWT expiry or the server trip safety deadline stops the service even offline.
   Remote revocation cannot be learned immediately without connectivity; the
   120-second lease bounds collection while its state is unknown. No stale point
   is uploaded simply to make the server appear fresh.
8. A bounded partial wake lock covers each native network operation and callback,
   with a 20-second safety timeout. There is no continuous wake lock, alarm loop,
   boot receiver or automatic battery-optimization exemption.
9. HTTPS is mandatory; user-info/query/fragment API URLs are rejected. Redirects
   are disabled. Tokens remain in memory and existing encrypted Android preferences;
   they are not sent in URLs or diagnostics. Explicit stop, logout and server rejection
   clear the saved credentials and tracking intent. A submitted HTTP request cannot
   be recalled, but its late result cannot start another upload/session.
10. Removing the task card does not deliberately stop the service. System recovery
    is allowed only with the necessary existing background permission and fresh
    server validation. Force-stop and reboot do not trigger tracking independently.

The server's **240-second stale threshold is unchanged**. Ten-second requested fixes,
freshness filtering and serialized uploads target a substantially smaller online age.
Android scheduling, approximate location throttling, signal loss and network loss
can still exceed 240 seconds; the server must truthfully show GPS stale in that case.

## Permissions and OS behavior

- `ACCESS_FINE_LOCATION` or `ACCESS_COARSE_LOCATION`: runtime foreground permission.
  Android 12 approximate location is accepted, with reduced precision/cadence.
- `FOREGROUND_SERVICE` and the declared `foregroundServiceType="location"`:
  foreground tracking service. `FOREGROUND_SERVICE_LOCATION` remains declared for
  newer Android targets.
- Existing `ACCESS_BACKGROUND_LOCATION`: not requested by this plugin. It is not
  required merely to lock the screen after starting the visible location service;
  it is required by this implementation for automatic background recovery on
  Android 11+. Without it, return to the app to resume after process death.
- Existing `INTERNET`, `WAKE_LOCK`, and added normal `ACCESS_NETWORK_STATE` support
  direct uploads, bounded CPU wake time and recovery detection.
- Android 12 has no `POST_NOTIFICATIONS` runtime prompt. Existing newer-version
  notification handling remains unchanged. User channel/app settings can hide the
  notification; the app does not override them.

Android documents continued foreground-location access after display-off when the
foreground service was started while visible:
[location permissions](https://developer.android.com/develop/sensors-and-location/location/permissions),
[background service-start restrictions](https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start).
Doze can suspend network/CPU access even when application logic is correct:
[Doze and App Standby](https://developer.android.com/training/monitoring-device-state/doze-standby).
The provider can hold a wake lock while delivering a location; the app's bounded
lock covers its subsequent asynchronous work:
[Fused Location Provider](https://developers.google.com/android/reference/com/google/android/gms/location/FusedLocationProviderClient).

## Changed files

- `android/app/src/main/java/com/schoolhop/app/SchoolHopLocationTracker.java`: native scheduler/state machine.
- `android/app/src/main/java/com/schoolhop/app/SchoolHopLocationService.java`: GPS/network/lifecycle adapter.
- `android/app/src/main/java/com/schoolhop/app/SchoolHopLocationPlugin.java`: foreground startup, permission/readiness handling, stop/manual/diagnostic bridge methods.
- `android/app/src/debug/java/com/schoolhop/app/SchoolHopLocationDiagnostics.java`: safe debug diagnostics.
- `android/app/src/release/java/com/schoolhop/app/SchoolHopLocationDiagnostics.java`: no-op release facade.
- `android/app/src/main/AndroidManifest.xml`: network-state permission and explicit task-lifecycle behavior.
- `android/app/src/main/res/values/strings.xml`: Stop sharing label.
- `android/app/build.gradle`: Robolectric test dependency, Android test resources and Java API desugaring for the existing minimum SDK.
- `capacitor.config.json`: Android-only payload logging disabled; iOS/Firebase settings unchanged.
- `app/static/app.js`: Android manual GPS and native logout cleanup; existing iOS/browser paths retained.
- `android/app/src/test/java/com/schoolhop/app/SchoolHopLocationTrackerTest.java`.
- `android/app/src/test/java/com/schoolhop/app/SchoolHopLocationServiceTest.java`.
- `android/app/src/test/java/com/schoolhop/app/SchoolHopLocationPluginTest.java`.
- `tests/frontend/native-location.test.mjs` and existing `tests/frontend/pwa.test.mjs`.
- `docs/ANDROID.md` and this report.

Pre-existing FCM readiness/deployment documentation and the pending push-test plan
were preserved; they are separate working-tree changes from the preceding task.

## Automated validation

| Final check | Result |
| --- | --- |
| Android debug JVM tests | **42 passed**, no skips |
| Android release JVM tests | **42 passed**, same behavioral suite, no skips |
| Frontend tests | **36 passed**, including seven native-location regressions |
| Chromium layout tests | **7 passed** |
| Backend regressions with network connections blocked | **121 passed**; seven unchanged, opt-in disposable PostgreSQL concurrency tests skipped |
| ESLint / TypeScript | Passed |
| Debug and release Android lint | **0 errors**; 12 debug / 14 release warnings in existing Firebase resource lookup, backup and visual-resource categories |
| `assembleDebug` / `assembleRelease` | Passed; release APK is unsigned |
| `assembleDebugAndroidTest` | Passed; device instrumentation not executed |
| APK asset/config verification | Bundled app.js matches reviewed source; Android bridge logging disabled; GPS diagnostic tag present only in debug DEX |
| Protected-source review | No iOS, workflow, signing, Firebase push implementation, backend Python, migration or production Compose changes |
| Production read-only identity check | API and PostgreSQL remain healthy, with unchanged image IDs/start times |
| Whitespace checks | Passed |

Android lint's concrete-service type false positive from JVM service subclasses is
narrowly suppressed at foreground promotion. A manifest regression test separately
requires the real service to be private, type `location`, with `stopWithTask=false`.
Synchronous secure-state clears are intentional and narrowly annotated: a pending
asynchronous clear must not allow process recovery with a stopped session's token.

Local review artifacts (not installed or uploaded):

- Debug APK: `android/app/build/outputs/apk/debug/app-debug.apk`
  (SHA-256 `79387c8b1d19b18278d359f21847f33ae6e3085ba03fdf7e3577a8b1fda99687`).
- Unsigned release APK: `android/app/build/outputs/apk/release/app-release-unsigned.apk`
  (SHA-256 `7400df89539b1f6f4e3ed1dd1b1584cd29f435bfcedab940a0d1fcb9d4556542`).
- JVM reports: `android/app/build/reports/tests/testDebugUnitTest/` and `testReleaseUnitTest/`.
- Lint reports: `android/app/build/reports/lint-results-debug.html` and `lint-results-release.html`.

Both local APKs contain the existing production API base URL,
`https://schoolhop.shourish.com`. The user subsequently approved using that backend
for a controlled Samsung Galaxy test; no separate test environment is required.
That approval does not authorize this build task to create, start or alter trips
or rosters, upload GPS, or start a device test. No APK was installed, no real
location was uploaded, and no notifications were sent.

Android tests
use a deterministic native scheduler and Robolectric **Android 12/API 31** framework
shadows with fake GPS subscriptions and fake HTTPS connections. They do not access
production, send push messages or upload actual GPS. A shadowed idle/screen state
does not emulate real Samsung power management or actual Doze enforcement.

Coverage includes foreground promotion/readiness, fine/coarse denial, activity
backgrounding, screen-off/idle updates, authentication headers and redirect refusal,
active-driver validation, network interruption/recovery, latest-fix coalescing,
deduplication, stale/future samples, lease expiry, remote completion, token expiry,
stop/cold-stop, late callbacks, sticky recovery, manifest contract, Capacitor exports,
sanitized diagnostics and generic bridge logging suppression.

## Samsung Galaxy Android 12 acceptance procedure

These are future device-test instructions, not actions performed by this change.
The user approved the existing production backend, `https://schoolhop.shourish.com`,
for controlled Samsung testing. Before any field test, identify the exact safe trip,
driver and observer and obtain approval for the fixture and any record changes.
The current APK build task must stop after providing the artifact and installation
instructions; it does not authorize creating or starting trips or initiating GPS.

Install only an APK signed with the currently installed application's signing key,
with a compatible version code, while no active trip is sharing GPS:

```bash
adb install -r /path/to/reviewed/app-debug.apk
```

**Existing CI signing limitation:** `android-build.yml` uses Android's automatically
generated debug keystore on an ephemeral runner. The reviewed build-7 and build-8
APKs have different signing certificates. Keeping `com.schoolhop.app` and increasing
the version code alone therefore cannot guarantee an in-place update. Reuse the
keystore that signed the installed APK; do not generate a replacement key or
uninstall the app to work around this requirement. No signing configuration or
key was changed by the GPS implementation.

Use JDK 21 and SDK platform 36/build tools 35 as required by the existing project.
If installation reports a signing mismatch or downgrade, stop; do not uninstall
the real app, change keys or force a downgrade. A separate approved test device/profile
may be needed. Do not install over an app that is currently sharing a real trip.

1. Confirm Android 12, the reviewed APK and the approved production API host. Sign in as the assigned
   test driver. Grant While using the app location permission, preferably Precise.
   Do not change Samsung battery/channel settings as a first troubleshooting step.
2. Accept and Start the approved test trip with the screen on. Wait for startup to
   succeed and the ongoing **SchoolHop trip tracking active** notification to appear.
   Inspect channel **Active trip location**, ID `schoolhop_active_trip_location`.
3. Collect only sanitized diagnostics (no broad Logcat, HTTP bodies or bearer headers):

   ```bash
   adb logcat -v time -s SchoolHopGpsDebug:I '*:S'
   ```

   The installed debug WebView can also call the read-only method:

   ```js
   await window.Capacitor.Plugins.SchoolHopLocation.getTrackingDiagnostics()
   ```

   Check permission flags, location enabled, tracking/collecting, interactive/idle,
   power saver, battery exemption, validated network, channel state, event/status and
   age fields. No snapshot should include coordinates, trip/user IDs, tokens or URLs.
4. On a separate authenticated test observer, watch the app's GPS age/freshness
   indicator. Lock the Galaxy for **10 minutes**; remain stationary for at least
   **5 minutes**, then walk safely. Never operate the phone while driving. Expect
   `interactive=false` alongside native FIX/UPLOAD/UPLOADED events and HTTP 200s;
   normally GPS age stays far below 240 seconds. Record any online gap above 240.
5. Press Home / switch apps and optionally dismiss the task card during the same
   approved trip. Tracking should continue without a WebView timer. Tap Send GPS
   once after reopening: it should use the native queue, with no separate WebView POST.
6. On the test phone only, interrupt its network for **60 seconds**, then recover.
   Expect retry-wait metadata, no old-point replay and a fresh upload after recovery.
   Repeat a separately approved longer interruption above **120 seconds**: collection
   should pause until active-trip validation succeeds. Server GPS age may exceed
   240 offline; it must not be artificially reset by replayed positions.
7. While locked, end the test trip from its authorised test organiser. With network
   available, the next native validation (about 30 seconds plus request latency), or
   a rejected upload, must stop collection and remove the notification. Observe no
   new GPS requests after any already-submitted request finishes.
8. In separate approved test-trip runs, test **Stop sharing** from the notification,
   normal End Trip, and Sign Out. Stop sharing halts GPS only; it does not end the
   backend trip. Sign Out must wait for native stop. Existing trip responses and
   other users' assignments must not be modified as troubleshooting workarounds.
9. Permission denial should prevent startup. Coarse-only permission should work
   with reduced accuracy. Revoking location permission must stop tracking; Android
   may terminate the process when a permission is revoked. Automated tests cover
   HTTP 401/403 revocation; real-device account revocation requires a separately
   approved test account/environment action, not a JWT rotation.
10. Force-stop is an explicit user stop: expect no automatic restart. Reopen and
    resume the still-active approved trip while visible. Do not use force-stop as
    proof of low-memory sticky recovery. System recovery is separately covered by
    automated tests; device recovery with already-granted background permission
    must validate the trip before any GPS collection. An ended trip must never resume.

If gaps occur, correlate sanitized FIX vs UPLOAD vs UPLOADED events and network/power
flags. No FIX suggests provider/permission/power/signal issues; FIX without successful
uploads suggests network/auth/API behavior. A false battery-exempt flag alone does
not prove the cause. Owner-approved Samsung battery-setting comparisons can follow,
but the app does not request unrestricted exemptions or promise tracking after
force-stop, reboot, deep Doze or OEM termination.

Real-device GPS receipt, battery consumption, actual Doze behavior and Samsung
background reliability remain acceptance work. No end-to-end Galaxy result is claimed.
