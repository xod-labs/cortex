"""The Ed25519 verifier (ADR-015 §3.5): RFC 8032 §7.1's vectors, OpenSSL's signatures, and the
signatures and encodings it must refuse."""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the harness, not a package named tests
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]
import ed25519_signer as signer  # noqa: E402
from cortex_cli import ed25519  # noqa: E402

VECTORS = json.loads((Path(__file__).resolve().parent / "fixtures" / "ed25519" / "rfc8032-7.1.json")
                     .read_text(encoding="utf-8"))["vectors"]
# An Ed25519 private key in PKCS #8 DER is this prefix, then its 32-byte secret (RFC 8410).
PKCS8_PREFIX = bytes.fromhex("302e020100300506032b657004220420")
SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


def vector(v):
    return tuple(bytes.fromhex(v[k]) for k in ("secret", "public", "message", "signature"))


def openssl_with_ed25519():
    """``openssl`` when it signs Ed25519 raw — OpenSSL 3; LibreSSL, macOS's own, does not."""
    path = shutil.which("openssl")
    if path is None:
        return None
    version = subprocess.run([path, "version"], capture_output=True, text=True).stdout
    return path if version.startswith("OpenSSL ") and not version.startswith(("OpenSSL 0.", "OpenSSL 1.")) else None


OPENSSL = openssl_with_ed25519()


def pem(prefix, raw, kind):
    return f"-----BEGIN {kind}-----\n{base64.b64encode(prefix + raw).decode()}\n-----END {kind}-----\n"


