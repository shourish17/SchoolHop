# SchoolHop Android

SchoolHop Android is a Capacitor shell around the maintained root PWA frontend. It reuses the existing FastAPI API, auth, carpool, notification, and GPS contracts. Do not use the archived React Native prototype in `mobile/schoolhop-mobile` for current builds.

## Application

- App name: `SchoolHop`
- Application ID: `com.schoolhop.app`
- Backend: `https://schoolhop.shourish.com`
- Capacitor web output: `build/capacitor`
- Android project: `android/`
- Native location plugin: `android/app/src/main/java/com/schoolhop/app/SchoolHopLocationPlugin.java`
- Foreground tracking service: `android/app/src/main/java/com/schoolhop/app/SchoolHopLocationService.java`

Confirm `com.schoolhop.app` before creating Google Play upload signing credentials. Google Play package names are effectively permanent after first upload.

## Prerequisites

- Node.js 20+
- npm 10+
- JDK 21
- Android Studio Rabbit or newer
- Android SDK platform 36 for Google Play readiness
- Android SDK build tools 35.0.0 or newer
- Android Gradle Plugin 8.9.1, matching API 36 support guidance
- `ANDROID_HOME` or Android Studio SDK path configured, for example `/home/testuser/Android/Sdk`

Install dependencies from the repository root:

```bash
npm ci
```

## Local Build

Prepare web assets and sync Android:

```bash
npm run android:sync
```

Build a debug APK:

```bash
cd android
./gradlew assembleDebug
```

The APK is written to:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

Install on a connected emulator or device:

