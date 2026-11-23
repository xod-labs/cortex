"""Ed25519 signature verification — RFC 8032 §5.1.7, in pure Python (ADR-015 §3.5).

``cortex self-update`` checks a release's ``SHA256SUMS.sig`` with it. The standard library verifies
no signature, and ``cryptography`` would add a native library to each binary. This module verifies
only: signing is the release workflow's, with OpenSSL, and nothing here can sign.

A public key and the two halves of a signature are decoded as RFC 8032 §5.1.3 says, and refused
when they are not canonical: a ``y`` not below ``p``, an ``x`` of 0 with its sign bit set, a point
not on the curve. ``S`` must be below the group order ``L``, and the lengths exact — 32 bytes for a
key, 64 for a signature. The group equation is the cofactored one, ``[8][S]B = [8]R + [8][k]A``,
which §5.1.7 names first. The arithmetic is not constant-time: a verification handles no secret.
"""

from __future__ import annotations

import hashlib
from typing import Optional, Tuple

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)

# A point in extended homogeneous coordinates (X, Y, Z, T), x = X/Z, y = Y/Z, x*y = T/Z (RFC 8032 §5.1.4).
Point = Tuple[int, int, int, int]
_IDENTITY: Point = (0, 1, 1, 0)


def _add(p: Point, q: Point) -> Point:
    """RFC 8032 §5.1.4's addition, complete: it doubles a point too."""
    a = (p[1] - p[0]) * (q[1] - q[0]) % P
    b = (p[1] + p[0]) * (q[1] + q[0]) % P
    c = 2 * p[3] * q[3] * D % P
    d = 2 * p[2] * q[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _double(p: Point) -> Point:
    """RFC 8032 §5.1.4's doubling."""
    a = p[0] * p[0] % P
    b = p[1] * p[1] % P
    c = 2 * p[2] * p[2] % P
    h = a + b
    e = h - (p[0] + p[1]) ** 2 % P
    g = a - b
    f = c + g
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _multiply(scalar: int, p: Point) -> Point:
    result = _IDENTITY
    while scalar:
        if scalar & 1:
            result = _add(result, p)
        p = _double(p)
        scalar >>= 1
    return result


def _equal(p: Point, q: Point) -> bool:
    return (p[0] * q[2] - q[0] * p[2]) % P == 0 and (p[1] * q[2] - q[1] * p[2]) % P == 0


def _recover_x(y: int, sign: int) -> Optional[int]:
    """The ``x`` of the point of ``y`` whose sign is ``sign`` — None when there is none (RFC 8032 §5.1.3)."""
    if y >= P:
        return None
    u = (y * y - 1) % P
    v = (D * y * y + 1) % P
    x = u * pow(v, 3, P) * pow(u * pow(v, 7, P), (P - 5) // 8, P) % P
    if (v * x * x - u) % P != 0:
        if (v * x * x + u) % P != 0:
            return None
        x = x * SQRT_M1 % P
    if x == 0 and sign:
        return None
    if x & 1 != sign:
        x = P - x
    return x


def decode_point(data: bytes) -> Optional[Point]:
    """The point 32 bytes encode, or None when they encode none, or not canonically."""
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def encode_point(p: Point) -> bytes:
    """A point's 32 bytes (RFC 8032 §5.1.2)."""
    z = pow(p[2], P - 2, P)
    x, y = p[0] * z % P, p[1] * z % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


_GY = 4 * pow(5, P - 2, P) % P
_GX = _recover_x(_GY, 0)
B: Point = (_GX, _GY, 1, _GX * _GY % P)   # the base point


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Whether ``signature`` is the Ed25519 signature of ``message`` by ``public`` (RFC 8032 §5.1.7)."""
    if len(public) != 32 or len(signature) != 64:
        return False
    a = decode_point(public)
    r = decode_point(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False
    k = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % L
    left = _multiply(8 * s, B)
    right = _multiply(8, _add(r, _multiply(k, a)))
    return _equal(left, right)
