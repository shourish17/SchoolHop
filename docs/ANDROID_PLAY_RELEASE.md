# Google Play Internal Testing release preparation

Release candidate: `release/android-handover-13-20261021`; application ID
`com.schoolhop.app`; versionName **1.0.26**, versionCode **13**, minSdkVersion
**24**. This includes the reviewed handover fix at `fbec716` without changing
its frontend or native implementation. Code 13 is reserved for this candidate;
do not distribute another build with this code.

The candidate starts at successful Android commit `bcbbd2d` and includes the
current workspace's native delayed-trip GPS/no-cache fixes and shared frontend
GPS freshness, resume, push and handover fixes with their focused tests. Backend,
deployment configuration, Family AI and iOS source are excluded from the release
changes. Existing source files and builds in the original workspace are intact.
The workflow builds only Android and never calls the Play upload workflow.

## Persistent upload identity

RSA-4096, SHA256withRSA, PKCS12, 10,000-day validity. Public upload certificate
SHA-256:

```text
40116592e5d8e8c9ba7bee9429fa5bd49936f661be4cf50fffee4dd655fdae7d
```

This is separate from the permanent debug signing key. Restore this identity for
future releases; never generate a fresh key for each build.

| Protected host location | Purpose |
| --- | --- |
| `/home/spela/.local/share/schoolhop/android-upload-signing/upload-signing.p12` | Persistent private upload keystore |
| `/home/spela/.config/schoolhop/android-upload-signing.json` | Keystore/key passwords and alias |
| `/home/spela/.local/share/schoolhop/android-upload-signing/metadata.json` | Public fingerprint and release reservation |
| `/home/spela/.local/share/schoolhop/android-upload-signing/upload-certificate.pem` | Public certificate for Play Console registration |
| `/home/spela/.local/share/schoolhop/signing-backups/android-upload-signing.tar.gz.gpg` | AES-256 encrypted recovery archive |
| `/home/spela/.config/schoolhop/android-upload-backup.passphrase` | Separate recovery passphrase |

Private files are mode `0600` and directories `0700`, owned by the signing
operator. The encrypted archive contains the keystore, credentials, metadata and
public certificate. Its passphrase is excluded. A decrypt-and-restore comparison
in memory verified every archived file byte-for-byte.

**Back up off-host:** copy the `.tar.gz.gpg` archive to operator-controlled backup
storage and put the passphrase in a separate password manager or recovery vault.
Do not put them in the same location, source control, chat or Actions artifacts.
The current recovery archive is on this host; an independent off-host copy has
not been made. GitHub secrets cannot be downloaded as a recovery method.

For recovery, decrypt with GnuPG's interactive password prompt in a protected
mode-0700 temporary directory, extract with restrictive permissions, and export
the public certificate using `keytool -exportcert -storepass:env` with passwords
loaded directly from the recovered credentials into a child environment. Confirm
the SHA-256 above before restoring local files or GitHub secrets. Do not print
passwords, keystore data or decrypted archive contents.

GitHub repository secrets `ANDROID_KEYSTORE_BASE64`,
`ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` contain
this upload identity. Actions variable `ANDROID_UPLOAD_CERT_SHA256` pins its
public certificate. Firebase continues to use the existing
`ANDROID_GOOGLE_SERVICES_JSON_BASE64` secret. No debug or Firebase credential
was replaced.

## Play App Signing compatibility

The AAB is signed with the upload key. Google Play must use its separate app
signing key for installed APKs. When first enrolling, let Play generate/manage
the app signing key and register this public upload certificate when requested.
If `com.schoolhop.app` already exists in Play, compare its registered upload
certificate first; an existing different upload key requires recovery or Play's
upload-key reset process. No enrollment, key reset or Google Play upload is
performed by this build.

See [Android app signing documentation](https://developer.android.com/studio/publish/app-signing).

Both HTTPS links to `schoolhop.shourish.com` and the `schoolhop://` scheme remain
in the release manifest. A read-only check on 21 October 2026 found the production
`/.well-known/assetlinks.json` URL returns HTTP 404. Verified HTTPS app links
therefore cannot currently be confirmed. After enrollment, the site's association
must contain the **Play app-signing certificate**, not merely this upload
certificate. That requires a separately authorized backend/site change. Custom
scheme and notification action routing are retained and checked in source.

## Build and verification

Dispatch `.github/workflows/android-build.yml` on this release branch, with
`version_name=1.0.26` and `version_code=13`. It performs frontend tests,
typechecking/lint, real cryptographic verification boundary tests, release JVM
tests, Android release lint, GPS deadline desugaring checks, and a check that
Gradle rejects release signing without credentials. Only then does it stage the
upload key, compare its certificate and build `bundleRelease`.

The workflow requires reviewed commit `fbec716` and unchanged reviewed frontend,
native code and plugin dependencies. Fifteen browser regressions cover Android
handover, parent tracking and retained iOS behaviour. The final AAB's `app.js`
must match the reviewed SHA-256
`44cae38a82be9d3e9bfc5f4a7bfa2bf68c4c58d0536cbff6062d049da7be620d`;
the verification report records that hash and the reviewed commit.

Gradle also verifies private-key decryptability and the pinned upload certificate.
No signing material is added to caches or artifacts. Temporary signing/Firebase
files are removed on success or failure, and configuration caching is disabled.

The completed bundle must pass `jarsigner`, bundletool validation, and Java
`JarFile` signature verification of **every payload entry**, rejecting unsigned
additions, changed payloads or a different signer. Inspection also validates
package/version, minSdkVersion 24, release flags, location/background/foreground-service
permissions, Firebase resources and messaging service, deep-link declarations,
Capacitor plugins, the production API URL and packaged frontend/source equality.

The AAB artifact includes `android-release-verification.json` with its commit,
version, upload certificate and AAB SHA-256. The separate checks artifact contains
release test/lint reports. Artifacts are retained for 90 days. Keep a durable copy
of approved releases if longer retention is required.

Physical-device GPS, locked-screen operation, push delivery, handover and link
opening through a Play-installed build remain Internal Testing acceptance tests;
JVM/static checks do not establish actual device delivery or timing.

Local focused validation passed: 55 release JVM tests, 47 frontend tests, 7
Chromium layout checks, 8 real release-signing boundary tests, 14 existing
signing checks, frontend lint/typechecking and 12 deadline compatibility checks.
Android release lint had zero errors and 14 warnings concerning icon/resources,
backup rules and one discouraged API. The actual unsigned `bundleRelease`
invocation was rejected by the explicit signing guard.
