"""Stage a private debug keystore and verify distributable Android APKs.

Only public certificate fingerprints and generic diagnostics may reach stdout.
Passwords are passed to keytool through environment variables, never arguments.
"""

import argparse
import base64
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile


class SigningError(Exception):
    """A safe diagnostic that contains no supplied credential values."""


def required(name):
    value = os.environ.get(name)
    if not value:
        raise SigningError(f"Missing required setting: {name}")
    return value


def expected_certificate():
    value = required("ANDROID_DEBUG_CERT_SHA256")
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise SigningError("ANDROID_DEBUG_CERT_SHA256 must be a lowercase SHA-256 fingerprint")
    return value


def capture(args):
    result = subprocess.run(args, capture_output=True, check=False)
    if result.returncode:
        raise SigningError("Android signing inspection failed; tool output withheld")
    return result.stdout


def stage():
    expected = expected_certificate()
    encoded = required("ANDROID_DEBUG_KEYSTORE_BASE64")
    required("ANDROID_DEBUG_KEYSTORE_PASSWORD")
    alias = required("ANDROID_DEBUG_KEY_ALIAS")
    required("ANDROID_DEBUG_KEY_PASSWORD")
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise SigningError("Invalid debug keystore encoding") from None
    if not data or len(data) > 32768:
        raise SigningError("Invalid debug keystore size")
    directory = Path(required("RUNNER_TEMP")) / "schoolhop-debug-signing"
    directory.mkdir(mode=0o700)
    target = directory / "debug-signing.p12"
    try:
        with target.open("xb") as output:
            os.chmod(target, 0o600)
            output.write(data)
        certificate = capture([
            "keytool", "-exportcert", "-keystore", str(target), "-alias", alias,
            "-storepass:env", "ANDROID_DEBUG_KEYSTORE_PASSWORD",
        ])
        if hashlib.sha256(certificate).hexdigest() != expected:
            raise SigningError("Debug keystore certificate does not match the pinned fingerprint")
    except Exception:
        target.unlink(missing_ok=True)
        directory.rmdir()
        raise
    print(f"Persistent debug keystore verified: {expected}")


def verify_apk(apk, build_tools):
    expected = expected_certificate()
    version_code = required("ANDROID_VERSION_CODE")
    version_name = required("ANDROID_VERSION_NAME")
    if not re.fullmatch(r"[1-9][0-9]*", version_code) or not 9 <= int(version_code) <= 2100000000:
        raise SigningError("Android versionCode must be an integer from 9 to 2100000000")
    verification = capture([
        str(build_tools / "apksigner"), "verify", "--verbose", "--print-certs", str(apk),
    ]).decode("utf-8")
    certificates = re.findall(r"^Signer #\d+ certificate SHA-256 digest: ([0-9a-f]{64})$", verification, re.M)
    if certificates != [expected]:
        raise SigningError("APK signer does not match the single pinned debug certificate")
    badging = capture([str(build_tools / "aapt"), "dump", "badging", str(apk)]).decode("utf-8")
    package = re.search(r"^package: name='([^']*)' versionCode='([^']*)' versionName='([^']*)'", badging, re.M)
    if not package or package.groups() != ("com.schoolhop.app", version_code, version_name):
        raise SigningError("APK package or version does not match the requested SchoolHop build")
    with zipfile.ZipFile(apk) as archive:
        index = archive.read("assets/public/index.html").decode("utf-8")
        urls = re.findall(r'apiBaseUrl:\s*"([^"\n]+)"', index)
        if urls != ["https://schoolhop.shourish.com"]:
            raise SigningError("APK does not use the SchoolHop production backend URL")
        if archive.read("assets/public/static/app.js") != Path("app/static/app.js").read_bytes():
            raise SigningError("APK frontend does not match the reviewed source")
    resources = capture([str(build_tools / "aapt"), "dump", "--values", "resources", str(apk)]).decode("utf-8")
    if required("ANDROID_REQUIRE_FIREBASE") == "true" and not all(
        value in resources for value in ["schoolhop-1c00d", "string/google_app_id", "string/gcm_defaultSenderId"]
    ):
        raise SigningError("Expected SchoolHop Firebase resources are missing from the APK")
    print(f"APK signing verification PASSED: {expected}")
    print(f"APK package: com.schoolhop.app; versionCode: {version_code}; versionName: {version_name}")
    print("Packaged frontend, production backend URL and Firebase configuration verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("stage")
    verify = commands.add_parser("verify-apk")
    verify.add_argument("apk", type=Path)
    verify.add_argument("--build-tools", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "stage":
            stage()
        else:
            verify_apk(args.apk, args.build_tools)
    except SigningError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print("Android signing validation failed; details withheld to protect credentials", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
