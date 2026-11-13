"""F9: VAPID (RFC 8292): the token's claims, the raw R || S signature JOSE wants, the cache, the
contact, and the key files the deploy's provision step makes. Every key is generated here; the
only published key is RFC 8292's example public key."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import jwt  # PyJWT, pinned in pyproject: a JOSE reader that is not the code under test
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from push_testkit import b64, b64url, point, vector

from sudarshan.push import DEFAULT_SUBJECT, Vapid, VapidKeyError, check_subject, webpush

V = vector("rfc8292_section2_4.json")
FCM = "https://fcm.googleapis.com"
P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
DEPLOY = Path(__file__).resolve().parents[2] / "ops" / "deploy" / "sudarshan-deploy"


def es256_claims(token: str, public_key: str) -> dict:
    """An ES256 check written here from RFC 7515 and RFC 7518 section 3.4: the signature is R and
    S side by side, 32 bytes each. The claims, or InvalidSignature."""
    header, claims, signature = token.split(".")
    raw = b64(signature)
    assert len(raw) == 64, "a JOSE ES256 signature is 64 bytes of R and S, not DER"
    r, s = int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    assert 0 < r < P256_ORDER and 0 < s < P256_ORDER
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64(public_key))
    key.verify(encode_dss_signature(r, s), f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    assert json.loads(b64(header)) == {"typ": "JWT", "alg": "ES256"}
    return json.loads(b64(claims))


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def key():
    return ec.generate_private_key(ec.SECP256R1())


def test_the_check_here_accepts_rfc8292s_own_example():
    assert es256_claims(V["token"], V["k"]) == {"aud": V["aud"], "exp": V["exp"], "sub": V["sub"]}
    assert b64(V["k"]) == b"\x04" + b64(V["jwk_x"]) + b64(V["jwk_y"])
    tampered = V["token"].replace(".eyJhdWQiOiJodHRwczovL3", ".eyJhdWQiOiJodHRwczovL4", 1)
    with pytest.raises(InvalidSignature):
        es256_claims(tampered, V["k"])


def test_the_signing_input_is_rfc8292s_byte_for_byte(key):
    vapid = Vapid(key, V["sub"], clock=Clock(V["exp"] - webpush.TOKEN_LIFETIME_S))
    token = vapid.token(V["aud"])
    assert token.rsplit(".", 1)[0] == V["token"].rsplit(".", 1)[0]
    assert es256_claims(token, vapid.public_key) == {
        "aud": V["aud"],
        "exp": V["exp"],
        "sub": V["sub"],
    }


def test_the_signature_is_r_and_s_side_by_side_and_verifies_with_the_public_key(key):
    vapid = Vapid(key)
    for _ in range(20):  # a DER signature varies in length; R || S never does
        vapid._tokens.clear()
        token = vapid.token(FCM)
        assert len(b64(token.split(".")[2])) == 64
        assert es256_claims(token, vapid.public_key)["aud"] == FCM
    assert vapid.public_key == b64url(point(key))
    assert re.fullmatch(r"B[A-P][A-Za-z0-9_-]{85}", vapid.public_key)  # the deploy's own pattern


def test_an_independent_jose_library_takes_the_token_and_refuses_der(key):
    token = Vapid(key, "mailto:push@example.com").token(FCM)
    claims = jwt.decode(token, key.public_key(), algorithms=["ES256"], audience=FCM)
    assert claims["sub"] == "mailto:push@example.com"
    assert 0 < claims["exp"] - time.time() <= 24 * 3600
    assert jwt.get_unverified_header(token) == {"typ": "JWT", "alg": "ES256"}
    signing_input = token.rsplit(".", 1)[0]
    der = key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            f"{signing_input}.{b64url(der)}", key.public_key(), algorithms=["ES256"], audience=FCM
        )


def test_exp_is_12_hours_ahead_and_never_more_than_24(key):
    clock = Clock(1_800_000_000)
    token = Vapid(key, clock=clock).token(FCM)
    assert es256_claims(token, b64url(point(key)))["exp"] == 1_800_000_000 + 12 * 3600
    Vapid(key, lifetime_s=24 * 3600)
    for wrong in (24 * 3600 + 1, 3600, 0, -5):
        with pytest.raises(ValueError, match="24 hours at most"):
            Vapid(key, lifetime_s=wrong)


def test_a_token_is_kept_until_an_hour_of_it_is_left(key):
    clock = Clock(1_800_000_000)
    vapid = Vapid(key, clock=clock)
    first = vapid.token(FCM)
    clock.now += 11 * 3600 - 1  # an hour and a second left
    assert vapid.token(FCM) == first
    clock.now += 1  # an hour left: a new one
    second = vapid.token(FCM)
    assert second != first
    assert es256_claims(second, vapid.public_key)["exp"] == clock.now + 12 * 3600
    assert vapid.token(FCM) == second


def test_each_push_service_has_its_own_token(key):
    vapid = Vapid(key)
    google = vapid.token(FCM)
    mozilla = vapid.token("https://updates.push.services.mozilla.com")
    assert google != mozilla
    assert (
        es256_claims(mozilla, vapid.public_key)["aud"]
        == "https://updates.push.services.mozilla.com"
    )
    assert vapid.token(FCM) == google


def test_the_cache_holds_a_bounded_number_of_tokens(key):
    clock = Clock(1_800_000_000)
    vapid = Vapid(key, clock=clock)
    for number in range(300):
        clock.now += 1
        vapid.token(f"https://wns2-n{number}.notify.windows.com")
    assert len(vapid._tokens) == webpush.MAX_AUDIENCES
    assert "https://wns2-n299.notify.windows.com" in vapid._tokens  # the newest stay
    assert "https://wns2-n0.notify.windows.com" not in vapid._tokens


@pytest.mark.parametrize(
    "audience",
    [
        "http://fcm.googleapis.com",
        f"{FCM}/fcm/send/x",
        "fcm.googleapis.com",
        f"{FCM}:443",
        "https://FCM.googleapis.com",
        "",
        None,
    ],
)
def test_a_token_is_made_for_an_https_origin_only(key, audience):
    with pytest.raises(ValueError, match="https origin"):
        Vapid(key).token(audience)


def test_the_authorization_header(key):
    vapid = Vapid(key)
    found = re.fullmatch(
        r"vapid t=([\w-]+\.[\w-]+\.[\w-]+), k=([\w-]{87})", vapid.authorization(FCM)
    )
    assert found and found.group(2) == vapid.public_key
    assert es256_claims(found.group(1), found.group(2))["aud"] == FCM


def test_the_default_contact_names_nobody(key):
    assert DEFAULT_SUBJECT == "mailto:push@example.invalid"  # RFC 2606: .invalid never resolves
    assert es256_claims(Vapid(key).token(FCM), b64url(point(key)))["sub"] == DEFAULT_SUBJECT


@pytest.mark.parametrize(
    "good", ["mailto:ops@example.org", "https://example.org/contact", DEFAULT_SUBJECT]
)
def test_a_mailto_or_https_contact_is_taken(good):
    assert check_subject(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "ops@example.org",
        "mailto:",
        "mailto:ops@localhost",
        "mailto:o ps@example.org",
        "mailto:ops@example.org\nX-Other: 1",
        "http://example.org/contact",
        "mailto:" + "a" * 300 + "@example.org",
        None,
    ],
)
def test_any_other_contact_is_refused_in_words(key, bad):
    with pytest.raises(ValueError, match="must be a mailto: or an https: address"):
        check_subject(bad)
    with pytest.raises(ValueError):
        Vapid(key, bad)


def test_the_repr_names_neither_the_contact_nor_a_token(key):
    vapid = Vapid(key, "mailto:owner-contact@example.org")
    token = vapid.token(FCM)
    assert "owner-contact" not in repr(vapid) and token not in repr(vapid)


# ---- The key files


def sec1(key) -> bytes:
    """The private key as openssl ecparam -genkey -noout writes it: SEC1 PEM, unprotected."""
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


def write_pair(folder: Path, private_pem: bytes, public_text: str) -> tuple[Path, Path]:
    private, public = folder / "vapid_private.pem", folder / "vapid_public.txt"
    private.write_bytes(private_pem)
    public.write_text(
        f"{public_text}\n", encoding="ascii"
    )  # printf '%s\n', as the deploy writes it
    return private, public


def test_the_provisioned_pair_loads(tmp_path, key):
    private, public = write_pair(tmp_path, sec1(key), b64url(point(key)))
    assert private.read_text().startswith("-----BEGIN EC PRIVATE KEY-----")
    assert len(public.read_bytes()) == 88
    vapid = Vapid.from_files(private, public, "mailto:ops@example.org")
    assert vapid.public_key == public.read_text().strip()
    assert vapid.subject == "mailto:ops@example.org"
    assert es256_claims(vapid.token(FCM), vapid.public_key)["sub"] == "mailto:ops@example.org"
    assert Vapid.from_files(private).public_key == vapid.public_key  # the public file is optional


def test_a_pkcs8_key_loads_too(tmp_path, key):
    pkcs8 = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    private, public = write_pair(tmp_path, pkcs8, b64url(point(key)))
    assert Vapid.from_files(private, public).public_key == b64url(point(key))


def test_a_public_file_of_another_key_is_refused_in_words(tmp_path, key):
    other = ec.generate_private_key(ec.SECP256R1())
    private, public = write_pair(tmp_path, sec1(key), b64url(point(other)))
    with pytest.raises(VapidKeyError) as refused:
        Vapid.from_files(private, public)
    assert str(refused.value) == (
        f"{public} does not belong to {private}, so every notification would be refused. Remove "
        f"{public} as root; the next deploy run makes it again from the private key."
    )


def test_key_files_that_cannot_be_used_are_refused_in_words(tmp_path, key):
    missing = tmp_path / "absent.pem"
    with pytest.raises(VapidKeyError, match=r"absent\.pem: No such file or directory\.$"):
        Vapid.from_files(missing)
    garbage = tmp_path / "garbage.pem"
    garbage.write_text("this is not a key\n")
    with pytest.raises(VapidKeyError, match="does not hold an unprotected private key in PEM form"):
        Vapid.from_files(garbage)
    locked = tmp_path / "locked.pem"
    locked.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.BestAvailableEncryption(b"a passphrase"),
        )
    )
    with pytest.raises(VapidKeyError, match="does not hold an unprotected private key"):
        Vapid.from_files(locked)
    p384 = tmp_path / "p384.pem"
    p384.write_bytes(sec1(ec.generate_private_key(ec.SECP384R1())))
    with pytest.raises(VapidKeyError, match="not on the P-256 curve"):
        Vapid.from_files(p384)
    private, _ = write_pair(tmp_path, sec1(key), b64url(point(key)))
    with pytest.raises(VapidKeyError, match="cannot be read"):
        Vapid.from_files(private, tmp_path / "no-public.txt")
    with pytest.raises(VapidKeyError, match="not a P-256 private key"):
        Vapid(ec.generate_private_key(ec.SECP384R1()))


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("openssl") is None,
    reason="the deploy's commands need Linux coreutils and openssl",
)
def test_the_deploy_tools_own_commands_make_a_pair_that_loads(tmp_path):
    """The private key and the public text made with sudarshan-deploy's own commands (fam_provision,
    step 2), which this test also finds in that file, so the two cannot drift apart unseen."""
    deploy = DEPLOY.read_text(encoding="utf-8")
    for command in (
        "openssl ecparam -name prime256v1 -genkey -noout -out",
        '-pubout -outform DER -out "${work}/pub.der"',
        "base64 -w 0 | tr -- '+/' '-_' | tr -d '='",
        "printf '%s\\n' \"${pub}\"",
    ):
        assert command in deploy, command
    script = (
        'set -euo pipefail; cd "$1"\n'
        "openssl ecparam -name prime256v1 -genkey -noout -out vapid_private.pem\n"
        "openssl ec -in vapid_private.pem -noout -check > /dev/null 2>&1\n"
        "openssl ec -in vapid_private.pem -pubout -outform DER -out pub.der > /dev/null 2>&1\n"
        'test "$(wc -c < pub.der)" -eq 91\n'
        "pub=\"$(tail -c 65 -- pub.der | base64 -w 0 | tr -- '+/' '-_' | tr -d '=')\"\n"
        "printf '%s\\n' \"${pub}\" > vapid_public.txt\n"
    )
    subprocess.run(["bash", "-c", script, "make-pair", str(tmp_path)], check=True, timeout=60)
    vapid = Vapid.from_files(tmp_path / "vapid_private.pem", tmp_path / "vapid_public.txt")
    assert vapid.public_key == (tmp_path / "vapid_public.txt").read_text().strip()
    assert es256_claims(vapid.token(FCM), vapid.public_key)["aud"] == FCM
