# Temporary Android notification diagnostics

These changes target `feature/schoolhop-my-carpool-ui`. They do not activate the production sender, send FCM messages, alter server credentials, or change a device's notification settings.

## Findings and smallest fixes

Two implementation issues are confirmed:

1. Capacitor Push Notifications 7.0.7 returns `receive=granted` unconditionally below API 33. The guarded SchoolHop plugin now uses `NotificationManagerCompat.areNotificationsEnabled()` for both checks and requests on those versions. Android 12 has no `POST_NOTIFICATIONS` runtime prompt; `denied` accurately reports an app-level block that must be resolved in Settings.
2. The channel used `sound: "default"`. This plugin interprets any supplied sound as an app raw resource, producing `android.resource://com.schoolhop.app/raw/default`. That resource does not exist. The channel configuration now omits this property so new channels keep Android's system default sound. Existing channel sound preferences are preserved; the code does not delete, recreate, or reset channels. If the snapshot confirms the old invalid resource, select a built-in sound in that channel's Samsung Settings page as a separate device-owner action.

Neither issue proves why the earlier FCM notification never appeared. The sound issue can affect audio; it does not establish a notification-policy block. `POST_NOTIFICATION=allow`, importance 4, Settings Allowed, and Samsung's undocumented `allowNoti=false` cannot by themselves identify the effective gate. The actual in-app framework result and message-receive path are needed. The previous conclusion that `allowNoti=false` necessarily proves blocking would be too strong.

The main merged manifest contains the Firebase receiver, Capacitor messaging service at default priority, and Firebase's SDK fallback service at priority -500. The fallback is normal, not a competing equally ranked app handler. The guarded Capacitor plugin is selected in the generated plugin registry, and `presentationOptions` includes `alert`. The default manifest channel and payload channel both use `schoolhop_activity`.

Foreground messages pass through the messaging service to Capacitor, whose native implementation posts the notification before notifying JavaScript. Background notification-plus-data messages can be consumed and displayed by the Firebase SDK before `onMessageReceived` runs. The JavaScript listener refreshes the authenticated inbox; a direct test push does not create a database inbox row.

The earlier real request included a notification title/body, HIGH transport priority, a 60-second TTL, and `action_url=/?tab=alerts`. HTTP 200 confirms provider acceptance, not phone receipt or notification display. An expired message will not replay after a settings correction.

