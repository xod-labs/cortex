"""SHA256SUMS.sig, read and checked (ADR-015 §3.2), with keys made for the test by openssl."""

import base64
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import signature as sg  # noqa: E402

SUMS = b"0" * 64 + b"  cortex-linux-x86_64.tar.gz\n"


def new_key(directory):
    """A new Ed25519 private key in ``directory``: its path, and its raw public key."""
    path = Path(tempfile.mkstemp(prefix="key-", suffix=".pem", dir=directory)[1])
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(path)], check=True, capture_output=True)
    return path, sg.public_of(path)


def keys_file(**roles):
    """A release keys file: ``primary=raw, backup=raw``."""
    entries = "".join(f'    "{sg.key_id(raw)}": ("{role}", "{base64.b64encode(raw).decode()}"),\n' for role, raw in roles.items())
    return f'"""Keys."""\n\nfrom typing import Dict, Tuple\n\nKEYS: Dict[str, Tuple[str, str]] = {{\n{entries}}}\n'


class SignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="signature-tests-")
        cls.primary, cls.primary_raw = new_key(cls.tmp.name)
        cls.backup, cls.backup_raw = new_key(cls.tmp.name)
        cls.other, cls.other_raw = new_key(cls.tmp.name)
        cls.keys = sg.read_keys(keys_file(primary=cls.primary_raw, backup=cls.backup_raw))
        sums = Path(cls.tmp.name, "SHA256SUMS")
        sums.write_bytes(SUMS)
        cls.signed = {raw: sg.sign(path, sums) for path, raw in ((cls.primary, cls.primary_raw), (cls.backup, cls.backup_raw),
                                                                 (cls.other, cls.other_raw))}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def line(self, raw):
        return sg.line(*self.signed[raw])

    def test_a_key_s_id(self):
        self.assertEqual(sg.key_id(self.primary_raw), hashlib.sha256(self.primary_raw).hexdigest()[:16])
        self.assertEqual(self.signed[self.primary_raw][0], sg.key_id(self.primary_raw))

    def test_the_pem_of_a_raw_key_is_the_one_openssl_derives(self):
        derived = subprocess.run(["openssl", "pkey", "-in", str(self.primary), "-pubout"], check=True,
                                 capture_output=True, text=True).stdout
        self.assertEqual(sg.pem(self.primary_raw), derived)

    def test_a_line_s_format(self):
        kid, signature = self.signed[self.primary_raw]
        self.assertRegex(sg.line(kid, signature), r"^ed25519 [0-9a-f]{16} [A-Za-z0-9+/]{86}==\n$")

    def test_a_line_by_a_key_given_verifies(self):
        for raw in (self.primary_raw, self.backup_raw):
            self.assertEqual(sg.check(SUMS, self.line(raw), self.keys).key, sg.key_id(raw))

    def test_sums_changed_after_signing_do_not_verify(self):
        checked = sg.check(SUMS + b"\n", self.line(self.primary_raw), self.keys)
        self.assertIsNone(checked.key)
        self.assertEqual(checked.why, f"its signature by {sg.key_id(self.primary_raw)} does not match SHA256SUMS")

    def test_a_changed_signature_does_not_verify(self):
        kid, signature = self.signed[self.primary_raw]
        changed = bytes([signature[0] ^ 1]) + signature[1:]
        self.assertIsNone(sg.check(SUMS, sg.line(kid, changed), self.keys).key)

    def test_a_signature_by_no_key_given(self):
        checked = sg.check(SUMS, self.line(self.other_raw), self.keys)
        self.assertEqual((checked.key, checked.why), (None, f"it is signed by no release key: {sg.key_id(self.other_raw)}"))

    def test_lines_skipped_and_one_that_verifies(self):
        text = ("rsa 0123456789abcdef AAAA\n" + "garbage\n" + self.line(self.other_raw) + "\n"
                + self.line(self.backup_raw))
        self.assertEqual(sg.check(SUMS, text, self.keys).key, sg.key_id(self.backup_raw))

    def test_nothing_to_read(self):
        for text in ("", "rsa 0123456789abcdef AAAA\n", "ed25519 only-two\n"):
            with self.subTest(text=text):
                self.assertEqual(sg.check(SUMS, text, self.keys).why, "it holds no ed25519 signature")

    def test_a_signature_that_is_no_base64_or_not_64_bytes(self):
        kid = sg.key_id(self.primary_raw)
        for encoded in ("!!!", base64.b64encode(b"x" * 63).decode()):
            with self.subTest(encoded=encoded):
                self.assertEqual(sg.check(SUMS, f"ed25519 {kid} {encoded}\n", self.keys).why,
                                 f"its signature by {kid} does not match SHA256SUMS")

    def test_the_keys_file_is_read_as_text(self):
        self.assertEqual(self.keys, {sg.key_id(self.primary_raw): ("primary", self.primary_raw),
                                     sg.key_id(self.backup_raw): ("backup", self.backup_raw)})
        self.assertEqual(sg.read_keys("KEYS = {}\n"), {})

    def test_a_keys_file_that_does_not_hold_keys_is_refused(self):
        raw = self.primary_raw
        cases = {"no KEYS": "OTHER = {}\n",
                 "no literal": "KEYS = dict()\n",
                 "no mapping": "KEYS = []\n",
                 "does not read": "KEYS = {\n",
                 "is 31 bytes": f'KEYS = {{"{sg.key_id(raw[:31])}": ("primary", "{base64.b64encode(raw[:31]).decode()}")}}\n',
                 f"its id is {sg.key_id(raw)}": f'KEYS = {{"{"0" * 16}": ("primary", "{base64.b64encode(raw).decode()}")}}\n',
                 "not (role, base64)": f'KEYS = {{"{sg.key_id(raw)}": "{base64.b64encode(raw).decode()}"}}\n'}
        for why, text in cases.items():
            with self.subTest(why=why):
                with self.assertRaisesRegex(sg.SignatureError, why.replace("(", r"\(").replace(")", r"\)")):
                    sg.read_keys(text)

    def test_the_private_key_file_is_this_user_s_only(self):
        if not hasattr(__import__("os"), "geteuid"):
            self.skipTest("POSIX modes")
        path = sg.write_private("-----BEGIN PRIVATE KEY-----", Path(self.tmp.name))
        try:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_text(), "-----BEGIN PRIVATE KEY-----\n")
        finally:
            path.unlink()

    def test_a_file_that_is_no_key_is_not_signed_with(self):
        sums = Path(self.tmp.name, "SHA256SUMS")
        not_a_key = Path(self.tmp.name, "not-a-key.pem")
        not_a_key.write_text("not a key\n")
        with self.assertRaisesRegex(sg.SignatureError, "no Ed25519 private key"):
            sg.sign(not_a_key, sums)


if __name__ == "__main__":
    unittest.main()
