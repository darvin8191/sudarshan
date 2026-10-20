"""The owner vault: encryption at rest, key derivation and separation, file mode, status, lease."""

from __future__ import annotations

import base64
import os
import sqlite3
import stat
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet, InvalidToken

from sudarshan.vault import (
    HANDOFF_INFO,
    VAULT_INFO,
    SecretUnreadable,
    Vault,
    VaultKeyError,
    derive_key,
    fernet_for,
    key_fingerprint,
    pepper_from,
)

PEPPER = "a1" * 32  # fake
OTHER_PEPPER = "b2" * 32  # fake
SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238's published test key
T0 = datetime(2026, 10, 19, 3, 5, tzinfo=UTC)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "db" / "sudarshan_vault.db"


@pytest.fixture
def vault(path):
    return Vault(path, pepper=PEPPER)


def _raw_rows(path, sql):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql).fetchall()


# -- secrets ----------------------------------------------------------------------------------


def test_round_trip(vault):
    assert vault.get_secret("upstox_totp_secret") is None
    saved_at = vault.set_secret("upstox_totp_secret", SECRET)
    assert vault.get_secret("upstox_totp_secret") == SECRET
    info = vault.secret_info("upstox_totp_secret")
    assert info.is_set and info.readable
    assert abs((info.updated_at - saved_at).total_seconds()) < 1


def test_overwrite_and_clear(vault):
    vault.set_secret("upstox_pin", "111111")
    vault.set_secret("upstox_pin", "222222")
    assert vault.get_secret("upstox_pin") == "222222"
    assert vault.clear_secret("upstox_pin") is True
    assert vault.clear_secret("upstox_pin") is False
    assert vault.get_secret("upstox_pin") is None
    assert vault.secret_info("upstox_pin").is_set is False


def test_only_ciphertext_is_stored(vault, path):
    vault.set_secret("upstox_totp_secret", SECRET)
    vault.set_secret("upstox_pin", "246813")
    rows = _raw_rows(path, "SELECT name, ciphertext, key_id FROM secrets")
    assert {name for name, _, _ in rows} == {"upstox_totp_secret", "upstox_pin"}
    blob = path.read_bytes()
    for _name, ciphertext, key_id in rows:
        assert ciphertext.startswith("gAAAA")  # a Fernet token
        assert SECRET not in ciphertext and "246813" not in ciphertext
        assert key_id == key_fingerprint(PEPPER, VAULT_INFO)
    assert SECRET.encode() not in blob and b"246813" not in blob


def test_empty_value_or_name_refused(vault):
    with pytest.raises(ValueError):
        vault.set_secret("upstox_pin", "")
    with pytest.raises(ValueError):
        vault.set_secret("", "x")


def test_rotated_pepper_makes_secrets_unreadable_without_guessing(vault, path):
    vault.set_secret("upstox_totp_secret", SECRET)
    rotated = Vault(path, pepper=OTHER_PEPPER)
    info = rotated.secret_info("upstox_totp_secret")
    assert info.is_set and not info.readable
    with pytest.raises(SecretUnreadable):
        rotated.get_secret("upstox_totp_secret")


def test_tampered_ciphertext_is_unreadable(vault, path):
    vault.set_secret("upstox_pin", "246813")
    with sqlite3.connect(path) as conn:
        (ciphertext,) = conn.execute("SELECT ciphertext FROM secrets").fetchone()
        flipped = ciphertext[:-6] + ("A" if ciphertext[-6] != "A" else "B") + ciphertext[-5:]
        conn.execute("UPDATE secrets SET ciphertext = ?", (flipped,))
    with pytest.raises(SecretUnreadable):
        vault.get_secret("upstox_pin")


# -- key derivation -----------------------------------------------------------------------------


def _fernet_key_bytes(fernet: Fernet) -> bytes:
    return fernet._signing_key + fernet._encryption_key  # test-only peek


def test_keys_are_deterministic_and_purpose_separated():
    vault_key = derive_key(PEPPER, VAULT_INFO)
    handoff_key = derive_key(PEPPER, HANDOFF_INFO)
    assert len(vault_key) == 32 and len(handoff_key) == 32
    assert derive_key(PEPPER, VAULT_INFO) == vault_key
    assert vault_key != handoff_key
    assert derive_key(OTHER_PEPPER, VAULT_INFO) != vault_key
    assert vault_key not in PEPPER.encode() and PEPPER.encode()[:32] != vault_key
    assert key_fingerprint(PEPPER, VAULT_INFO) != key_fingerprint(PEPPER, HANDOFF_INFO)


