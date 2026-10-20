"""Owner vault: small secrets the owner enters on his phone, encrypted at rest (SUDARSHAN).

Values are write-only from the web UI: OpenAlgo can set or clear them and report whether they are
set, but nothing serves a value back over HTTP. Only the auto-login worker decrypts them.
"""

from .keys import (
    HANDOFF_INFO,
    VAULT_INFO,
    VaultKeyError,
    derive_key,
    fernet_for,
    key_fingerprint,
    pepper_from,
)
from .store import (
    Attempt,
    AutologinState,
    SecretInfo,
    SecretUnreadable,
    Vault,
    default_vault_path,
    utcnow,
)

__all__ = [
    "HANDOFF_INFO",
    "VAULT_INFO",
    "Attempt",
    "AutologinState",
    "SecretInfo",
    "SecretUnreadable",
    "Vault",
    "VaultKeyError",
    "default_vault_path",
    "derive_key",
    "fernet_for",
    "key_fingerprint",
    "pepper_from",
    "utcnow",
]
