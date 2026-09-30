# SchoolHop Mobile

Archived React Native prototype for future native-app planning. The maintained SchoolHop frontend is currently the PWA served from `app/static`, with active npm scripts and dependencies in the repository-root `package.json`.

Do not install or run this prototype for current SchoolHop validation. It intentionally has no npm dependencies because the current product is a PWA, not React Native. Run these commands from the repository root instead:

```bash
npm install
npm test
npm run typecheck
npm run lint
```

## Stack Recommendation

Use React Native for this milestone.

Both Flutter and React Native can call the existing FastAPI JSON API. React Native is the better fit here because SchoolHop already has a lightweight JavaScript browser MVP, the app needs little custom native UI, and reliable locked-screen GPS is best handled by mature native-backed modules such as `@transistorsoft/react-native-background-geolocation`. That module supports iOS significant/background location behavior, Android foreground services, headless/background execution, motion changes, and stop-on-trip-end control. Flutter can do this too, but it does not offer a meaningful backend integration advantage for this codebase.

## Background GPS License Check

`@transistorsoft/react-native-background-geolocation` wraps Transistorsoft's proprietary native SDK. The wrapper package is open source, but release/App Store builds require a commercial SDK license.

Current vendor terms to confirm before committing to this dependency:

- Debug builds work without a license.
- Release builds and App Store distribution require a paid license.
- A free 30-day trial key is available for release-build evaluation only, not production distribution.
- One purchase covers iOS and Android for one app identifier.
- The starter plan is currently listed at $399 for one application key, perpetual license, GitHub support, and one year of updates.

Budget decision: keep this library only if that commercial license is acceptable for SchoolHop. Otherwise replace it before production hardening with a native in-house location service or a different background-location provider.

## Implemented Flow

- Driver signs in with the existing bearer-token API.
- Driver sees only trips returned by `GET /api/groups/{group_id}/trips`; the server enforces assigned-driver and trip visibility permissions.
- Driver starts a scheduled trip with `POST /api/trips/{trip_id}/start`.
- Native background GPS starts only after the API accepts the trip start.
- GPS updates are sent to `POST /api/trips/{trip_id}/locations` while the phone is locked.
- GPS stops locally when the driver ends the trip, or when the API rejects updates because the trip is no longer active or the safety timeout elapsed.
- Driver can mark each child `picked_up`, `dropped_off`, or `absent`.
- Parent trip screen shows authorized trips, live map position, freshness/staleness, trip status, and child pickup/drop-off updates.
- Push token registration uses `POST /api/mobile/devices` with APNs tokens on iOS and FCM tokens on Android.

## Local Setup

```bash
cd mobile/schoolhop-mobile
cp .env.example .env
npm install
```

Set `SCHOOLHOP_API_BASE_URL` to a device-reachable URL:

- iOS simulator to host: `http://127.0.0.1:8088`
- Android emulator to host: `http://10.0.2.2:8088`
- Physical devices: LAN or tunnel URL that reaches the SchoolHop API

## Required Apple Credentials

- Apple Developer Team ID: `APNS_TEAM_ID`
- APNs Auth Key ID: `APNS_KEY_ID`
- APNs `.p8` private key for this app: `APNS_PRIVATE_KEY` or `APNS_PRIVATE_KEY_PATH`
- Bundle ID: `APNS_BUNDLE_ID=com.schoolhop.mobile`
- Push Notifications capability enabled for `com.schoolhop.mobile`
- Background Modes enabled:
  - Location updates
  - Remote notifications
  - Background fetch
- `ios/SchoolHopMobile/GoogleService-Info.plist`, copied from the SchoolHop Firebase iOS app if Firebase Messaging remains installed for permission/token helpers

Use `APNS_USE_SANDBOX=true` for debug/TestFlight development builds and `false` for production APNs.

## Required Google Credentials

- Firebase project dedicated to SchoolHop, not Family AI
- Android app package: `com.schoolhop.mobile`
- `android/app/google-services.json`, copied from the SchoolHop Firebase Android app
- Server key or equivalent sender credential in `FCM_SERVER_KEY`
- Google Maps Android API key for `SCHOOLHOP_ANDROID_MAPS_API_KEY`
- Google Maps SDK enabled for Android and iOS if production maps are required on both platforms

## Locked-Screen Real Device Test

Run this on real iOS and Android phones. Simulators are not enough for reliable background GPS validation.

1. Start the SchoolHop API locally or in a non-live test environment.
2. Seed data with `docker compose exec api python -m app.seed`.
3. Install the app on two devices, or one driver device plus one parent device.
4. Sign in as the assigned driver, for example `ava@example.com`.
5. Sign in as a parent authorized for the trip on the second device, for example `ben@example.com`.
6. On the driver device, grant precise location and always/background location when prompted.
7. Start the scheduled trip from the Driver tab.
8. Lock the driver phone and walk or drive for at least 10 minutes.
9. Confirm the parent screen updates the map and freshness indicator while the driver phone stays locked.
10. Put the driver app in the background, keep the phone locked, and verify Android shows the foreground-service notification and iOS shows the background location indicator when applicable.
11. Unlock the driver phone and end the trip.
12. Confirm the parent screen transitions away from active sharing and no new `trip_locations` rows are written.
13. Repeat with airplane-mode interruption and restored network to confirm queued/native retries meet product expectations.

## Notes

- The backend remains authoritative. Attempts to start trips, send GPS, or record handovers without trip permission are rejected by the existing API.
- Push delivery no-ops until APNs/FCM credentials are configured.
- Do not reuse Family AI Firebase, APNs, bundle IDs, package names, or signing credentials.
