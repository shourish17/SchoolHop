# Mobile Background Location Notes

SchoolHop's API accepts and authorizes live GPS updates only while a trip is active:

- `POST /api/trips/{trip_id}/start`
- `POST /api/trips/{trip_id}/locations`
- `GET /api/trips/{trip_id}/location`
- `POST /api/trips/{trip_id}/end`

The web UI includes foreground browser GPS tracking for local MVP testing and PWA pilots. A normal website or PWA should not be treated as reliable locked-screen tracking: iOS can suspend JavaScript timers, `watchPosition`, and network posts when Safari or the installed PWA is backgrounded, the screen locks, battery saver intervenes, or permission changes. While the page remains foregrounded, SchoolHop throttles posted fixes to roughly every four seconds and keeps the latest trailing position.

For production driver tracking, use native iOS/Android shells or a cross-platform app using platform background-location APIs. The iOS Capacitor shell in `ios/App` has `UIBackgroundModes=location`, requests Always permission only after a driver starts an active trip, sets `allowsBackgroundLocationUpdates` only when Always permission is granted, disables automatic pauses, and uses a 25 m distance filter plus a four-second post throttle. If the installed TestFlight build does not contain those plist/plugin settings, or the driver grants only When In Use, locked-screen tracking will remain unreliable until a new native build is approved and installed.

The native app must:

- request OS background location permission only for drivers who start an assigned trip;
- start location collection after the trip transitions to `started`;
- send updates with the parent's bearer token to `POST /api/trips/{trip_id}/locations`;
- stop collection when the trip is ended, all assigned children are dropped off, or the API rejects updates after the safety timeout;
- surface OS-level location indicators and an in-app "sharing active" state;
- never reuse Family AI notification or mobile credentials.

Raw location updates are retained only for `RAW_LOCATION_RETENTION_MINUTES` and are cleaned opportunistically when new updates arrive.

The first native milestone lives in `mobile/schoolhop-mobile`.
