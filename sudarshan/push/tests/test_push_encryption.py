"""F9: message encryption for Web Push (RFC 8291 over RFC 8188's aes128gcm).

Pinned by the RFC's own example, every intermediate value of it, typed by hand into vectors/ from
the RFC text, and checked by a receiver written from the RFCs alone (push_testkit.py), which is
itself first held to RFC 8188's examples.
"""

from __future__ import annotations

import os

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from push_testkit import (
    b64,
    b64url,
    phone_reads,
    point,
    private_key,
    rfc8188_decrypt,
    rfc8188_keys,
    vector,
)

from sudarshan.push import (
    MAX_PAYLOAD,
    RECORD_SIZE,
    InvalidSubscription,
    PayloadTooLarge,
    PushError,
    encrypt,
    webpush,
)

A = vector("rfc8291_appendix_a.json")
E = vector("rfc8188_section3.json")


@pytest.fixture
def phone():
    """A phone's subscription keys, made here: its private key, public point and auth secret."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key, point(key), os.urandom(16)


# ---- The receiver is right before it judges anything


def test_the_receiver_reads_rfc8188s_one_record_example():
    v = E["single_record"]
    body, ikm = b64(v["body"]), b64(v["ikm"])
    assert len(body) == v["body_length"] == 53  # RFC 8188 erratum 5516 (the text says 54)
    assert rfc8188_keys(body[:16], ikm) == (b64(v["prk"]), b64(v["cek"]), b64(v["nonce"]))
    assert body[:16] == b64(v["salt"])
    assert rfc8188_decrypt(body, ikm) == (b"I am the walrus", b"", 4096)
    assert b"I am the walrus\x02" == b64(v["padded_plaintext"])


def test_the_receiver_reads_rfc8188s_two_record_example():
    v = E["multiple_records"]
    body = b64(v["body"])
    assert len(body) == v["body_length"] == 73
    assert rfc8188_decrypt(body, b64(v["ikm"])) == (b"I am the walrus", b"a1", 25)


def test_the_receiver_refuses_a_changed_byte_and_a_wrong_delimiter():
    v = E["single_record"]
    changed = bytearray(b64(v["body"]))
    changed[-1] ^= 0x01
    with pytest.raises(InvalidTag):
        rfc8188_decrypt(bytes(changed), b64(v["ikm"]))
    salt, ikm = bytes(16), bytes(range(16))
    _, cek, nonce = rfc8188_keys(salt, ikm)
    one_record_marked_not_last = AESGCM(cek).encrypt(nonce, b"hello\x01", b"")
    body = salt + (4096).to_bytes(4, "big") + b"\x00" + one_record_marked_not_last
    with pytest.raises(ValueError, match="delimiter"):
        rfc8188_decrypt(body, ikm)


def test_the_receiver_reads_the_rfc8291_example_with_the_rfcs_phone_key():
    body = b64(A["body"])
    assert phone_reads(body, private_key(b64(A["ua_private"])), b64(A["auth_secret"])) == (
        b64(A["plaintext"])
    )


# ---- RFC 8291 Appendix A, byte for byte


def test_rfc8291_appendix_a_every_intermediate_value():
    as_private = private_key(b64(A["as_private"]))
    assert point(as_private) == b64(A["as_public"])  # the RFC's pair is one key
    ua_public = b64(A["ua_public"])
    keys = webpush.key_schedule(as_private, ua_public, b64(A["auth_secret"]), b64(A["salt"]))
    assert keys.ecdh_secret == b64(A["ecdh_secret"])
    assert keys.as_public == b64(A["as_public"])
    assert keys.prk_key == b64(A["prk_key"])
    assert keys.key_info == b64(A["key_info"])
    assert keys.ikm == b64(A["ikm"])
    assert keys.prk == b64(A["prk"])
    assert keys.cek == b64(A["cek"])
    assert keys.nonce == b64(A["nonce"])
    assert webpush.CEK_INFO == b64(A["cek_info"])
    assert webpush.NONCE_INFO == b64(A["nonce_info"])
    assert webpush.KEY_INFO == b64(A["key_info"])[:14]


def test_rfc8291_section_5_body_byte_for_byte():
    sealed = encrypt(
        b64(A["plaintext"]),
        A["ua_public"],
        A["auth_secret"],
        sender_key=private_key(b64(A["as_private"])),
        salt=b64(A["salt"]),
    )
    assert b64(A["plaintext"]) == A["plaintext_ascii"].encode("ascii")
    assert sealed.body == b64(A["body"])
    assert len(sealed.body) == A["body_length"] == 144  # RFC 8291 erratum 5230 (the text says 145)
    assert sealed.body[:86] == b64(A["header"])
    assert sealed.body[86:] == b64(A["ciphertext"])
    assert sealed.headers == {
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
    }


def test_the_example_s_text_gives_the_example_s_body_too():
    sealed = encrypt(
        A["plaintext_ascii"],
        b64(A["ua_public"]),
        b64(A["auth_secret"]),
        sender_key=private_key(b64(A["as_private"])),
        salt=b64(A["salt"]),
    )
    assert sealed.body == b64(A["body"])


# ---- What any message looks like


@pytest.mark.parametrize("size", [0, 1, 41, 1000, MAX_PAYLOAD])
def test_a_round_trip_through_the_independent_receiver(phone, size):
    key, public, secret = phone
    payload = os.urandom(size)
    body = encrypt(payload, public, secret).body
    assert len(body) == 86 + size + 1 + 16
    assert int.from_bytes(body[16:20], "big") == RECORD_SIZE == 4096
    assert body[20] == 65 and body[21] == 0x04
    assert phone_reads(body, key, secret) == payload


def test_the_largest_payload_fills_the_4096_bytes_a_push_service_must_take(phone):
    key, public, secret = phone
    body = encrypt(b"\xff" * MAX_PAYLOAD, public, secret).body
    assert len(body) == 4096
    assert MAX_PAYLOAD == RECORD_SIZE - 86 - 1 - 16 == 3993


def test_every_message_has_its_own_key_pair_and_salt(phone):
    key, public, secret = phone
    first, second = (encrypt(b"the same words", public, secret).body for _ in range(2))
    assert first[:16] != second[:16]
    assert first[21:86] != second[21:86]
    assert first[86:] != second[86:]
    assert phone_reads(first, key, secret) == phone_reads(second, key, secret)


def test_text_is_sent_as_utf8(phone):
    key, public, secret = phone
    words = "सुदर्शन / SUDARSHAN: a family member signed in on a new phone"
    assert phone_reads(encrypt(words, public, secret).body, key, secret) == words.encode("utf-8")


def test_one_byte_over_the_limit_is_refused_in_words(phone):
    _, public, secret = phone
    with pytest.raises(PayloadTooLarge) as refused:
        encrypt(b"x" * (MAX_PAYLOAD + 1), public, secret)
    assert str(refused.value) == (
        "The notification is 3,994 bytes and one push message carries at most 3,993, so it was "
        "not sent."
    )
    with pytest.raises(PayloadTooLarge, match="1,00,000 bytes"):
        encrypt("a" * 100_000, public, secret)
    with pytest.raises(PayloadTooLarge, match="4,050 bytes"):  # 1,350 letters of three bytes each
        encrypt("स" * 1350, public, secret)


def test_the_keys_may_come_as_base64url_text_with_or_without_padding(phone):
    key, public, secret = phone
    for p256dh, auth in (
        (b64url(public), b64url(secret)),
        (b64url(public) + "=", b64url(secret) + "=="),
        (public, secret),
    ):
        assert phone_reads(encrypt(b"x", p256dh, auth).body, key, secret) == b"x"


def _standard_alphabet_key() -> str:
    """A phone key written in standard base64 ('+' and '/'), which the browsers never send."""
    while True:
        text = b64url(point(ec.generate_private_key(ec.SECP256R1())))
        if "-" in text or "_" in text:
            return text.replace("-", "+").replace("_", "/")


@pytest.mark.parametrize(
    "p256dh",
    [
        "not base64 at all!",
        b64url(os.urandom(64)),  # one byte short
        b64url(b"\x04" + bytes(64)),  # the right size, not on the curve
        b64url(point(ec.generate_private_key(ec.SECP256R1()))[:33]),  # a compressed-size key
        _standard_alphabet_key(),
        "A",  # no whole byte
        12345,
        None,
    ],
)
def test_a_phone_key_that_cannot_be_used_is_refused_in_words(phone, p256dh):
    _, _, secret = phone
    with pytest.raises(InvalidSubscription) as refused:
        encrypt(b"x", p256dh, secret)
    assert str(refused.value) == (
        "This phone's notification key is not usable. Turn notifications off and on again on the "
        "phone."
    )
    assert isinstance(refused.value, PushError) and isinstance(refused.value, ValueError)


@pytest.mark.parametrize("auth", [bytes(15), bytes(17), b"", "", b64url(bytes(32)), "!!!!", 7])
def test_an_auth_secret_that_is_not_16_bytes_is_refused_in_words(phone, auth):
    _, public, _ = phone
    with pytest.raises(InvalidSubscription, match="notification secret is not usable"):
        encrypt(b"x", public, auth)


def test_a_salt_a_sender_key_or_a_payload_of_the_wrong_kind_is_refused(phone):
    _, public, secret = phone
    with pytest.raises(ValueError, match="16 bytes"):
        encrypt(b"x", public, secret, salt=bytes(15))
    with pytest.raises(ValueError, match="P-256"):
        encrypt(b"x", public, secret, sender_key=ec.generate_private_key(ec.SECP384R1()))
    with pytest.raises(TypeError, match="bytes or text"):
        encrypt({"v": 1}, public, secret)