```bash
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

Open in Android Studio:

```bash
npm run android:open
```

## Configuration

`scripts/build-capacitor-web.js` injects:

```js
window.SCHOOLHOP_NATIVE_CONFIG = {
  apiBaseUrl: "https://schoolhop.shourish.com",
  appVersion: "..."
}
```

Override for non-production local testing only:

```bash
SCHOOLHOP_CAPACITOR_API_BASE_URL=https://your-staging-host.example npm run android:sync
```

The Android native tracking plugin rejects non-HTTPS API base URLs. Production traffic must remain HTTPS-only.

## System bars and header

`MainActivity` enables edge-to-edge with AndroidX on supported Android versions,
including mandatory edge-to-edge on Android 15/API 35 and Android 16/API 36.
The native content container applies the current `systemBars` and `displayCutout`
insets to all four sides. This keeps the entire WebView, including scrolling
content, outside status bars, camera cutouts, gesture navigation and button navigation.
Insets are reapplied on rotation, window resizing and display scaling.

Capacitor's `android.adjustMarginsForEdgeToEdge` is explicitly `disable` because
`MainActivity` owns these insets. The activity zeroes only the handled inset types
before delivering them to the WebView, preventing duplicate CSS safe-area padding
on newer WebViews while preserving keyboard inset updates. Do not add Android
status-bar heights or a second CSS top inset. The activity uses `adjustResize`
for keyboard compatibility with older WebViews. iOS keeps `contentInset: automatic`
and the frontend's existing CSS safe-area rules.

The header retains the existing Sign Out control and Alerts tab. Header text can
shrink or wrap without pushing Sign Out offscreen; its tap target is at least 48px
high. A `ResizeObserver` keeps sticky navigation below the actual header height,
including when the install button appears or text scaling changes.

Run the isolated browser regressions with:

```bash
npx playwright install chromium
npm run test:layout
```

These tests use the real frontend with API fixtures and no live backend. They
cover portrait/landscape Pixel 7 viewport sizes, small phones, tablets, desktop,
larger text/display settings, sticky scrolling, install controls, and touch Sign
Out online and offline. Sign Out removes the stored bearer token and remembered
trip/driver selection, stops tracking/polling, and returns to the login form.
There is no server logout endpoint in this bearer-token flow. Browser hit testing
verifies the tap target separately from session cleanup; native system-bar behavior
must also be checked on an emulator/device.

## GPS Tracking

Android tracking starts only after the existing frontend successfully starts an authorised assigned-driver trip through `POST /api/trips/{trip_id}/start`. The native plugin then starts a location foreground service and posts location updates to `POST /api/trips/{trip_id}/locations`.

Implemented Android behavior:

- requests foreground location permission before tracking;
- declares background location separately for locked-screen/background tracking review;
- uses a foreground service with `foregroundServiceType="location"`;
- shows a persistent notification while tracking is active;
- throttles uploads to roughly every four seconds and uses a 25 m distance filter;
- persists active trip tracking state with encrypted Android storage for service recovery;
- stops when the frontend calls `stopTripTracking`, when logout clears local state, or when the API rejects updates with 401, 403, 404, or 409;
- does not start tracking for parents or children unless they are the authorised assigned driver who started the trip.

Google Play declarations likely required:

- Background location declaration, because active driver tracking can continue while the app is backgrounded or the screen is locked.
- Foreground service declaration for location.
- Data Safety disclosure for precise location, account identifiers, child profile data, contact details, addresses, and app activity notifications.
- Prominent in-app disclosure and privacy policy language explaining that live location is collected only during an active assigned-driver trip.
- Child-safety/family-related declarations as applicable because the product processes child carpool information, even though the app is for parents/drivers.

Do not request unrestricted background tracking. Tracking must stay tied to an active trip and stop when the backend says the trip is no longer active or authorised.

## Push Notifications

The app uses Capacitor Push Notifications with Firebase project `schoolhop-1c00d` and Android package `com.schoolhop.app`. The original configuration is stored outside Git at `/home/spela/google_android/google-services.json`.

Build with the existing Firebase configuration:

```bash
ANDROID_GOOGLE_SERVICES_FILE=/home/spela/google_android/google-services.json \
ANDROID_REQUIRE_FIREBASE=true npm run android:sync
cd android
ANDROID_REQUIRE_FIREBASE=true ./gradlew --no-daemon assembleDebug
```

The staging script validates the project/package and creates an ignored, mode-600 copy at `android/app/google-services.json`. It never logs its contents. Keep the original outside the repository. The Google Services Gradle plugin generates Firebase resources during the Gradle build.

GitHub Actions reads the encrypted `ANDROID_GOOGLE_SERVICES_JSON_BASE64` secret. Android Build requires it by default; its `require_firebase` input can be disabled for development builds without push. Missing configuration remains safe: both the frontend and guarded native plugin skip FCM registration, preventing the previous `Default FirebaseApp is not initialized` crash. Invalid supplied configuration fails validation rather than silently disabling push.

The backend now uses FCM HTTP v1 with server-only `FCM_SERVICE_ACCOUNT_PATH` and `FCM_PROJECT_ID=schoolhop-1c00d`. The old `FCM_SERVER_KEY` legacy sender is retired. The Android client JSON is **not** a backend service account credential. No production configuration has been changed.

Registration and refresh use authenticated `POST /api/mobile/devices`; Sign Out disables recorded tokens through authenticated `DELETE /api/mobile/devices` before clearing authentication and deleting the native token. If registered-device removal fails, Sign Out stays retryable and explains that a connection is needed. Builds that never registered a device can sign out offline.

Android uses the private `schoolhop_activity` notification channel. Push previews contain only a generic SchoolHop update; full details stay in the authenticated inbox. Taps use trusted `action_url` routes for trips, rosters, swaps, and invitations, including cold app launches and navigation pending login.

See [Android FCM integration report](ANDROID_FCM.md) for the audit, configured build-secret details, validation results, and Samsung Galaxy test checklist.

## Signing

Use Google Play App Signing with a dedicated Android upload key. Do not reuse Apple credentials and do not commit keystores.

Create an upload keystore locally:

```bash
keytool -genkeypair \
  -v \
  -storetype JKS \
  -keystore schoolhop-upload-keystore.jks \
  -alias schoolhop-upload \
  -keyalg RSA \
  -keysize 4096 \
  -validity 10000
