# Persistent Android debug signing

SchoolHop distributes debug testing APKs with one dedicated RSA-4096 PKCS12 key.
The application ID remains `com.schoolhop.app`. This identity is separate from
future Google Play upload and app-signing keys; never configure a release build
with the debug key. Do not regenerate the debug key between builds.

Public debug certificate SHA-256:

```text
d62c353813bf56355ee20cc066e4309fbe53da03d216a374daa924337e386413
```

The first persistent-signing GPS APK uses versionCode **9**, versionName
**1.0.25**, and includes GPS commit
`daa3c200f9f31ecdad4ed03fc1ad2da2fefcf8b9`. Increase versionCode for every later
distributed build; CI defaults to its run number and rejects values below 9.
Re-running a workflow keeps its run number, so use a new dispatch with a higher
versionCode when distributing another APK.

## Storage and GitHub configuration

Private files are outside the repository. Directories are mode `0700`; private
files are mode `0600`, owned by the signing operator:

| Location on the signing host | Contents |
| --- | --- |
| `/home/spela/.local/share/schoolhop/android-debug-signing/debug-signing.p12` | Primary keystore |
| `/home/spela/.local/share/schoolhop/android-debug-signing/metadata.json` | Public fingerprint, alias and purpose |
| `/home/spela/.config/schoolhop/android-debug-signing.json` | Alias and keystore/key passwords |
| `/home/spela/.local/share/schoolhop/signing-backups/android-debug-signing.tar.gz.gpg` | AES-256 encrypted recovery archive |
| `/home/spela/.config/schoolhop/android-debug-backup.passphrase` | Separate backup-decryption passphrase |

The encrypted archive contains `debug-signing.p12`, `credentials.json` and
`metadata.json`. Encryption uses GnuPG symmetric AES-256 with an iterated, salted
SHA-256 S2K. It was restored in memory and compared byte-for-byte before GitHub
configuration. The backup passphrase is not inside the archive. This is a local
encrypted recovery copy; place another encrypted copy and the separately protected
passphrase in operator-controlled off-host storage for protection against host loss.
Never print any password or keystore encoding, including while preparing backups.

Repository `shourish17/SchoolHop` uses these four Actions secrets:

| Secret | Contents |
| --- | --- |
| `ANDROID_DEBUG_KEYSTORE_BASE64` | Base64 of the PKCS12 keystore; encoding is not encryption |
| `ANDROID_DEBUG_KEYSTORE_PASSWORD` | Keystore password |
| `ANDROID_DEBUG_KEY_ALIAS` | Private-key entry alias |
| `ANDROID_DEBUG_KEY_PASSWORD` | Private-key password |

Actions variable `ANDROID_DEBUG_CERT_SHA256` contains the public fingerprint above.
Release signing continues to use the separate existing `ANDROID_KEYSTORE_BASE64`,
`ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, and `ANDROID_KEY_PASSWORD` names.
Do not populate these release names with the debug credentials.

The workflow stages the keystore in a mode-0700 directory under `RUNNER_TEMP`,
with mode 0600 on the file. It requires all four secrets and the fingerprint,
compares the keystore certificate before building, and verifies the finished APK
before artifact upload. Gradle also verifies the certificate and decryptability
of the private-key entry. Debug APK builds without credentials fail instead of
using a new automatic debug identity. Release-only builds require no debug key.
Configuration caching is disabled for signing builds; signing material is neither
cached nor uploaded and temporary signing files are removed with `if: always()`.
The private debug keystore is staged only after dependency installation and
frontend/backend checks, immediately before the Android build. Its path and
passwords are scoped to the debug build step, so the optional release build does
not inherit debug signing configuration.

Firebase remains supplied by `ANDROID_GOOGLE_SERVICES_JSON_BASE64`, with
`require_firebase=true` for the GPS testing build. The workflow pins the packaged
backend URL to `https://schoolhop.shourish.com`. APK verification checks the
certificate, application ID, requested versions, reviewed frontend contents,
production URL and required Firebase resources. An ancestry check requires the
GPS commit. No backend deployment, database mutation, iOS change or Play upload
is part of this process.

