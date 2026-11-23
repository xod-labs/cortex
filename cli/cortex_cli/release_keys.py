"""The public keys that sign a release's ``SHA256SUMS`` (ADR-015 §3.1).

Each key is named by its id — the first 16 hexadecimal digits of the SHA-256 of its 32-byte public
key — and given with its role, ``primary`` or ``backup``, and the key itself, the raw 32 bytes of an
Ed25519 public key in base64. A binary embeds this file as the commit it is built from has it:
``cortex self-update`` installs a release from 1.10.0 on only when one line of its
``SHA256SUMS.sig`` verifies with one of these keys (§3.5). The release workflow checks its own
signature against the primary key here before it drafts anything, and the guard checks a published
release against this file at its tag's commit (§3.3, §3.4).

``KEYS`` stays a literal: the release actions read it as text (``ast``), without importing it.
Tests never use these keys: they are given their own.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Dict, Tuple

# id -> (role, the raw 32-byte public key, base64).
# Empty until the maintainer generates the primary and the backup key and commits their public
# halves here (ADR-015 §3.1, §4 phase 1). Until then, no release can be drafted: the release
# workflow finds no primary key to check its signature against.
KEYS: Dict[str, Tuple[str, str]] = {}

ROLES = ("primary", "backup")


def key_id(public: bytes) -> str:
    """The id of a raw 32-byte public key: the first 16 hexadecimal digits of its SHA-256."""
    return hashlib.sha256(public).hexdigest()[:16]


def public_keys(keys: Dict[str, Tuple[str, str]]) -> Dict[str, bytes]:
    """``{id: raw public key}`` of a mapping shaped as ``KEYS``."""
    return {kid: base64.b64decode(encoded, validate=True) for kid, (_, encoded) in keys.items()}
