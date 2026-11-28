"""The release's signature (ADR-015 §3.3): made with the secret, checked against the committed
primary key, and the key never left on disk nor printed."""

import base64
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "lib" / "tests"))
import sign  # noqa: E402
import signature as sg  # noqa: E402
from test_signature import keys_file, new_key  # noqa: E402

SUMS = b"1" * 64 + b"  cortex-spec.tar.gz\n"


class SignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys_dir = tempfile.TemporaryDirectory(prefix="sign-keys-")
        cls.primary, cls.primary_raw = new_key(cls.keys_dir.name)
        cls.backup, cls.backup_raw = new_key(cls.keys_dir.name)
        cls.other, _ = new_key(cls.keys_dir.name)

    @classmethod
    def tearDownClass(cls):
        cls.keys_dir.cleanup()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sign-")
        self.addCleanup(tmp.cleanup)
        self.assets, self.temp = Path(tmp.name, "assets"), Path(tmp.name, "runner-temp")
        self.assets.mkdir()
        self.temp.mkdir()
        (self.assets / "SHA256SUMS").write_bytes(SUMS)
        self.keys = Path(tmp.name, "release_keys.py")
        self.keys.write_text(keys_file(primary=self.primary_raw, backup=self.backup_raw), encoding="utf-8")

    def run_sign(self, key):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.dict(os.environ, {"RELEASE_SIGNING_KEY": key.read_text() if key else "",
                                                                "RUNNER_TEMP": str(self.temp)}):
            code = sign.main(["--assets", str(self.assets), "--keys", str(self.keys)])
        return code, out.getvalue()

    def assert_refused(self, key, why):
        code, out = self.run_sign(key)
        self.assertEqual(code, 1, out)
        self.assertIn(f"::error title=release signature::{why}", out)
        self.assertEqual(list(self.temp.iterdir()), [])   # the key file is gone
        return out

    def test_the_primary_key_signs_and_its_signature_is_checked(self):
        code, out = self.run_sign(self.primary)
        self.assertEqual(code, 0, out)
        kid = sg.key_id(self.primary_raw)
        self.assertIn(f"SHA256SUMS signed by the primary key {kid}", out)
        text = (self.assets / "SHA256SUMS.sig").read_text(encoding="ascii")
        self.assertRegex(text, rf"^ed25519 {kid} [A-Za-z0-9+/]{{86}}==\n$")
        # openssl verifies it with the committed key's PEM, built from its raw bytes.
        self.assertEqual(sg.check(SUMS, text, sg.read_keys(self.keys.read_text())).key, kid)
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_nothing_of_the_key_is_printed(self):
        captured = io.StringIO()
        with mock.patch.object(sys, "stderr", captured):
            code, out = self.run_sign(self.primary)
        body = "".join(self.primary.read_text().splitlines()[1:-1])
        self.assertEqual(code, 0)
        self.assertNotIn(body, out + captured.getvalue())

    def test_the_key_file_is_removed_when_openssl_fails(self):
        with mock.patch.object(sign, "sign", side_effect=sg.SignatureError("openssl could not sign")):
            self.assert_refused(self.primary, "openssl could not sign")

    def test_a_secret_that_is_no_committed_key_fails_before_any_draft(self):
        primary = sg.key_id(self.primary_raw)
        self.assert_refused(self.other, f"RELEASE_SIGNING_KEY does not match the primary key {primary} of {self.keys}: "
                                        "SHA256SUMS.sig it is signed by no release key")

    def test_the_backup_key_is_not_the_primary(self):
        self.assert_refused(self.backup, f"RELEASE_SIGNING_KEY is the backup key {sg.key_id(self.backup_raw)}, not the "
                                         f"primary key {sg.key_id(self.primary_raw)}")

    def test_an_empty_secret_says_where_it_comes_from(self):
        self.assert_refused(None, "RELEASE_SIGNING_KEY is empty: the job runs outside the release-signing environment, "
                                  "or its secret is not set — bin/setup-release.sh says how to set it")

    def test_a_keys_file_without_a_primary_key_signs_nothing(self):
        # The committed file, until the maintainer's keys are in it.
        self.keys.write_text("KEYS = {}\n", encoding="utf-8")
        self.assert_refused(self.primary, f"{self.keys} holds 0 primary key(s), and should hold one: no signature can be "
                                          "checked")
        self.assertFalse((self.assets / "SHA256SUMS.sig").exists())

    def test_a_keys_file_with_two_primary_keys_signs_nothing(self):
        two = "".join(f'    "{sg.key_id(raw)}": ("primary", "{base64.b64encode(raw).decode()}"),\n'
                      for raw in (self.primary_raw, self.backup_raw))
        self.keys.write_text(f"KEYS = {{\n{two}}}\n", encoding="utf-8")
        self.assertEqual([role for role, _ in sg.read_keys(self.keys.read_text()).values()], ["primary", "primary"])
        self.assert_refused(self.primary, f"{self.keys} holds 2 primary key(s), and should hold one: no signature can be "
                                          "checked")
        self.assertFalse((self.assets / "SHA256SUMS.sig").exists())

    def test_no_sums_to_sign(self):
        (self.assets / "SHA256SUMS").unlink()
        self.assert_refused(self.primary, f"there is no SHA256SUMS in {self.assets} to sign")


class OpenSSLTests(unittest.TestCase):
    def test_openssl_signs_ed25519_raw(self):
        # pkeyutl -rawin arrived with OpenSSL 3.0: GitHub's Ubuntu runners have it.
        version = subprocess.run(["openssl", "version"], capture_output=True, text=True).stdout
        self.assertRegex(version, r"^OpenSSL [3-9]\.")


if __name__ == "__main__":
    unittest.main()