class VectorTests(unittest.TestCase):
    def test_the_vectors_are_rfc_8032_s_five(self):
        self.assertEqual([v["name"] for v in VECTORS], ["1", "2", "3", "1024", "SHA(abc)"])
        self.assertEqual([len(v["message"]) // 2 for v in VECTORS], [0, 1, 2, 1023, 64])

    def test_each_vector_verifies(self):
        for v in VECTORS:
            _, public, message, signature = vector(v)
            with self.subTest(test=v["name"]):
                self.assertTrue(ed25519.verify(public, message, signature))

    def test_the_test_signer_gives_each_vector_s_key_and_signature(self):
        for v in VECTORS:
            secret, public, message, signature = vector(v)
            with self.subTest(test=v["name"]):
                self.assertEqual(signer.public_key(secret), public)
                self.assertEqual(signer.sign(secret, message), signature)

    def test_a_verification_takes_well_under_a_second(self):
        _, public, message, signature = vector(VECTORS[3])
        start = time.perf_counter()
        for _ in range(5):
            self.assertTrue(ed25519.verify(public, message, signature))
        self.assertLess((time.perf_counter() - start) / 5, 0.25)


@unittest.skipUnless(OPENSSL, "OpenSSL 3, which GitHub's Linux and Windows runners have")
class OpenSSLTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ed25519-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def openssl(self, *args):
        return subprocess.run([OPENSSL, *args], check=True, capture_output=True).stdout

    def test_openssl_signs_each_vector_as_the_rfc_says(self):
        # pkeyutl reads no empty input: TEST 1, whose message is empty, is the RFC's alone.
        for v in VECTORS[1:]:
            secret, _, message, signature = vector(v)
            with self.subTest(test=v["name"]):
                (self.tmp / "key.pem").write_text(pem(PKCS8_PREFIX, secret, "PRIVATE KEY"))
                (self.tmp / "message").write_bytes(message)
                self.assertEqual(self.openssl("pkeyutl", "-sign", "-rawin", "-inkey", str(self.tmp / "key.pem"),
                                              "-in", str(self.tmp / "message")), signature)

    def test_a_signature_openssl_makes_with_a_new_key_verifies(self):
        key = self.tmp / "new.pem"
        self.openssl("genpkey", "-algorithm", "ed25519", "-out", str(key))
        public = self.openssl("pkey", "-in", str(key), "-pubout", "-outform", "DER")[len(SPKI_PREFIX):]
        message = os.urandom(300)
        (self.tmp / "message").write_bytes(message)
        signature = self.openssl("pkeyutl", "-sign", "-rawin", "-inkey", str(key), "-in", str(self.tmp / "message"))
        self.assertTrue(ed25519.verify(public, message, signature))
        self.assertFalse(ed25519.verify(public, message + b"x", signature))


class RefusalTests(unittest.TestCase):
    """What ADR-015 §3.5 names: a changed message, a changed signature, an S not below the group
    order, a point not on the curve — and encodings that are not canonical, and wrong lengths."""

    def setUp(self):
        _, self.public, self.message, self.signature = vector(VECTORS[2])

    def test_a_changed_message(self):
        self.assertFalse(ed25519.verify(self.public, self.message + b"\x00", self.signature))
        self.assertFalse(ed25519.verify(self.public, b"\xaf\x83", self.signature))

    def test_a_changed_signature(self):
        for index in (0, 31, 32, 63):
            changed = bytearray(self.signature)
            changed[index] ^= 0x01
            with self.subTest(byte=index):
                self.assertFalse(ed25519.verify(self.public, self.message, bytes(changed)))

    def test_another_key(self):
        _, other, _, _ = vector(VECTORS[0])
        self.assertFalse(ed25519.verify(other, self.message, self.signature))

    def test_an_s_not_below_the_group_order(self):
        # S + L verifies the same group equation: only the bound refuses it.
        s = int.from_bytes(self.signature[32:], "little")
        malleated = self.signature[:32] + (s + ed25519.L).to_bytes(32, "little")
        self.assertFalse(ed25519.verify(self.public, self.message, malleated))
        at_l = self.signature[:32] + ed25519.L.to_bytes(32, "little")
        self.assertFalse(ed25519.verify(self.public, self.message, at_l))

    def test_a_point_not_on_the_curve(self):
        # y = 2 has no x on the curve.
        off_curve = (2).to_bytes(32, "little")
        self.assertIsNone(ed25519.decode_point(off_curve))
        self.assertFalse(ed25519.verify(off_curve, self.message, self.signature))
        self.assertFalse(ed25519.verify(self.public, self.message, off_curve + self.signature[32:]))

    def test_points_not_encoded_canonically(self):
        # The identity, y = 1 and x = 0: as y + p, and with the sign bit of an x of 0.
        canonical = (1).to_bytes(32, "little")
        self.assertIsNotNone(ed25519.decode_point(canonical))
        for name, encoding in (("y + p", (ed25519.P + 1).to_bytes(32, "little")),
                               ("x of 0, signed", (1 | 1 << 255).to_bytes(32, "little")),
                               ("y = p", ed25519.P.to_bytes(32, "little"))):
            with self.subTest(encoding=name):
                self.assertIsNone(ed25519.decode_point(encoding))
                self.assertFalse(ed25519.verify(encoding, self.message, self.signature))
                self.assertFalse(ed25519.verify(self.public, self.message, encoding + self.signature[32:]))

    def test_wrong_lengths(self):
        for public, signature in ((self.public[:31], self.signature), (self.public + b"\x00", self.signature),
                                  (self.public, self.signature[:63]), (self.public, self.signature + b"\x00"),
                                  (b"", b"")):
            with self.subTest(public=len(public), signature=len(signature)):
                self.assertFalse(ed25519.verify(public, self.message, signature))

    def test_the_points_decoded_are_the_points_encoded(self):
        for v in VECTORS:
            _, public, _, signature = vector(v)
            with self.subTest(test=v["name"]):
                self.assertEqual(ed25519.encode_point(ed25519.decode_point(public)), public)
                self.assertEqual(ed25519.encode_point(ed25519.decode_point(signature[:32])), signature[:32])

    def test_the_module_signs_nothing(self):
        source = Path(ed25519.__file__).read_text(encoding="utf-8")
        self.assertNotIn("def sign", source)
        self.assertNotIn("sha512(secret", source)


if __name__ == "__main__":
    unittest.main()
