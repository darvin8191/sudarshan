"""RFC 6238 TOTP: the published SHA-1 test vectors, an independent implementation, key input."""

from __future__ import annotations

import hashlib

import pyotp
import pytest
from autologin_fakes import FAKE_TOTP_SECRET

from sudarshan.autologin.totp import (
    InvalidSecret,
    hotp,
    normalize_secret,
    seconds_left,
    totp,
)

# RFC 6238 appendix B, SHA-1 column; the key is ASCII "12345678901234567890".
RFC6238_SHA1 = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


@pytest.mark.parametrize(("at", "expected"), RFC6238_SHA1)
def test_rfc6238_sha1_vectors(at, expected):
    assert totp(FAKE_TOTP_SECRET, at, digits=8) == expected


@pytest.mark.parametrize(("at", "expected"), RFC6238_SHA1)
def test_six_digit_codes_are_the_low_digits_of_the_vectors(at, expected):
    assert totp(FAKE_TOTP_SECRET, at) == expected[-6:]


def test_rfc4226_hotp_vectors():
    # RFC 4226 appendix D, same key.
    expected = ["755224", "287082", "359152", "969429", "338314", "254676", "287922"]
    key = b"12345678901234567890"
    assert [hotp(key, counter) for counter in range(7)] == expected


@pytest.mark.parametrize("at", [0, 29.999, 30, 1_700_000_000, 1_760_850_910.5, 4_102_444_800])
def test_matches_pyotp(at):
    assert totp(FAKE_TOTP_SECRET, at) == pyotp.TOTP(FAKE_TOTP_SECRET).at(int(at))


def test_code_changes_only_at_step_boundaries():
    assert totp(FAKE_TOTP_SECRET, 60) == totp(FAKE_TOTP_SECRET, 89.9)
    assert totp(FAKE_TOTP_SECRET, 89.9) != totp(FAKE_TOTP_SECRET, 90)


def test_seconds_left():
    assert seconds_left(60) == 30
    assert seconds_left(85.5) == pytest.approx(4.5)


def test_sha256_option_differs_from_default():
    assert totp(FAKE_TOTP_SECRET, 59, digest=hashlib.sha256) != totp(FAKE_TOTP_SECRET, 59)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("GEZD GNBV GY3T QOJQ GEZD GNBV GY3T QOJQ", FAKE_TOTP_SECRET),
        ("gezdgnbvgy3tqojqgezdgnbvgy3tqojq", FAKE_TOTP_SECRET),
        ("  GEZDGNBVGY3TQOJQ\n", "GEZDGNBVGY3TQOJQ"),
        ("JBSWY3DPEHPK3PXP====", "JBSWY3DPEHPK3PXP"),  # padding pasted along is dropped
        ("A" * 64, "A" * 64),
    ],
)
def test_normalize_accepts_pasted_forms(raw, expected):
    assert normalize_secret(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "SHORTKEY",  # 8 characters
        "A" * 15,
        "A" * 65,
        "GEZDGNBVGY3TQOJ1",  # 1 is not base32
        "GEZDGNBVGY3TQOJ-",
        "GEZDGNBVGY3TQOJQG",  # 17 characters: no base32 string ends there
        None,
        123456,
    ],
)
def test_normalize_rejects(raw):
    with pytest.raises(InvalidSecret):
        normalize_secret(raw)


def test_normalized_secret_produces_the_same_code():
    spaced = normalize_secret("gezd gnbv gy3t qojq gezd gnbv gy3t qojq")
    assert totp(spaced, 1111111109) == totp(FAKE_TOTP_SECRET, 1111111109)
