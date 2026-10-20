"""Security boundaries for Android-only signing and distributable APK checks."""

import base64
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import android_debug_signing as signing


class AndroidDebugSigningTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.certificate = b"public test certificate"
        self.fingerprint = hashlib.sha256(self.certificate).hexdigest()
        self.env = {
            "RUNNER_TEMP": str(self.root),
            "ANDROID_DEBUG_CERT_SHA256": self.fingerprint,
            "ANDROID_DEBUG_KEYSTORE_BASE64": base64.b64encode(b"test store").decode(),
            "ANDROID_DEBUG_KEYSTORE_PASSWORD": "not-a-real-store-password",
            "ANDROID_DEBUG_KEY_ALIAS": "test-alias",
            "ANDROID_DEBUG_KEY_PASSWORD": "not-a-real-key-password",
            "ANDROID_VERSION_CODE": "9",
            "ANDROID_VERSION_NAME": "1.0.25",
            "ANDROID_REQUIRE_FIREBASE": "true",
        }
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        quiet = patch("builtins.print")
        quiet.start()
        self.addCleanup(quiet.stop)

    def test_stage_requires_each_credential_and_pin_before_writing(self):
        for name in [key for key in self.env if key.startswith("ANDROID_DEBUG_")]:
            with self.subTest(name=name), patch.dict(os.environ, {name: ""}):
                with self.assertRaises(signing.SigningError):
                    signing.stage()
                self.assertFalse((self.root / "schoolhop-debug-signing").exists())

    def test_invalid_keystore_encoding_is_rejected(self):
        with patch.dict(os.environ, {"ANDROID_DEBUG_KEYSTORE_BASE64": "invalid!"}):
            with self.assertRaisesRegex(signing.SigningError, "encoding"):
                signing.stage()

    def test_stage_permissions_and_password_not_in_tool_arguments(self):
        with patch.object(signing, "capture", return_value=self.certificate) as capture:
            signing.stage()
        target = self.root / "schoolhop-debug-signing/debug-signing.p12"
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode), 0o700)
        self.assertEqual(target.read_bytes(), b"test store")
        args = capture.call_args.args[0]
        self.assertIn("-storepass:env", args)
        for name in ["ANDROID_DEBUG_KEYSTORE_PASSWORD", "ANDROID_DEBUG_KEY_PASSWORD"]:
            self.assertNotIn(self.env[name], args)

    def test_wrong_keystore_certificate_is_removed(self):
        with patch.object(signing, "capture", return_value=b"wrong certificate"):
            with self.assertRaisesRegex(signing.SigningError, "pinned fingerprint"):
                signing.stage()
        self.assertFalse((self.root / "schoolhop-debug-signing").exists())

    def test_tool_failure_is_sanitized_and_staged_key_is_removed(self):
        with patch.object(signing, "capture", side_effect=signing.SigningError("tool failed")):
            with self.assertRaises(signing.SigningError):
                signing.stage()
        self.assertFalse((self.root / "schoolhop-debug-signing").exists())

    def test_refuses_to_overwrite_an_existing_signing_directory(self):
        directory = self.root / "schoolhop-debug-signing"
        directory.mkdir()
        marker = directory / "keep"
        marker.write_text("existing")
        with self.assertRaises(FileExistsError):
            signing.stage()
        self.assertEqual(marker.read_text(), "existing")

    def test_subprocess_failure_does_not_disclose_tool_output(self):
        result = signing.subprocess.CompletedProcess([], 1, b"sensitive stdout", b"sensitive stderr")
        with patch.object(signing.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(signing.SigningError, "output withheld") as error:
                signing.capture(["keytool"])
        self.assertNotIn("sensitive", str(error.exception))

    def make_apk(self, url="https://schoolhop.shourish.com", frontend=None):
        target = self.root / "app-debug.apk"
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("assets/public/index.html", 'apiBaseUrl: "' + url + '"')
            archive.writestr("assets/public/static/app.js", frontend if frontend is not None else Path("app/static/app.js").read_bytes())
        return target

    def verify(self, signer=None, package="com.schoolhop.app", code="9", name="1.0.25", resources=None, **apk_options):
        apk = self.make_apk(**apk_options)
        inspection = [
            signer if signer is not None else f"Signer #1 certificate SHA-256 digest: {self.fingerprint}\n".encode(),
            f"package: name='{package}' versionCode='{code}' versionName='{name}'".encode(),
            resources if resources is not None else b"schoolhop-1c00d string/google_app_id string/gcm_defaultSenderId",
        ]
        with patch.object(signing, "capture", side_effect=inspection):
            signing.verify_apk(apk, Path("/sdk/build-tools/35.0.0"))

    def test_accepts_expected_signed_production_apk(self):
        self.verify()

    def test_rejects_wrong_or_multiple_apk_signers(self):
        for signer in [b"Signer #1 certificate SHA-256 digest: " + b"0" * 64 + b"\n",
                       (f"Signer #1 certificate SHA-256 digest: {self.fingerprint}\n" * 2).encode()]:
            with self.subTest(signer=signer), self.assertRaisesRegex(signing.SigningError, "signer"):
                self.verify(signer=signer)

    def test_rejects_wrong_package_or_versions(self):
        for changes in [{"package": "other.app"}, {"code": "7"}, {"name": "wrong"}]:
            with self.subTest(changes=changes), self.assertRaisesRegex(signing.SigningError, "package or version"):
                self.verify(**changes)

    def test_rejects_invalid_requested_version_codes(self):
        for value in ["7", "8", "09", "0", "-1", "2100000001", "invalid"]:
            with self.subTest(value=value), patch.dict(os.environ, {"ANDROID_VERSION_CODE": value}):
                with self.assertRaisesRegex(signing.SigningError, "versionCode"):
                    self.verify()

    def test_rejects_changed_backend_url(self):
        with self.assertRaisesRegex(signing.SigningError, "backend URL"):
            self.verify(url="https://other.example")

    def test_rejects_stale_packaged_frontend(self):
        with self.assertRaisesRegex(signing.SigningError, "reviewed source"):
            self.verify(frontend=b"old frontend")

    def test_rejects_missing_firebase_resources(self):
        with self.assertRaisesRegex(signing.SigningError, "Firebase"):
            self.verify(resources=b"no firebase")


if __name__ == "__main__":
    unittest.main()
