# Mobile Background Location Notes

SchoolHop's API accepts and authorizes live GPS updates only while a trip is active:

- `POST /api/trips/{trip_id}/start`
- `POST /api/trips/{trip_id}/locations`
- `GET /api/trips/{trip_id}/location`
- `POST /api/trips/{trip_id}/end`

The web UI includes a manual foreground browser GPS sender for local MVP testing only. A normal website or PWA should not be treated as reliable locked-screen tracking.

For production driver tracking, build native iOS/Android shells or a cross-platform app using platform background-location APIs. The native app must:

- request OS background location permission only for drivers who start an assigned trip;
- start location collection after the trip transitions to `started`;
- send updates with the parent's bearer token to `POST /api/trips/{trip_id}/locations`;
- stop collection when the trip is ended, all assigned children are dropped off, or the API rejects updates after the safety timeout;
- surface OS-level location indicators and an in-app "sharing active" state;
- never reuse Family AI notification or mobile credentials.

Raw location updates are retained only for `RAW_LOCATION_RETENTION_MINUTES` and are cleaned opportunistically when new updates arrive.

The first native milestone lives in `mobile/schoolhop-mobile`.