def test_vault_key_is_not_openalgos_broker_token_key(monkeypatch):
    """OpenAlgo's own Fernet (PBKDF2 over the pepper) must differ from every derived key."""
    monkeypatch.setenv("API_KEY_PEPPER", PEPPER)
    import database.auth_db as auth_db

    openalgo_fernet = auth_db.get_encryption_key()
    assert auth_db.PEPPER  # the module's pepper (the test pepper of this process)
    ours = {derive_key(auth_db.PEPPER, VAULT_INFO), derive_key(auth_db.PEPPER, HANDOFF_INFO)}
    assert _fernet_key_bytes(openalgo_fernet) not in ours

    vault_cipher = fernet_for(auth_db.PEPPER, VAULT_INFO)
    token = vault_cipher.encrypt(b"fake secret")
    with pytest.raises(InvalidToken):
        openalgo_fernet.decrypt(token)
    with pytest.raises(InvalidToken):
        fernet_for(auth_db.PEPPER, HANDOFF_INFO).decrypt(token)
    with pytest.raises(InvalidToken):
        vault_cipher.decrypt(openalgo_fernet.encrypt(b"fake broker token"))


def test_fernet_key_encoding():
    key = base64.urlsafe_b64encode(derive_key(PEPPER, VAULT_INFO))
    assert Fernet(key).decrypt(fernet_for(PEPPER, VAULT_INFO).encrypt(b"x")) == b"x"


@pytest.mark.parametrize("bad", [None, "", "short", "x" * 31])
def test_pepper_checked_like_openalgo(bad):
    with pytest.raises(VaultKeyError):
        pepper_from({"API_KEY_PEPPER": bad})
    with pytest.raises(VaultKeyError):
        Vault("/nonexistent/never-created.db", pepper=bad or "")


def test_pepper_from_strips_whitespace():
    assert pepper_from({"API_KEY_PEPPER": f"  {PEPPER}\n"}) == PEPPER


# -- file -------------------------------------------------------------------------------------


def test_created_on_first_use_with_mode_600(vault, path):
    assert not path.exists()
    vault.state()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    vault.set_secret("upstox_pin", "246813")
    for extra in (path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        if extra.exists():
            assert stat.S_IMODE(extra.stat().st_mode) & 0o077 == 0


def test_existing_wide_file_is_narrowed(path):
    path.parent.mkdir(parents=True)
    path.touch()
    os.chmod(path, 0o644)
    Vault(path, pepper=PEPPER).state()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# -- status -----------------------------------------------------------------------------------


def test_status_defaults_and_targeted_updates(path):
    app_side = Vault(path, pepper=PEPPER)
    worker_side = Vault(path, pepper=PEPPER)  # a second process, in effect
    state = app_side.state()
    assert state.enabled is False and state.last_run_at is None and state.paused_code is None

    app_side.set_enabled(True)
    worker_side.update_status(last_run_at=T0, last_outcome="ok", last_code="ok", last_message="x")
    app_side.update_status(paused_code="invalid_credentials")
    state = worker_side.state()
    assert state.enabled is True  # not undone by the worker's write
    assert state.last_outcome == "ok" and state.last_run_at == T0
    assert state.paused_code == "invalid_credentials"


def test_update_status_rejects_unknown_columns_and_naive_times(vault):
    with pytest.raises(ValueError):
        vault.update_status(lease_owner="sneaky")
    with pytest.raises(ValueError):
        vault.update_status(last_run_at=datetime(2026, 10, 19, 8, 35))


def test_lease_is_exclusive_and_expires(vault):
    ttl = timedelta(seconds=180)
    assert vault.acquire_lease("run-a", now=T0, ttl=ttl)
    assert not vault.acquire_lease("run-b", now=T0 + timedelta(seconds=5), ttl=ttl)
    assert vault.lease_active(T0 + timedelta(seconds=5))
    vault.release_lease("run-b")  # not the holder: no effect
    assert vault.lease_active(T0 + timedelta(seconds=6))
    vault.release_lease("run-a")
    assert not vault.lease_active(T0 + timedelta(seconds=7))
    assert vault.acquire_lease("run-b", now=T0 + timedelta(seconds=8), ttl=ttl)
    # run-b crashes; its lease lapses and a later run takes over
    assert vault.acquire_lease("run-c", now=T0 + timedelta(seconds=200), ttl=ttl)
    assert vault.state().lease_owner == "run-c"


def test_attempts_window_order_and_pruning(vault):
    first = vault.start_attempt(kind="scheduled", at=T0)
    vault.finish_attempt(first, outcome="failed", code="network")
    second = vault.start_attempt(kind="test", at=T0 + timedelta(minutes=10))
    assert [a.id for a in vault.attempts_since(T0 + timedelta(minutes=5))] == [second]
    recent = vault.recent_attempts(5)
    assert [a.id for a in recent] == [second, first]
    assert recent[0].outcome == "started" and recent[1].code == "network"
    assert vault.prune_attempts(T0 + timedelta(days=31)) == 2
    assert vault.recent_attempts() == []
