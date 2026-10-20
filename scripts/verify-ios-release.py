#!/usr/bin/env python3
"""Check reviewed SchoolHop sources and exported IPA without exposing credentials."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / ".github/ios-apns-handover-source.json"
TOOLING = {
    ".github/workflows/ios-testflight.yml",
    ".github/ios-apns-handover-source.json",
    "scripts/verify-ios-release.py",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def command(args):
    result = subprocess.run(args, capture_output=True, cwd=ROOT)
    require(result.returncode == 0, f"Verification command failed: {Path(args[0]).name}; diagnostic output withheld")
    return result.stdout, result.stderr


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_source(manifest, expected_commit):
    require(re.fullmatch(r"[0-9a-f]{40}", expected_commit), "Expected commit must be a full SHA")
    head = command(["git", "rev-parse", "HEAD"])[0].decode().strip()
    require(head == expected_commit, "Checkout does not match approved commit")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        require(os.environ.get("GITHUB_REF") == "refs/heads/" + manifest["expected_branch"], "Workflow must be dispatched on the dedicated release branch")
        require(os.environ.get("GITHUB_SHA") == expected_commit, "Workflow event SHA differs from approved commit")
    tracked = set(command(["git", "ls-files", "-z"])[0].decode().rstrip("\0").split("\0"))
    require(tracked == set(manifest["files"]) | TOOLING, "Unexpected source files; only reviewed source and three release-tooling files are allowed")
    for relative, expected in manifest["files"].items():
        require(digest((ROOT / relative).read_bytes()) == expected, f"Reviewed source hash differs: {relative}")
    migrations = sorted(p.name for p in (ROOT / "migrations").glob("*.sql"))
    expected_migrations = sorted(Path(p).name for p in manifest["files"] if p.startswith("migrations/") and p.endswith(".sql"))
    require(migrations == expected_migrations and len(migrations) == 12, "Migration set differs from reviewed 001-012 baseline")
    return {"commit": head, "reviewed_source_files": len(manifest["files"]), "migration_count": 12}


def verify_web(manifest):
    for relative in ("app/main.py", "app/static/app.js", "ios/App/App/AppDelegate.swift", "ios/App/App/SchoolHopLocationPlugin.swift"):
        require(digest((ROOT / relative).read_bytes()) == manifest["files"][relative], f"Build input differs: {relative}")
    expected = manifest["files"]["app/static/app.js"]
    for relative in ("build/capacitor/static/app.js", "ios/App/App/public/static/app.js"):
        require(digest((ROOT / relative).read_bytes()) == expected, f"Bundled JavaScript differs: {relative}")
    verify_payload(
        (ROOT / "ios/App/App/public/static/app.js").read_bytes(),
        (ROOT / "ios/App/App/public/index.html").read_bytes(),
        json.loads((ROOT / "ios/App/App/capacitor.config.json").read_text()),
        manifest,
    )
    delegate = (ROOT / "ios/App/App/AppDelegate.swift").read_text()
    require("UNUserNotificationCenter.current().delegate = self" not in delegate, "Competing notification delegate remains")
    return {"bundled_app_js_sha256": expected, "native_delegate_source_sha256": manifest["files"]["ios/App/App/AppDelegate.swift"]}


def verify_payload(js, index, config, manifest):
    require(digest(js) == manifest["files"]["app/static/app.js"], "IPA JavaScript is not the reviewed handover/APNs candidate")
    require(b"app.js?v=31" in index, "Expected shell version 31 missing")
    require(b"https://schoolhop.shourish.com" in index, "Expected production API URL missing")
    require(config.get("appId") == manifest["bundle_id"], "Capacitor bundle ID mismatch")
    require(not config.get("server", {}).get("url"), "Remote UI override is not allowed")
    require("PushNotificationsPlugin" in config.get("packageClassList", []), "Native push plugin is missing")


def verify_ipa(ipa_path, manifest, build_number, signed):
    with zipfile.ZipFile(ipa_path) as archive:
        candidates = [name for name in archive.namelist() if name.startswith("Payload/") and name.endswith(".app/Info.plist") and len(PurePosixPath(name).parts) == 3]
        require(len(candidates) == 1, "Expected exactly one top-level iOS app")
        prefix = candidates[0][:-len("Info.plist")]
        info = plistlib.loads(archive.read(prefix + "Info.plist"))
        require(info.get("CFBundleIdentifier") == manifest["bundle_id"], "Signed app bundle ID mismatch")
        require(info.get("CFBundleShortVersionString") == manifest["marketing_version"], "Marketing version mismatch")
        require(str(info.get("CFBundleVersion")) == build_number, "Build number mismatch")
        verify_payload(archive.read(prefix + "public/static/app.js"), archive.read(prefix + "public/index.html"), json.loads(archive.read(prefix + "capacitor.config.json")), manifest)
        if signed:
            require(sys.platform == "darwin", "Full signing verification requires macOS")
            for name in archive.namelist():
                require(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts, "Unsafe archive path")
            with tempfile.TemporaryDirectory(prefix="schoolhop-ipa-check-") as temporary:
                # Keep executable modes and framework symlinks for codesign.
                command(["unzip", "-q", str(ipa_path.resolve()), "-d", temporary])
                app = Path(temporary) / prefix.rstrip("/")
                command(["codesign", "--verify", "--deep", "--strict", str(app)])
                details = command(["codesign", "--display", "--verbose=4", str(app)])[1].decode()
                require("TeamIdentifier=" + manifest["team_id"] in details, "Code signing team mismatch")
                require("Authority=Apple Distribution:" in details, "Expected Apple Distribution signing identity")
                entitlements = plistlib.loads(command(["codesign", "-d", "--entitlements", ":-", str(app)])[0])
                profile = plistlib.loads(command(["security", "cms", "-D", "-i", str(app / "embedded.mobileprovision")])[0])
                application_id = manifest["team_id"] + "." + manifest["bundle_id"]
                require(entitlements.get("aps-environment") == "production", "Signed app requires production APNs")
                require(entitlements.get("com.apple.developer.team-identifier") == manifest["team_id"], "Entitlement team mismatch")
                require(entitlements.get("application-identifier") == application_id, "Application identifier mismatch")
                require(entitlements.get("get-task-allow") is not True, "Debug entitlement is not allowed")
                require(profile.get("TeamIdentifier") == [manifest["team_id"]], "Profile team mismatch")
                profile_entitlements = profile.get("Entitlements", {})
                require(profile_entitlements.get("application-identifier") == application_id, "Profile application ID mismatch")
                require(profile_entitlements.get("aps-environment") == "production", "Profile requires production APNs")
                require(profile_entitlements.get("get-task-allow") is not True, "Debug profile is not allowed")
                require(not profile.get("ProvisionedDevices") and not profile.get("ProvisionsAllDevices"), "Expected an App Store distribution profile")
                require(profile["ExpirationDate"].replace(tzinfo=timezone.utc) > datetime.now(timezone.utc), "Provisioning profile expired")
    return {"ipa_sha256": digest(ipa_path.read_bytes()), "bundle_id": manifest["bundle_id"], "team_id": manifest["team_id"], "marketing_version": manifest["marketing_version"], "build_number": build_number, "signed_verification": signed, "bundled_app_js_sha256": manifest["files"]["app/static/app.js"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("source", "web", "payload", "signed"), required=True)
    parser.add_argument("--expected-commit", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--ipa", type=Path)
    parser.add_argument("--build-number", default=os.environ.get("BUILD_NUMBER", ""))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    manifest = json.loads(MANIFEST_PATH.read_text())
    if args.phase == "source":
        report = verify_source(manifest, args.expected_commit)
    elif args.phase == "web":
        report = verify_web(manifest)
    else:
        require(args.ipa is not None and args.build_number.isdigit(), "IPA path and numeric build number are required")
        report = verify_ipa(args.ipa, manifest, args.build_number, args.phase == "signed")
    report["phase"] = args.phase
    report["reviewed_source_commit"] = manifest["reviewed_source_commit"]
    report["commit"] = command(["git", "rev-parse", "HEAD"])[0].decode().strip()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        print(f"iOS release verification failed: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
