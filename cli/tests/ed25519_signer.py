"""Ed25519 signing for the tests only — RFC 8032 §5.1.5 and §5.1.6, on the verifier's arithmetic.

The command verifies and never signs (``cortex_cli.ed25519``); the tests sign the releases they
serve with key pairs of their own, made here from a 32-byte secret. Checked against RFC 8032 §7.1's
vectors, and against OpenSSL where it has Ed25519, by ``test_cli_ed25519``.
"""

import base64
import hashlib
import os

from cortex_cli import ed25519


def _expand(secret):
    digest = hashlib.sha512(secret).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def public_key(secret):
    """The 32-byte public key of a 32-byte secret."""
    scalar, _ = _expand(secret)
    return ed25519.encode_point(ed25519._multiply(scalar, ed25519.B))


def sign(secret, message):
    """The 64-byte signature of ``message`` by ``secret``."""
    scalar, prefix = _expand(secret)
    public = public_key(secret)
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % ed25519.L
    big_r = ed25519.encode_point(ed25519._multiply(r, ed25519.B))
    k = int.from_bytes(hashlib.sha512(big_r + public + message).digest(), "little") % ed25519.L
    s = (r + k * scalar) % ed25519.L
    return big_r + s.to_bytes(32, "little")


class KeyPair:
    """A release key made for a test: its secret, its public key and its id."""

    def __init__(self, secret=None):
        self.secret = secret if secret is not None else os.urandom(32)
        self.public = public_key(self.secret)
        self.id = hashlib.sha256(self.public).hexdigest()[:16]

    def sign(self, message):
        return sign(self.secret, message)

    def line(self, message):
        """A line of SHA256SUMS.sig: ``ed25519 <id> <base64>``."""
        return f"ed25519 {self.id} {base64.b64encode(self.sign(message)).decode()}\n"

    def keys(self, role="primary"):
        """This key as an entry of ``release_keys.KEYS``."""
        return {self.id: (role, base64.b64encode(self.public).decode())}
