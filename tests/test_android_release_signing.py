"""Exercise release verification against real valid, tampered and unsigned JARs."""
import base64
import hashlib
import importlib.util
import os
from pathlib import Path
import secrets
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_signing", ROOT / "scripts/android_release_signing.py")
signing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signing)


class ReleaseSigningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="schoolhop-test-signing-")
        cls.root = Path(cls.temporary.name)
        cls.key = cls.root / "test-only.p12"
        cls.env = dict(os.environ, TEST_ONLY_KEY_PASSWORD=secrets.token_urlsafe(24))
        def run(args):
            return subprocess.run(args, env=cls.env, check=True, capture_output=True).stdout
        run(["keytool", "-genkeypair", "-keystore", str(cls.key), "-storetype", "PKCS12",
             "-alias", "test-only", "-keyalg", "RSA", "-keysize", "2048", "-validity", "2",
             "-dname", "CN=Ephemeral Test Only", "-storepass:env", "TEST_ONLY_KEY_PASSWORD"])
        cert = run(["keytool", "-exportcert", "-keystore", str(cls.key), "-alias", "test-only",
                    "-storepass:env", "TEST_ONLY_KEY_PASSWORD"])
        cls.expected = hashlib.sha256(cert).hexdigest()
        cls.env["ANDROID_UPLOAD_CERT_SHA256"] = cls.expected
        cls.signed = cls.root / "signed.aab"
        with zipfile.ZipFile(cls.signed, "w") as archive:
            archive.writestr("base/assets/app.js", "reviewed frontend")
        run(["jarsigner", "-keystore", str(cls.key), "-storepass:env", "TEST_ONLY_KEY_PASSWORD",
             str(cls.signed), "test-only"])
        run(["javac", "-d", str(cls.root), str(ROOT / "scripts/VerifyAndroidBundle.java")])

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def verify(self, path, expected=None):
        env = dict(self.env, ANDROID_UPLOAD_CERT_SHA256=expected or self.expected)
        return subprocess.run(["java", "-cp", str(self.root), "VerifyAndroidBundle", str(path)],
                              env=env, capture_output=True).returncode

    def test_accepts_payload_signed_by_pinned_key(self):
        self.assertEqual(self.verify(self.signed), 0)

    def test_rejects_different_certificate(self):
        self.assertNotEqual(self.verify(self.signed, "0" * 64), 0)

    def test_rejects_modified_signed_payload(self):
        changed = self.root / "changed.aab"
        with zipfile.ZipFile(self.signed) as source, zipfile.ZipFile(changed, "w") as target:
            for entry in source.infolist():
                target.writestr(entry, b"changed" if entry.filename == "base/assets/app.js" else source.read(entry))
        self.assertNotEqual(self.verify(changed), 0)

    def test_rejects_unsigned_addition(self):
        changed = self.root / "added.aab"
        changed.write_bytes(self.signed.read_bytes())
        with zipfile.ZipFile(changed, "a") as target:
            target.writestr("base/assets/injected.js", "unsigned")
        self.assertNotEqual(self.verify(changed), 0)

    def test_rejects_unsigned_bundle(self):
        unsigned = self.root / "unsigned.aab"
        with zipfile.ZipFile(unsigned, "w") as target:
            target.writestr("base/assets/app.js", "unsigned")
        self.assertNotEqual(self.verify(unsigned), 0)

    def stage_env(self, directory):
        return dict(ANDROID_UPLOAD_CERT_SHA256=self.expected, ANDROID_DEBUG_CERT_SHA256="0" * 64,
                    ANDROID_KEYSTORE_BASE64=base64.b64encode(self.key.read_bytes()).decode(),
                    ANDROID_KEYSTORE_PASSWORD=self.env["TEST_ONLY_KEY_PASSWORD"],
                    ANDROID_KEY_PASSWORD=self.env["TEST_ONLY_KEY_PASSWORD"],
                    ANDROID_KEY_ALIAS="test-only", RUNNER_TEMP=str(directory))

    def test_stage_restricts_permissions(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, self.stage_env(directory)):
            signing.stage()
            root = Path(directory) / "schoolhop-upload-signing"
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((root / "upload-signing.p12").stat().st_mode), 0o600)

    def test_stage_wrong_pin_removes_private_material(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.stage_env(directory)
            env["ANDROID_UPLOAD_CERT_SHA256"] = "1" * 64
            with patch.dict(os.environ, env), self.assertRaises(ValueError):
                signing.stage()
            self.assertFalse((Path(directory) / "schoolhop-upload-signing").exists())

    def test_rejects_debug_identity(self):
        with patch.dict(os.environ, {"ANDROID_UPLOAD_CERT_SHA256": self.expected,
                                     "ANDROID_DEBUG_CERT_SHA256": self.expected}):
            with self.assertRaises(ValueError):
                signing.fingerprint()


if __name__ == "__main__":
    unittest.main()
