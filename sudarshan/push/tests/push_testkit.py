"""Shared by the push tests: base64url helpers and a receiver written from the RFCs alone.

The receiver does what a phone's browser does with a push body: RFC 8291 section 3.4's user-agent
lines give the input keying material, then RFC 8188 decodes the aes128gcm records, any number of
them, checking every padding delimiter. Nothing here imports the code under test, so a round trip
through it is a second opinion; test_push_encryption.py first holds it to RFC 8188's own examples.
"""

from __future__ import annotations

import base64
import hmac
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VECTORS = Path(__file__).parent / "vectors"


def vector(name: str) -> dict:
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


def b64(text: str) -> bytes:
    """urlsafe base64, padded or not, to bytes."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def point(key: ec.EllipticCurvePrivateKey) -> bytes:
    """The public key's uncompressed point (65 bytes, 0x04 first)."""
    return key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def private_key(scalar: bytes) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(scalar, "big"), ec.SECP256R1())


def rfc8188_keys(salt: bytes, ikm: bytes) -> tuple[bytes, bytes, bytes]:
    """PRK, CEK and the nonce of record 0 (RFC 8188 sections 2.2 and 2.3)."""
    prk = hmac.digest(salt, ikm, "sha256")
    cek = hmac.digest(prk, b"Content-Encoding: aes128gcm\x00\x01", "sha256")[:16]
    nonce = hmac.digest(prk, b"Content-Encoding: nonce\x00\x01", "sha256")[:12]
    return prk, cek, nonce


def rfc8188_decrypt(body: bytes, ikm: bytes) -> tuple[bytes, bytes, int]:
    """The plaintext, the key id and the record size of an aes128gcm body (RFC 8188 section 2).

    Raises ``cryptography.exceptions.InvalidTag`` for a changed byte and ``ValueError`` for a
    record whose padding delimiter is wrong (2 on the last record, 1 on every other)."""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    keyid, records = body[21 : 21 + idlen], body[21 + idlen :]
    if rs < 18:
        raise ValueError("a record size below 18 is invalid")
    _, cek, first = rfc8188_keys(salt, ikm)
    chunks = [records[i : i + rs] for i in range(0, len(records), rs)]
    plain = []
    for seq, chunk in enumerate(chunks):
        nonce = (int.from_bytes(first, "big") ^ seq).to_bytes(12, "big")
        padded = AESGCM(cek).decrypt(nonce, chunk, b"").rstrip(b"\x00")
        wanted = 2 if seq == len(chunks) - 1 else 1
        if not padded or padded[-1] != wanted:
            raise ValueError("a record's padding delimiter is wrong")
        plain.append(padded[:-1])
    return b"".join(plain), keyid, rs


def ua_ikm(ua_private: ec.EllipticCurvePrivateKey, as_public: bytes, auth_secret: bytes) -> bytes:
    """RFC 8291 section 3.4, the user agent's lines: the IKM a phone derives on receipt."""
    sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
    ecdh_secret = ua_private.exchange(ec.ECDH(), sender)
    prk_key = hmac.digest(auth_secret, ecdh_secret, "sha256")
    key_info = b"WebPush: info\x00" + point(ua_private) + as_public
    return hmac.digest(prk_key, key_info + b"\x01", "sha256")


def phone_reads(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth_secret: bytes) -> bytes:
    """What the phone reads out of a push body; RFC 8291 section 4 holds it to one record of 4096
    with the sender's 65-byte public key as the whole key id."""
    as_public = body[21 : 21 + body[20]]
    plain, keyid, rs = rfc8188_decrypt(body, ua_ikm(ua_private, as_public, auth_secret))
    assert (len(keyid), keyid[:1], rs) == (65, b"\x04", 4096)
    assert len(body) <= 21 + len(keyid) + rs, "more than one record"
    return plain
