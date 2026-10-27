"""Keys for the owner vault, derived from OpenAlgo's own secret and never equal to it.

OpenAlgo encrypts broker tokens with a Fernet key stretched (PBKDF2) from ``API_KEY_PEPPER`` in
its ``.env``. The vault reuses that secret as input keying material only: HKDF-SHA256 with a fixed
salt and a fixed ``info`` string per purpose gives keys that are independent of OpenAlgo's key
and of each other. Knowing one derived key reveals nothing about the pepper or the other keys.

Rotating the pepper (``upgrade/rotate_pepper.py``) therefore makes every vault secret unreadable.
That is detected through :func:`key_fingerprint` and reported as "save the secrets again"; the
vault is never decrypted with a guessed key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: Same floor OpenAlgo's ``database/auth_db.py`` enforces for the pepper.
MIN_PEPPER_LEN = 32
#: Public, fixed salt: it only separates these derivations from any other HKDF use of the pepper.
HKDF_SALT = b"sudarshan/hkdf-salt/v1"
#: Encrypts secrets at rest in the vault (``db/sudarshan-vault/sudarshan_vault.db``).
VAULT_INFO = b"sudarshan/vault/secrets/v1"
#: Seals the worker's loopback calls to OpenAlgo (authenticates them and hides the token).
HANDOFF_INFO = b"sudarshan/autologin/handoff/v1"
#: Seals OpenAlgo's bridge to the market alerts' local API (sudarshan-alerts, 127.0.0.1:5582).
ALERTS_API_INFO = b"sudarshan/alerts/api/v1"
#: Seals the market alerts' pushes to OpenAlgo's owner room (sudarshan-alerts to OpenAlgo).
ALERTS_PUSH_INFO = b"sudarshan/alerts/push/v1"
#: Seals the screener's digests to the market alerts: their own purpose, so the alerts API
#: refuses a digest under the bridge's seal and a bridge op under this one.
ALERTS_SCREENER_DIGEST_INFO = b"sudarshan/alerts/screener-digest/v1"
#: Seals OpenAlgo's bridge to the portfolio's local API (sudarshan-portfolio, 127.0.0.1:5583).
PORTFOLIO_API_INFO = b"sudarshan/portfolio/api/v1"
#: Seals OpenAlgo's bridge to the screener's local API (sudarshan-screener, 127.0.0.1:5584).
SCREENER_API_INFO = b"sudarshan/screener/api/v1"
#: Seals the screener's calls into OpenAlgo (its watchlist and push routes).
SCREENER_INTERNAL_INFO = b"sudarshan/screener/internal/v1"
#: Every purpose: a key derived for one never opens another's seal (a test holds them apart).
PURPOSES = (
    VAULT_INFO,
    HANDOFF_INFO,
    ALERTS_API_INFO,
    ALERTS_PUSH_INFO,
    ALERTS_SCREENER_DIGEST_INFO,
    PORTFOLIO_API_INFO,
    SCREENER_API_INFO,
    SCREENER_INTERNAL_INFO,
)
_FINGERPRINT_LABEL = b"sudarshan/vault/key-fingerprint/v1"


class VaultKeyError(ValueError):
    """The pepper is missing or too short to derive a key from."""


def pepper_from(env: Mapping[str, str | None]) -> str:
    """Return ``API_KEY_PEPPER`` from ``env``, checked like OpenAlgo checks it.

    Raises:
        VaultKeyError: when it is missing or shorter than ``MIN_PEPPER_LEN``.
    """
    pepper = (env.get("API_KEY_PEPPER") or "").strip()
    if len(pepper) < MIN_PEPPER_LEN:
        raise VaultKeyError("API_KEY_PEPPER is missing or too short in OpenAlgo's .env")
    return pepper


def derive_key(pepper: str, info: bytes) -> bytes:
    """32 bytes for one purpose (``info``), derived from the pepper with HKDF-SHA256."""
    if not pepper or len(pepper) < MIN_PEPPER_LEN:
        raise VaultKeyError("API_KEY_PEPPER is missing or too short")
    if not info:
        raise ValueError("a purpose (info) is required")
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=HKDF_SALT, info=info)
    return hkdf.derive(pepper.encode("utf-8"))


def fernet_for(pepper: str, info: bytes) -> Fernet:
    """A Fernet cipher keyed for one purpose."""
    return Fernet(base64.urlsafe_b64encode(derive_key(pepper, info)))


def key_fingerprint(pepper: str, info: bytes) -> str:
    """A short public id of the derived key, stored next to each ciphertext.

    A PRF output under the key with its own label: it identifies the key without revealing it,
    so a reader can tell "this was written under another key" without trying to decrypt.
    """
    digest = hmac.new(derive_key(pepper, info), _FINGERPRINT_LABEL, hashlib.sha256).hexdigest()
    return digest[:16]