Local validation before the signing commit passed: 14 signing-boundary tests,
42 Android debug JVM tests, 36 frontend tests, frontend lint/type checking,
workflow YAML/shell validation and verification of a real signed APK. Gradle
rejected missing credentials on repeated attempts and rejected an incorrect
fingerprint. Release-only task configuration succeeded without debug credentials.
Device instrumentation and Samsung field testing were not performed.

## Local debug APK build

Use JDK 21 and Android SDK platform 36/build tools 35.0.0. Prepare assets and
Firebase without displaying configuration contents:

```bash
ANDROID_GOOGLE_SERVICES_FILE=/home/spela/google_android/google-services.json \
ANDROID_REQUIRE_FIREBASE=true npm run android:sync
```

Read credentials directly into a child process environment; do not put passwords
in command arguments, terminal output, shell history or checked-in properties:

```bash
python3 - <<'PY'
import json, os, subprocess
from pathlib import Path
credentials = json.loads(Path('/home/spela/.config/schoolhop/android-debug-signing.json').read_text())
metadata = json.loads(Path('/home/spela/.local/share/schoolhop/android-debug-signing/metadata.json').read_text())
env = dict(os.environ,
    ANDROID_DEBUG_KEYSTORE_PATH='/home/spela/.local/share/schoolhop/android-debug-signing/debug-signing.p12',
    ANDROID_DEBUG_KEYSTORE_PASSWORD=credentials['store_password'],
    ANDROID_DEBUG_KEY_ALIAS=credentials['alias'],
    ANDROID_DEBUG_KEY_PASSWORD=credentials['key_password'],
    ANDROID_DEBUG_CERT_SHA256=metadata['certificate_sha256'],
    ANDROID_VERSION_CODE='9', ANDROID_VERSION_NAME='1.0.25', ANDROID_REQUIRE_FIREBASE='true')
subprocess.run(['./gradlew', '--no-daemon', '--no-configuration-cache',
                'assembleDebug', ':app:testDebugUnitTest', ':app:assembleDebugAndroidTest'],
               cwd='android', env=env, check=True)
subprocess.run(['python3', 'scripts/android_debug_signing.py', 'verify-apk',
                'android/app/build/outputs/apk/debug/app-debug.apk',
                '--build-tools', str(Path(env['ANDROID_HOME']) / 'build-tools/35.0.0')],
               env=env, check=True)
PY
```

The APK is `android/app/build/outputs/apk/debug/app-debug.apk`. Before distributing
a later build, replace versionCode 9 with the next unused higher value. Never use
Gradle `--debug`, dump the environment, or enable shell tracing around credentials.

## Recovery

Restore the existing identity, never generate a replacement as a recovery step.
Obtain the encrypted archive and its separately stored passphrase through secure
operator-controlled storage. The commands below use the protected local
passphrase file without printing it. For off-host recovery, use GnuPG's interactive
passphrase prompt instead of passing a literal password on the command line.

```bash
set -euo pipefail
umask 077
SIGNING_RECOVERY_DIR=$(mktemp -d /home/spela/schoolhop-signing-recovery.XXXXXX)
gpg --batch --no-symkey-cache --pinentry-mode loopback \
  --passphrase-file /home/spela/.config/schoolhop/android-debug-backup.passphrase \
  --output "$SIGNING_RECOVERY_DIR/recovered.tar.gz" \
  --decrypt /home/spela/.local/share/schoolhop/signing-backups/android-debug-signing.tar.gz.gpg
tar -xzf "$SIGNING_RECOVERY_DIR/recovered.tar.gz" \
  --no-same-owner --no-same-permissions -C "$SIGNING_RECOVERY_DIR" \
  debug-signing.p12 credentials.json metadata.json
rm -f "$SIGNING_RECOVERY_DIR/recovered.tar.gz"
```

Only proceed after GnuPG exits successfully. Inspect permissions without opening
the credential files in chat or logs. Use a protected Python process to load the
recovered credentials, export only the **public** certificate with `keytool
-exportcert -storepass:env`, and compare its SHA-256 to the pinned fingerprint.
After a match, restore files to their original restricted locations; retain the
existing protected files until the recovered copy is verified.

To restore GitHub secrets from verified recovered files, execute this from the
same shell (never add `print` calls for `values` or enable tracing):

