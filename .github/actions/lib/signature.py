"""The signature of a release's ``SHA256SUMS`` (ADR-015 §3.2): ``SHA256SUMS.sig``, read and checked.

``SHA256SUMS.sig`` is text, one line per signature::

    ed25519 <key id> <signature, base64>

over the exact bytes of ``SHA256SUMS``. A line with another algorithm, or an id that names no key
given, is skipped; the file is valid when one line verifies with a key given. The keys are the
release keys file's (``cli/cortex_cli/release_keys.py`` in Cortex): read as text, with ``ast``, never
imported — the guard reads it at a tag's commit, through the API.

Signing and verifying are OpenSSL's, ``openssl pkeyutl -rawin`` (3.0 or later), which GitHub's
Ubuntu runners have. A raw public key becomes a PEM through the fixed DER prefix of an Ed25519
``SubjectPublicKeyInfo``. Standard library only, and ``openssl``.
"""

from __future__ import annotations

import ast
import base64
import binascii
import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

SIG = "SHA256SUMS.sig"
ALGORITHM = "ed25519"
# An Ed25519 SubjectPublicKeyInfo is this prefix, then the 32 bytes of the key (RFC 8410).
SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


class SignatureError(Exception):
    pass


def key_id(public: bytes) -> str:
    """A key's id: the first 16 hexadecimal digits of the SHA-256 of its 32-byte public key."""
    return hashlib.sha256(public).hexdigest()[:16]


def read_keys(text: str) -> Dict[str, Tuple[str, bytes]]:
    """``{id: (role, raw public key)}`` of a release keys file's ``KEYS``, read as text. A file that
    holds no such literal, or a key that is not 32 bytes of base64, is refused."""
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        raise SignatureError(f"the release keys file does not read: {error}") from None
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == "KEYS" for t in targets) and node.value is not None:
            try:
                literal = ast.literal_eval(node.value)
            except ValueError:
                raise SignatureError("the release keys file's KEYS is no literal") from None
            break
    else:
        raise SignatureError("the release keys file has no KEYS")
    keys: Dict[str, Tuple[str, bytes]] = {}
    if not isinstance(literal, dict):
        raise SignatureError("the release keys file's KEYS is no mapping")
    for kid, value in literal.items():
        try:
            role, encoded = value
            raw = base64.b64decode(encoded, validate=True)
        except (TypeError, ValueError, binascii.Error):
            raise SignatureError(f"the release key {kid} is not (role, base64)") from None
        if len(raw) != 32:
            raise SignatureError(f"the release key {kid} is {len(raw)} bytes, not 32")
        if key_id(raw) != kid:
            raise SignatureError(f"the release key {kid} is named {kid}, and its id is {key_id(raw)}")
        keys[kid] = (role, raw)
    return keys


def pem(public: bytes) -> str:
    """The PEM of a raw Ed25519 public key, as ``openssl`` reads it."""
    der = base64.b64encode(SPKI_PREFIX + public).decode()
    return f"-----BEGIN PUBLIC KEY-----\n{der}\n-----END PUBLIC KEY-----\n"


def openssl_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Whether ``signature`` is ``public``'s over ``message``, as ``openssl pkeyutl -verify`` says."""
    with tempfile.TemporaryDirectory(prefix="release-signature-") as tmp:
        key, data, sig = Path(tmp, "key.pem"), Path(tmp, "message"), Path(tmp, "sig")
        key.write_text(pem(public), encoding="ascii")
        data.write_bytes(message)
        sig.write_bytes(signature)
        done = subprocess.run(["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", str(key),
                               "-in", str(data), "-sigfile", str(sig)], capture_output=True, text=True)
    return done.returncode == 0


Verify = Callable[[bytes, bytes, bytes], bool]


@dataclass
class Checked:
    """What a ``SHA256SUMS.sig`` says of a ``SHA256SUMS``: the id of the key whose line verifies it,
    or why none does."""
    key: Optional[str]
    why: str = ""


def check(sums: bytes, sig_text: str, keys: Dict[str, Tuple[str, bytes]], verify: Verify = openssl_verify) -> Checked:
    """The first line of ``sig_text`` that verifies ``sums`` with a key of ``keys``; why none does
    otherwise — no line it can read, lines by no key given, or lines that do not match."""
    unknown: List[str] = []
    failed: List[str] = []
    for line in sig_text.splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[0] != ALGORITHM:
            continue
        _, kid, encoded = fields
        if kid not in keys:
            unknown.append(kid)
            continue
        try:
            signature = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            failed.append(kid)
            continue
        if len(signature) == 64 and verify(keys[kid][1], sums, signature):
            return Checked(kid)
        failed.append(kid)
    if failed:
        return Checked(None, f"its signature by {', '.join(failed)} does not match SHA256SUMS")
    if unknown:
        return Checked(None, f"it is signed by no release key: {', '.join(unknown)}")
    return Checked(None, "it holds no ed25519 signature")


def public_of(private_pem: Path) -> bytes:
    """The raw public key of an Ed25519 private key in PEM: the last 32 bytes of its DER public half."""
    done = subprocess.run(["openssl", "pkey", "-in", str(private_pem), "-pubout", "-outform", "DER"],
                          capture_output=True)
    der = done.stdout
    if done.returncode != 0 or len(der) != len(SPKI_PREFIX) + 32 or not der.startswith(SPKI_PREFIX):
        raise SignatureError("the signing key is no Ed25519 private key in PEM")
    return der[len(SPKI_PREFIX):]


def sign(private_pem: Path, sums: Path) -> Tuple[str, bytes]:
    """The id of the key in ``private_pem``, and its signature over ``sums``, made by
    ``openssl pkeyutl -sign -rawin``. Nothing of the key is printed."""
    public = public_of(private_pem)
    done = subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(private_pem), "-in", str(sums)],
                          capture_output=True)
    if done.returncode != 0 or len(done.stdout) != 64:
        raise SignatureError("openssl could not sign SHA256SUMS with the signing key")
    return key_id(public), done.stdout


def line(kid: str, signature: bytes) -> str:
    return f"{ALGORITHM} {kid} {base64.b64encode(signature).decode()}\n"


def write_private(key_text: str, directory: Path) -> Path:
    """``key_text`` in a new file of ``directory`` that only this user may read: ``mkstemp`` creates
    it 0600, before a byte is written."""
    fd, name = tempfile.mkstemp(prefix="release-signing-", suffix=".pem", dir=directory)
    try:
        os.write(fd, (key_text if key_text.endswith("\n") else key_text + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return Path(name)