```

Back up the keystore and passwords in a secure password manager. Losing the upload key requires Play Console key reset/recovery.

GitHub Actions secrets:

- `ANDROID_KEYSTORE_BASE64`
- `ANDROID_KEYSTORE_PASSWORD`
- `ANDROID_KEY_ALIAS`
- `ANDROID_KEY_PASSWORD`

Encode the keystore:

```bash
base64 -w 0 schoolhop-upload-keystore.jks
```

Local signed bundle example:

```bash
export ANDROID_KEYSTORE_PATH=/secure/path/schoolhop-upload-keystore.jks
export ANDROID_KEYSTORE_PASSWORD=...
export ANDROID_KEY_ALIAS=schoolhop-upload
export ANDROID_KEY_PASSWORD=...
export ANDROID_VERSION_NAME=1.0.25
export ANDROID_VERSION_CODE=25
cd android
./gradlew bundleRelease
```

The AAB is written to:

```text
android/app/build/outputs/bundle/release/app-release.aab
```

Version codes must be unique and increasing for every Google Play upload.

## GitHub Actions

`Android Build` is defined in `.github/workflows/android-build.yml`.

It is manual-only and:

- checks out the selected revision;
- installs Node 20 and JDK 21;
- installs Android SDK platform 36;
- runs frontend tests, typecheck, lint, backend regression tests, and Python compile checks;
- builds Capacitor assets;
- syncs Android;
- validates manifest permissions;
- builds a debug APK;
- builds and uploads a signed release AAB only when Android signing secrets are configured;
- reports commit SHA, application ID, version name, version code, artifact names, and signing status.

`Android Play Internal Testing Upload` is defined in `.github/workflows/android-play-upload.yml`.

It is manual-only and expects an existing signed AAB artifact from `Android Build`. It validates the package name and version metadata before uploading to the Internal Testing track with draft status. It requires:

- verified Google Play Developer account;
- app created in Play Console with package `com.schoolhop.app`;
- Google Play App Signing enabled;
- first manual upload completed if Play Console requires it;
- Play Developer API enabled;
- service account granted only the app/release permissions it needs;
- `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` stored as a GitHub Actions secret.

Prefer Workload Identity Federation or another short-lived credential path if the repository/account setup supports it. Do not commit service-account JSON.

## Validation Checklist

Run locally before pushing Android changes:

```bash
npm test
npm run typecheck
npm run lint
python -m unittest discover -s tests -v
python -m py_compile app/main.py app/seed.py
npm run android:sync
cd android && ./gradlew assembleDebug
git diff --check
```

If signing credentials are available, also run:

```bash
cd android && ./gradlew bundleRelease
```

Native device/emulator tests to run before Google Play testing:

- login and logout;
- registration and forgot password;
- create/join group and invitations;
- child profile and driver approval flow;
- driver assignment accept/decline;
- trip start and end;
- driver GPS foreground update;
- driver GPS while app is backgrounded and screen is locked;
- Android foreground-service notification visibility;
- notification delivery and token refresh;
- notification deep links from foreground, background, and terminated app states;
- swap request and delay workflows;
- child pickup/drop-off/handover confirmation;
- logout while tracking is active.

Do not claim background GPS or push delivery works until tested on a real Android device with Firebase configured.

## Troubleshooting

- Missing `google-services.json`: debug builds can still run, but Android FCM registration is skipped and no Android device token is sent to the backend.
- Firebase push still unavailable after adding `google-services.json`: run `npm run android:sync`, confirm the file is at `android/app/google-services.json`, and rebuild so `google_app_id` and `gcm_defaultSenderId` are generated into the APK.
- `platforms;android-36` missing: install it with Android Studio SDK Manager or `sdkmanager`.
- Background location prompt unavailable: Android requires background location to be requested separately and may route users to Settings.
- Release AAB skipped in Actions: configure all four Android signing secrets.
- Play upload rejected: confirm package name, version code increase, Play App Signing status, and API service account permissions.

## Rollback

Android changes are isolated to the root Capacitor Android project, shared native bridge code, docs, tests, and Android-specific workflows. To roll back an Android build issue, revert the Android-specific commit before creating another workflow artifact. Do not change the iOS TestFlight workflow or production backend deployment as part of Android rollback.
