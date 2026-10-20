# Android GPS deadline parsing correction (versionCode 10)

Build #9's native validation called `GET /api/trips/{trip_id}` successfully, then
rejected the production `safety_timeout_at` timestamp. The API uses ISO-8601 values
such as `2026-10-20T05:29:20.625772+00:00`. The bundled `desugar_jdk_libs:2.1.5`
implementation of `Instant.parse` requires a `Z` suffix and throws on that offset.
The former catch block converted this local error into status 409 before GPS
collection. A real active trip could therefore display no first GPS signal even
though validation returned HTTP 200.

The Android-only correction parses with
`OffsetDateTime.parse(value).toInstant().toEpochMilli()` in a small shared native
helper. It retains the existing assigned-driver, `started`, not-ended, token
expiry, safety-deadline and monotonic authorization-lease checks. Delayed,
completed, reassigned, expired and malformed trips still fail closed. Native
collection, scheduling, queuing, retry, background recovery and upload behavior
are unchanged. No backend or trip-record correction is required for this defect.

Debug diagnostics retain the internal tracker result as `status` and identify
local failures using a fixed `validationFailure` enum. `validationHttpStatus`
records the validation response code. For a local deadline parsing rejection,
`httpStatus` is 200, `status` is 409 and `validationFailure` is
`INVALID_SAFETY_DEADLINE`. A genuine backend HTTP 409 remains `httpStatus=409`
with `validationFailure=NONE`. No timestamp strings, response bodies, exceptions,
tokens, coordinates, trip IDs or user IDs are added to diagnostics. The release
diagnostic facade continues to retain/log no diagnostic state.

## Regression coverage

- Exact production timestamp, six fractional digits and `+00:00`.
- Equivalent `Z`, positive/negative offsets, absent fractions, milliseconds and
  nanoseconds, with the correct millisecond instant.
- Malformed, offset-free and invalid-date values rejected.
- Service validation accepts a future timestamp in the production format and
  reaches collection/native upload through fake GPS and fake HTTP connections.
- Local parse failures and malformed JSON are distinguished from real backend
  conflicts without leaking supplied response contents.
- Expired safety windows, delayed/ended trips and existing wrong-driver/token
  regressions remain blocked.

Run Android debug/release JVM tests and lint with JDK 21 and the existing secure
signing environment from [the signing guide](ANDROID_SIGNING.md). Also run frontend
tests, TypeScript, lint and layout regressions. No test should start a real trip,
upload GPS, send notifications or modify production records.

`python3 scripts/test_android_deadline_desugaring.py` compiles the actual helper
against the exact Google backport artifact used by Build #9 (SHA-256
`d8044befae095781b9a80bf1faa92edc30382d75d437476784c1bf991598a976`). It isolates the
library's package name in temporary copies so the desktop JDK cannot silently
supply its newer `java.time` parser. It checks 12 cases, including a control that
the old backport `Instant.parse` still rejects the production value. This is a
backport compatibility test, not ART/device instrumentation. The normal Android
build still uses the unchanged `coreLibraryDesugaringEnabled true` and
`desugar_jdk_libs:2.1.5` configuration; CI runs this compatibility check before APK
verification/upload. No library upgrade is part of the fix.

Local pre-commit validation passed: **52 debug and 52 release JVM tests** (no
failures or skips), **12 backport compatibility cases**, **36 frontend tests**,
**7 layout regressions**, **14 signing tests**, frontend lint and TypeScript.
Android lint passed with zero errors and the existing 12 debug/14 release
warnings. The real local debug APK passed certificate, versionCode 10,
versionName 1.0.25, packaged frontend, production URL and Firebase verification.
Device instrumentation was compiled but not executed; no phone or production
trip was operated by these checks.

## APK and Samsung testing

Use versionCode **10**, versionName **1.0.25**, application ID `com.schoolhop.app`,
the existing Firebase configuration and `https://schoolhop.shourish.com`.
The persistent debug certificate must remain:

```text
d62c353813bf56355ee20cc066e4309fbe53da03d216a374daa924337e386413
```

Build #9 is now installed on the Samsung with this certificate. A verified build
10 signed by the same identity can update it with `adb install -r`, while no active
trip is sharing GPS. Do not uninstall, force a downgrade, or change signing keys.
This build task does not authorize starting/stopping trips or running a field
test against production; use the separately approved GPS acceptance procedure.