References: [Firebase foreground/background behavior](https://firebase.google.com/docs/cloud-messaging/android/receive-messages), [notification enabled API](https://developer.android.com/reference/androidx/core/app/NotificationManagerCompat#areNotificationsEnabled()), [channel behavior and user control](https://developer.android.com/develop/ui/compose/notifications/channels).

## Debug-only implementation

- `src/debug` contains the full diagnostic snapshot/logger and a service subclass. The debug manifest replaces the stock Capacitor service rather than adding an equally ranked competitor. Each intent and message is delegated to the existing implementation exactly once; the wrapper does not post notifications itself.
- `src/release` contains only a no-op diagnostic facade. Release uses the original Capacitor service. Calling `SchoolHopConfig.getNotificationDiagnostics` in release returns unavailable.
- Automatic debug snapshots occur at plugin load, resume, permission checks, channel creation, registration requests, service creation, SDK dispatch, and Capacitor message handling.
- State is process-local. No debug preferences, files, backend telemetry, or database writes are added.
- The logger uses a whitelist of safe metadata. It never logs tokens, FCM message IDs, message contents, arbitrary data fields, intent extras, exception messages, credential values, or personal sound URIs. Android UID/profile numbers are included to diagnose installation mismatches.

Snapshot fields include:

| Fields | Meaning |
| --- | --- |
| `packageName`, `uid`, `androidUserId`, `apiLevel`, `targetSdk` | Identify the app instance/profile and OS. |
| `notificationsEnabledCompat`, `notificationsEnabledFramework` | Actual app-level checks from the calling app identity. |
| `notificationsPaused` | Framework temporary suspension/restriction state, where supported. |
| `channel.exists`, `importance`, `groupBlocked`, `enabled` | `enabled` combines the app gate, existing nonzero-importance channel, and channel-group gate. It does not prove FCM connectivity or heads-up presentation. |
| `channel.sound.kind`, `resourceExists` | Classify sound without printing its URI; detect the missing raw resource. |
| `messagingServices`, `resolvedMessagingService`, `diagnosticServiceSelected` | Service classes/priorities and actual PackageManager resolution. |
| `capacitorPushPluginLoaded`, `foregroundAlertConfigured`, `defaultChannelMatches` | Check native plugin and display configuration. |
| `processImportance`, `processForeground`, `deviceLocked` | Process state; Firebase may treat a locked device as background even if the process is foreground. |
| `receivedCallbackCount`, `lastReceiptElapsedMs`, `lastCallbackPayloadHasNotification`, `lastCallbackPayloadHasData` | Callback observations only; no payload values. |
| `receivedMessageIntentCount`, `lastIntentKind`, `lastIntentHasNotificationPayload` | SDK intent observations, including background notification messages that bypass the callback. The kind is classified as message/token/other; no arbitrary intent action is logged. |
| `dispatchCompletionCount`, `lastDispatch`, `activeNotificationCount` | SDK/Capacitor path and currently active app notifications. A completed handling call or immediate active-count snapshot is not proof of user-visible display. |

`sdk_consumed_notification_without_callback` can be the expected background display path. It can also cover SDK suppression or duplicate handling; it is deliberately not labelled "delivered". `forwarded_to_capacitor` means the callback ran, not that the OS accepted a notification post.

## Collect evidence without sending another message

A new debug APK is required to run these diagnostics on the Galaxy. Use the existing debug signing identity. Installation or device changes were not performed as part of this task.

Read only the sanitized tag:

```bash
adb logcat -d -s SchoolHopPushDebug:I '*:S'
```

From an attached debug WebView's console, this read-only native method returns the same safe state:

```js
await window.Capacitor.Plugins.SchoolHopConfig.getNotificationDiagnostics()
```

Match `uid` and `androidUserId` to the exact SchoolHop entry in the existing notification dump. Work profiles and Secure Folder installations can have separate policies despite the same package name. Compare against the APK actually installed, rather than assuming repository settings match an older build.

| Result | Interpretation / next step |
| --- | --- |
| Compat/framework false | App-level restriction is effective. Inspect the matching profile's master notification setting and policy. App-op allow alone is insufficient evidence. |
| App enabled, channel disabled | Inspect that channel's importance/group state. |
| Both enabled, Samsung `allowNoti=false` | The vendor field alone does not establish a standard notification block. Inspect temporary suspension and receive/display evidence before changing settings or code. |
| No service/receive event | A receipt was not observed in that process/log window. Check retained phone/Firebase logs, connectivity, stopped state, profile state, and short-TTL expiry. Do not infer receipt from server HTTP 200. |
| SDK dispatch, no callback, notification payload | Can be the normal background SDK display path. Inspect Android notification-service rejection/suppression evidence. |
| Callback and Capacitor handling complete, no visible notification | Check actual channel/app state and notification-service logs, including whether a record was immediately cancelled/hidden. |

Do not use another Shell-owned notification as proof of SchoolHop posting permission. Do not clear data, reset app-ops, delete channels, reinstall, or send another message merely to gather these snapshots. Any later real delivery test requires separate authorization.

## Validation

Regression coverage includes the JavaScript channel definition and permission-denial flow, Capacitor method exports, and Android instrumentation tests for live framework/channel values, unique service selection, payload/URI redaction, and Android 12 permission reporting. The instrumentation tests only inspect state or record synthetic payload shape; they do not post notifications or contact FCM.

Validation on the VPS:

- `npm test`: 29 passed, including the channel sound regression and denied-permission flow.
- `npm run lint`, `npm run typecheck`, `npm run android:sync`: passed.
- Gradle `assembleDebug :app:testDebugUnitTest :app:assembleDebugAndroidTest :app:compileReleaseJavaWithJavac`: passed; four JVM tests passed. The instrumentation APK compiled with five diagnostic tests, but device execution was not performed.
- Merged debug/release manifests: each has exactly one highest-priority FCM handler. Debug selects the diagnostic wrapper; release selects Capacitor. Release bytecode contains no diagnostic logger/snapshot implementation or diagnostic service class.
- Additional Android `:app:lintDebug`: fails on seven existing errors in unchanged `SchoolHopLocationService.java` / `SchoolHopLocationPlugin.java` (API guards, foreground-service type, and receiver flags). These GPS issues are outside this notification change; none of the lint errors is in the changed files.
- `git diff --check`: passed.

No Galaxy is attached to this VPS, so the actual Samsung framework result and notification-loss root cause remain device acceptance work. No instrumentation tests or FCM sends were run on the phone.

## Changed files

- `android/app/src/main/java/com/schoolhop/app/SchoolHopPushNotificationsPlugin.java`
- `android/app/src/main/java/com/schoolhop/app/SchoolHopConfigPlugin.java`
- `android/app/src/debug/AndroidManifest.xml`
- `android/app/src/debug/java/com/schoolhop/app/SchoolHopDiagnosticMessagingService.java`
- `android/app/src/debug/java/com/schoolhop/app/SchoolHopPushDiagnostics.java`
- `android/app/src/release/java/com/schoolhop/app/SchoolHopPushDiagnostics.java`
- `android/app/src/test/java/com/schoolhop/app/SchoolHopFirebaseConfigTest.java`
- `android/app/src/androidTest/java/com/schoolhop/app/NotificationDiagnosticsTest.java`
- `app/static/app.js`
- `tests/frontend/native-push.test.mjs`
- `docs/ANDROID_NOTIFICATION_DIAGNOSTICS.md`

## Removal

After investigation, remove the debug manifest/service/diagnostic class, release no-op facade, native diagnostic method/hooks, and diagnostic instrumentation tests together. Keep the corrected pre-33 permission checks and system-default sound configuration. No server or database cleanup is needed.