```bash
export SIGNING_RECOVERY_DIR
python3 - <<'PY'
import base64, hashlib, json, os, subprocess
from pathlib import Path
root = Path(os.environ['SIGNING_RECOVERY_DIR'])
credentials = json.loads((root / 'credentials.json').read_text())
expected = 'd62c353813bf56355ee20cc066e4309fbe53da03d216a374daa924337e386413'
env = dict(os.environ, SCHOOLHOP_RECOVERY_STOREPASS=credentials['store_password'])
certificate = subprocess.run(['keytool', '-exportcert', '-keystore', str(root / 'debug-signing.p12'),
    '-alias', credentials['alias'], '-storepass:env', 'SCHOOLHOP_RECOVERY_STOREPASS'],
    env=env, capture_output=True)
if certificate.returncode or hashlib.sha256(certificate.stdout).hexdigest() != expected:
    raise SystemExit('Recovered identity verification failed; no GitHub changes made')
values = {
    'ANDROID_DEBUG_KEYSTORE_BASE64': base64.b64encode((root / 'debug-signing.p12').read_bytes()),
    'ANDROID_DEBUG_KEYSTORE_PASSWORD': credentials['store_password'].encode(),
    'ANDROID_DEBUG_KEY_ALIAS': credentials['alias'].encode(),
    'ANDROID_DEBUG_KEY_PASSWORD': credentials['key_password'].encode(),
}
for name, value in values.items():
    result = subprocess.run(['gh', 'secret', 'set', name, '--repo', 'shourish17/SchoolHop'],
                            input=value, capture_output=True)
    if result.returncode:
        raise SystemExit('Secret recovery failed; stop before building')
result = subprocess.run(['gh', 'variable', 'set', 'ANDROID_DEBUG_CERT_SHA256',
    '--repo', 'shourish17/SchoolHop', '--body', expected], capture_output=True)
if result.returncode:
    raise SystemExit('Fingerprint configuration failed; stop before building')
print('Recovered signing configuration installed; verify the next APK before distribution')
PY
```

Securely remove temporary recovered plaintext files after verified restoration.
For example, after the recovery verification above has succeeded:

```bash
install -m 600 "$SIGNING_RECOVERY_DIR/debug-signing.p12" \
  /home/spela/.local/share/schoolhop/android-debug-signing/debug-signing.p12
install -m 600 "$SIGNING_RECOVERY_DIR/credentials.json" \
  /home/spela/.config/schoolhop/android-debug-signing.json
install -m 600 "$SIGNING_RECOVERY_DIR/metadata.json" \
  /home/spela/.local/share/schoolhop/android-debug-signing/metadata.json
rm -rf -- "$SIGNING_RECOVERY_DIR"
```

Keep the encrypted backup and passphrase protected and separate. If both the
primary keystore and recoverable backup are lost, existing installations signed
by this identity cannot receive a normal update. Password changes do not require
a new key, but must be coordinated across primary storage, backup and CI.

## Samsung and other Android devices

The Samsung currently installed build 7 has certificate
`fa2005e9c724f0302c323c56b6844f9072b3474c396e102aab966bd71d89fa54`.
Its private key was not recovered. The new persistent certificate differs, so
**do not attempt an in-place update on that installation or uninstall it**.
A normal install in another Android user/profile may still conflict because
package signing identity is shared across users. Use a separate approved device
or emulator with no existing `com.schoolhop.app` package, or an already compatible
installation signed by the new persistent key. A Samsung migration requires a
separate decision; versionCode 9 does not bypass the certificate check.

Verify the downloaded APK with `apksigner verify --print-certs`, compare its
certificate to the public fingerprint above, and confirm versionCode/versionName
using `aapt dump badging`. On a device with no existing SchoolHop installation:

```bash
adb -s DEVICE_SERIAL install /path/to/app-debug.apk
```

For a later update already signed with the persistent identity, while no active
trip is sharing GPS:

```bash
adb -s DEVICE_SERIAL install -r /path/to/newer-app-debug.apk
```

Stop on a signing conflict or downgrade; do not uninstall or force a downgrade.
Follow [the controlled GPS acceptance procedure](ANDROID_LOCKED_SCREEN_GPS.md)
only after a compatible installation and separately approved trip fixture.
