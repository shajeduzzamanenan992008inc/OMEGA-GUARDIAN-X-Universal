"""Minimal, dependency-free Ed25519 signature verification (RFC 8032).

The project ships no runtime dependencies, so plugin signature verification
cannot rely on ``cryptography`` or ``PyNaCl``. This module implements *only*
verification (plus point decoding); it never generates or stores private keys.

It is deliberately small and constant in behaviour:

* Signature and public key must be exactly 64 and 32 bytes.
* ``S`` must be canonical (``S < L``); non-canonical signatures are rejected.
* Public-key points must decode to a valid curve point.
* Verification uses the standard equation ``[S]B = R + [k]A``.

The implementation follows the public-domain reference algorithm from
RFC 8032 section 6 ("Ed25519 Python") with strict input validation added.
It is not constant-time; verification handles public data only.
"""

from __future__ import annotations

import hashlib

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, P - 2, P)) % P
_SQRT_M1 = pow(2, (P - 1) // 4, P)


class SignatureError(ValueError):
    """Raised when a signature or key is malformed."""


def _inv(value: int) -> int:
    return pow(value % P, P - 2, P)


def _point_add(p_point: tuple[int, int, int, int], q_point: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x1, y1, z1, t1 = p_point
    x2, y2, z2, t2 = q_point
    a = ((y1 - x1) * (y2 - x2)) % P
    b = ((y1 + x1) * (y2 + x2)) % P
    c = (2 * t1 * t2 * _D) % P
    d = (2 * z1 * z2) % P
    e, f, g, h = (b - a) % P, (d - c) % P, (d + c) % P, (b + a) % P
    return ((e * f) % P, (g * h) % P, (f * g) % P, (e * h) % P)


def _point_mul(scalar: int, point: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    result = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            result = _point_add(result, point)
        point = _point_add(point, point)
        scalar >>= 1
    return result


def _point_equal(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
    (x1, y1, z1, _), (x2, y2, z2, _) = left, right
    return ((x1 * z2) - (x2 * z1)) % P == 0 and ((y1 * z2) - (y2 * z1)) % P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= P:
        return None
    x_squared = ((y * y - 1) * _inv(_D * y * y + 1)) % P
    if x_squared == 0:
        return 0 if sign == 0 else None
    x = pow(x_squared, (P + 3) // 8, P)
    if (x * x - x_squared) % P != 0:
        x = (x * _SQRT_M1) % P
    if (x * x - x_squared) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


def _decompress(data: bytes) -> tuple[int, int, int, int] | None:
    if len(data) != 32:
        return None
    value = int.from_bytes(data, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, (x * y) % P)


_G_Y = (4 * _inv(5)) % P
_G_X = _recover_x(_G_Y, 0)
if _G_X is None:  # pragma: no cover - the base point always decodes
    raise AssertionError("Ed25519 base point failed to decode")
_BASE = (_G_X, _G_Y, 1, (_G_X * _G_Y) % P)


def verify(public_key: bytes, signature: bytes, message: bytes) -> bool:
    """Return ``True`` when ``signature`` is a valid Ed25519 signature.

    Malformed keys or signatures return ``False`` rather than raising, so a
    caller can treat verification as a fail-closed boolean. Inputs are still
    typed-checked to avoid silent ``str``/``bytes`` confusion.
    """

    if not isinstance(public_key, (bytes, bytearray)) or not isinstance(
        signature, (bytes, bytearray)
    ) or not isinstance(message, (bytes, bytearray)):
        return False
    public_key = bytes(public_key)
    signature = bytes(signature)
    message = bytes(message)
    if len(public_key) != 32 or len(signature) != 64:
        return False

    scalar = int.from_bytes(signature[32:], "little")
    if scalar >= L:
        return False
    r_point = _decompress(signature[:32])
    a_point = _decompress(public_key)
    if r_point is None or a_point is None:
        return False

    challenge = int.from_bytes(
        hashlib.sha512(signature[:32] + public_key + message).digest(), "little"
    ) % L
    left = _point_mul(scalar, _BASE)
    right = _point_add(r_point, _point_mul(challenge, a_point))
    return _point_equal(left, right)


def verify_strict(public_key: bytes, signature: bytes, message: bytes) -> None:
    """Raise :class:`SignatureError` when verification fails."""

    if not verify(public_key, signature, message):
        raise SignatureError("Ed25519 signature verification failed.")
