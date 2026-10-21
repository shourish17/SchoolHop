"""Stage upload credentials and verify the actual signed Play release bundle."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile


def required(name):
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing required setting: {name}")
    return value


def capture(args):
    result = subprocess.run(args, capture_output=True)
    if result.returncode:
        raise ValueError("Release inspection failed; tool output withheld")
    return result.stdout


def fingerprint():
    expected = required("ANDROID_UPLOAD_CERT_SHA256")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Invalid upload certificate fingerprint")
    if expected == required("ANDROID_DEBUG_CERT_SHA256"):
        raise ValueError("Release must not use the debug identity")
    return expected


def stage():
    expected = fingerprint()
    alias = required("ANDROID_KEY_ALIAS")
    required("ANDROID_KEYSTORE_PASSWORD")
    required("ANDROID_KEY_PASSWORD")
    data = base64.b64decode(required("ANDROID_KEYSTORE_BASE64"), validate=True)
    if not data or len(data) > 32768:
        raise ValueError("Invalid upload keystore size")
    directory = Path(required("RUNNER_TEMP")) / "schoolhop-upload-signing"
    directory.mkdir(mode=0o700)
    target = directory / "upload-signing.p12"
    try:
        with target.open("xb") as output:
            target.chmod(0o600)
            output.write(data)
        cert = capture(["keytool", "-exportcert", "-keystore", str(target), "-alias", alias,
                        "-storepass:env", "ANDROID_KEYSTORE_PASSWORD"])
        if hashlib.sha256(cert).hexdigest() != expected:
            raise ValueError("Upload keystore differs from pinned certificate")
    except Exception:
        target.unlink(missing_ok=True)
        directory.rmdir()
        raise
    print(f"Persistent upload keystore verified: {expected}")


def verify(bundle, bundletool, report):
    expected = fingerprint()
    code, name = required("ANDROID_VERSION_CODE"), required("ANDROID_VERSION_NAME")
    assert re.fullmatch(r"[1-9][0-9]*", code) and 9 <= int(code) <= 2100000000
    capture(["java", "scripts/VerifyAndroidBundle.java", str(bundle)])
    signed = capture(["jarsigner", "-verify", str(bundle)]).decode()
    assert "jar verified." in signed
    command = ["java", "-jar", str(bundletool)]
    capture(command + ["validate", "--bundle", str(bundle)])
    manifest = ET.fromstring(capture(command + ["dump", "manifest", "--bundle", str(bundle)]))
    android = "{http://schemas.android.com/apk/res/android}"
    assert manifest.get("package") == "com.schoolhop.app"
    assert manifest.get(android + "versionCode") == code
    assert manifest.get(android + "versionName") == name
    app = manifest.find("application")
    assert app is not None and app.get(android + "debuggable", "false") == "false"
    assert app.get(android + "usesCleartextTraffic") == "false"
    assert app.get(android + "testOnly", "false") == "false"
    permissions = {p.get(android + "name") for p in manifest.findall("uses-permission")}
    assert {"android.permission." + p for p in ["ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION",
        "ACCESS_BACKGROUND_LOCATION", "FOREGROUND_SERVICE_LOCATION", "FOREGROUND_SERVICE",
        "POST_NOTIFICATIONS", "WAKE_LOCK", "INTERNET"]} <= permissions
    services = {s.get(android + "name"): s for s in app.findall("service")}
    location = services["com.schoolhop.app.SchoolHopLocationService"]
    # bundletool may dump compiled enum flags numerically (location = 0x8).
    assert location.get(android + "foregroundServiceType") in {"location", "8", "0x00000008", "0x8"}
    assert location.get(android + "exported") == "false"
    assert location.get(android + "stopWithTask") == "false"
    assert "com.capacitorjs.plugins.pushnotifications.MessagingService" in services
    activity = next(a for a in app.findall("activity") if a.get(android + "name") == "com.schoolhop.app.MainActivity")
    filters = activity.findall("intent-filter")
    assert any(f.get(android + "autoVerify") == "true" and any(d.get(android + "scheme") == "https"
        and d.get(android + "host") == "schoolhop.shourish.com" for d in f.findall("data")) for f in filters)
    assert any(d.get(android + "scheme") == "schoolhop" for f in filters for d in f.findall("data"))
    resources = capture(command + ["dump", "resources", "--bundle", str(bundle), "--values"]).decode()
    assert all(value in resources for value in ["schoolhop-1c00d", "google_app_id", "gcm_defaultSenderId"])
    with zipfile.ZipFile(bundle) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(set(archive.namelist()))
        assert {"BundleConfig.pb", "base/manifest/AndroidManifest.xml", "base/resources.pb", "base/dex/classes.dex"} <= set(archive.namelist())
        index = archive.read("base/assets/public/index.html").decode()
        assert re.findall(r'apiBaseUrl:\s*"([^"\n]+)"', index) == ["https://schoolhop.shourish.com"]
        assert re.findall(r'appVersion:\s*"([^"\n]+)"', index) == [name]
        for filename in ["app.js", "service-worker.js", "styles.css"]:
            assert archive.read("base/assets/public/static/" + filename) == Path("app/static", filename).read_bytes()
        config = json.loads(archive.read("base/assets/capacitor.config.json"))
        assert config["appId"] == "com.schoolhop.app"
        assert config["plugins"]["CapacitorHttp"]["enabled"] is True
        assert "server" not in config or "url" not in config["server"]
        assert b"com.schoolhop.app.SchoolHopPushNotificationsPlugin" in archive.read("base/assets/capacitor.plugins.json")
    result = {"application_id": "com.schoolhop.app", "version_code": int(code), "version_name": name,
        "commit": required("GITHUB_SHA"), "upload_certificate_sha256": expected,
        "aab_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(), "checks": ["all payload entries cryptographically signed",
        "pinned upload certificate", "jarsigner", "bundletool validate", "release manifest and version",
        "GPS and background location", "FCM configuration and service", "HTTPS and custom deep links",
        "packaged frontend matches source", "production API URL", "Capacitor plugins"],
        "play_uploaded": False, "device_tests": "not run"}
    report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stage")
    check = sub.add_parser("verify")
    check.add_argument("bundle", type=Path)
    check.add_argument("--bundletool", type=Path, required=True)
    check.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "stage":
            stage()
        else:
            verify(args.bundle, args.bundletool, args.report)
    except Exception:
        raise SystemExit("Android release validation failed; private tool output withheld") from None
