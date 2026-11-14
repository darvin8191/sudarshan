"""Settings and keys of sudarshan-family (design 2.8; cross-design Y1), read once at start.

The settings are the unit's environment file, ``/etc/sudarshan-family/family.env`` (root:
sudarshan-family 640): systemd hands it over as the process environment, and the file is also read
here (under the environment) so that a start by hand sees the same values. The family account can
read nothing under ``/etc/sudarshan`` or OpenAlgo's ``.env``, so nothing here looks there.

| setting | default |
|---|---|
| ``SUDARSHAN_FAMILY`` | ``off``: every viewer request answers 503, no job runs |
| ``SUDARSHAN_FAMILY_HTTP_PORT`` | ``5585`` (127.0.0.1 only; never another port) |
| ``SUDARSHAN_FAMILY_FEED_URL`` / ``_FEED_ZMQ`` | ``http://127.0.0.1:5580`` / ``tcp://127.0.0.1:5581`` |
| ``SUDARSHAN_FAMILY_PORTFOLIO_URL`` | ``http://127.0.0.1:5583`` |
| ``SUDARSHAN_FAMILY_OPENALGO_URL`` | ``http://127.0.0.1:5000`` |
| ``SUDARSHAN_FAMILY_JOURNAL_URL`` | ``http://127.0.0.1:5586`` (the summary's trades line, Y16) |
| ``SUDARSHAN_FAMILY_MAX_MEMBERS`` / ``_MAX_DEVICES`` | ``4`` / ``3`` (lower only) |
| ``SUDARSHAN_FAMILY_MAX_CLAIMS`` | ``100`` (the feed's quota for this client, cross-design Y12) |
| ``SUDARSHAN_FAMILY_DIGEST_AFTER_MIN`` | ``25`` (minutes after the day's last close) |
| ``SUDARSHAN_FAMILY_PUSH_SUBJECT`` | empty (a ``mailto:`` address; never in the repository) |
| ``SUDARSHAN_FAMILY_NAME_MEMBERS`` | ``off``: notices say "a family member"; on, their sign-in name |
| ``SUDARSHAN_FAMILY_LOG_LEVEL`` | ``INFO`` |

The keys are files in ``/etc/sudarshan-family`` that the deploy's provision step writes: the seal
keys ``seal-<purpose>.key`` (urlsafe base64 of 32 bytes, derived by ``sudarshan`` from the pepper;
this service never derives one) and ``store.key`` (standard base64 of 32 random bytes). A key that
is missing or malformed stops the start with a sentence and exit status 2.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from dotenv import dotenv_values

from sudarshan.ops.release import DEPLOYED_TAG_FILE

from .contract import LIMITS, PORT
from .store import STATE_DIR, Secrets

ETC_DIR = "/etc/sudarshan-family"
EXPORT_DIR = "/var/lib/sudarshan-family-export"
LOOPBACK = "127.0.0.1"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
#: The largest key file read (a key is 44 characters and a newline).
KEY_FILE_MAX = 256
_ON = {"1", "on", "true", "yes"}
_OFF = {"0", "off", "false", "no"}


class ConfigError(ValueError):
    """The service cannot run with these settings or keys; the message names what and the fix."""


def setup_logging(level: str = "INFO") -> None:
    """Our format on the journal, at the level the setting names: counts and kinds, never a name,
    a sign-in name, a chat id, an endpoint, a code or a token."""
    logging.basicConfig(
        level=getattr(logging, level.strip().upper(), logging.INFO), format=LOG_FORMAT, force=True
    )
    for library in ("httpx", "httpcore", "sqlalchemy", "websockets"):
        logging.getLogger(library).setLevel(logging.WARNING)


def _switch(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _ON:
        return True
    if raw in _OFF:
        return False
    raise ConfigError(f"{name} must be on or off")


def _number(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    if not raw.isdigit() or not low <= int(raw) <= high:
        raise ConfigError(f"{name} must be a whole number from {low} to {high}")
    return int(raw)


def _loopback(env: Mapping[str, str], name: str, default: str, scheme: str) -> str:
    url = (env.get(name) or default).strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != scheme or (parts.hostname or "") not in LOOPBACK_HOSTS:
        raise ConfigError(f"{name} must be {scheme}:// on this server (127.0.0.1)")
    return url


def read_env_file(path: Path) -> dict[str, str]:
    """The settings file as a dict; an absent or unreadable file is no settings (the defaults)."""
    try:
        with path.open(encoding="utf-8") as handle:
            values = dotenv_values(stream=handle)
    except OSError:
        return {}
    return {key: (value or "").strip() for key, value in values.items() if key}


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    http_port: int = PORT
    feed_url: str = f"http://{LOOPBACK}:5580"
    feed_zmq: str = f"tcp://{LOOPBACK}:5581"
    portfolio_url: str = f"http://{LOOPBACK}:5583"
    openalgo_url: str = f"http://{LOOPBACK}:5000"
    journal_url: str = f"http://{LOOPBACK}:5586"  # F10 (feat/family-t2): Y16
    max_members: int = LIMITS["max_members"]
    max_devices: int = LIMITS["max_devices"]
    max_claims: int = 100
    digest_after_min: int = 25
    push_subject: str = ""
    name_members: bool = False
    log_level: str = "INFO"
    etc_dir: Path = Path(ETC_DIR)
    state_dir: Path = STATE_DIR
    export_dir: Path = Path(EXPORT_DIR)
    deployed_tag_file: Path = DEPLOYED_TAG_FILE

    @property
    def holidays_file(self) -> Path:
        return self.etc_dir / "nse_holidays.txt"

    @property
    def extra_days_file(self) -> Path:
        return self.etc_dir / "nse_extra_trading_days.txt"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """The settings: ``env`` as given (a test), else the process environment over the file."""
        if env is None:
            base = dict(os.environ)
            etc = Path(base.get("SUDARSHAN_FAMILY_ETC_DIR") or ETC_DIR)
            env_file = Path(base.get("SUDARSHAN_FAMILY_ENV_FILE") or etc / "family.env")
            env = read_env_file(env_file) | {k: v for k, v in base.items() if v != ""}
        get = env.get
        subject = (get("SUDARSHAN_FAMILY_PUSH_SUBJECT") or "").strip()
        if subject and not subject.startswith(("mailto:", "https://")):
            raise ConfigError("SUDARSHAN_FAMILY_PUSH_SUBJECT must be a mailto: or https:// address")
        state = get("SUDARSHAN_FAMILY_STATE_DIR") or get("STATE_DIRECTORY") or str(STATE_DIR)
        return cls(
            enabled=_switch(env, "SUDARSHAN_FAMILY", False),
            http_port=_number(env, "SUDARSHAN_FAMILY_HTTP_PORT", PORT, 1, 65535),
            feed_url=_loopback(env, "SUDARSHAN_FAMILY_FEED_URL", cls.feed_url, "http"),
            feed_zmq=_loopback(env, "SUDARSHAN_FAMILY_FEED_ZMQ", cls.feed_zmq, "tcp"),
            portfolio_url=_loopback(env, "SUDARSHAN_FAMILY_PORTFOLIO_URL", cls.portfolio_url,
                                    "http"),
            openalgo_url=_loopback(env, "SUDARSHAN_FAMILY_OPENALGO_URL", cls.openalgo_url, "http"),
            journal_url=_loopback(env, "SUDARSHAN_FAMILY_JOURNAL_URL", cls.journal_url,
                                  "http"),  # F10 (feat/family-t2)
            max_members=_number(env, "SUDARSHAN_FAMILY_MAX_MEMBERS", LIMITS["max_members"], 1,
                                LIMITS["max_members"]),
            max_devices=_number(env, "SUDARSHAN_FAMILY_MAX_DEVICES", LIMITS["max_devices"], 1,
                                LIMITS["max_devices"]),
            max_claims=_number(env, "SUDARSHAN_FAMILY_MAX_CLAIMS", 100, 1, 100),
            digest_after_min=_number(env, "SUDARSHAN_FAMILY_DIGEST_AFTER_MIN", 25, 0, 120),
            push_subject=subject,
            name_members=_switch(env, "SUDARSHAN_FAMILY_NAME_MEMBERS", False),
            log_level=(get("SUDARSHAN_FAMILY_LOG_LEVEL") or "INFO").strip().upper(),
            etc_dir=Path(get("SUDARSHAN_FAMILY_ETC_DIR") or ETC_DIR),
            state_dir=Path(state.split(":")[0]),
            export_dir=Path(get("SUDARSHAN_FAMILY_EXPORT_DIR") or EXPORT_DIR),
            deployed_tag_file=Path(get("SUDARSHAN_DEPLOYED_TAG_FILE") or DEPLOYED_TAG_FILE),
        )  # fmt: skip


def _key_text(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(KEY_FILE_MAX + 1)
    except FileNotFoundError:
        raise ConfigError(f"{path} is missing: run the deploy's family provision step") from None
    except OSError as exc:
        raise ConfigError(f"{path} cannot be read ({exc.strerror or 'no access'})") from None
    if len(raw) > KEY_FILE_MAX:
        raise ConfigError(f"{path} is not a key: it is too long")
    return raw.decode("ascii", errors="replace").strip()


def seal_key(etc_dir: Path, purpose: str) -> Fernet:
    """The Fernet of ``seal-<purpose>.key``: 44 characters of urlsafe base64, 32 bytes."""
    path = etc_dir / f"seal-{purpose}.key"
    text = _key_text(path)
    try:
        if len(text) != 44 or len(base64.urlsafe_b64decode(text.encode("ascii"))) != 32:
            raise ValueError
        return Fernet(text.encode("ascii"))
    except (ValueError, binascii.Error):
        raise ConfigError(
            f"{path} is not a seal key: run the deploy's family provision step"
        ) from None


def store_secrets(etc_dir: Path) -> Secrets:
    path = etc_dir / "store.key"
    text = _key_text(path)
    try:
        return Secrets.from_text(text)
    except ValueError:
        raise ConfigError(
            f"{path} is not 32 bytes of base64; it is made once by the provision "
            "step and never rewritten, so restore it from the backup"
        ) from None


@dataclass(frozen=True)
class Keys:
    """The keys this service uses: the two sealed doors' and, when the view is on, the store's."""

    admin: Fernet
    nudge: Fernet
    store: Secrets | None

    @classmethod
    def load(cls, settings: Settings) -> Keys:
        etc = settings.etc_dir
        admin, nudge = seal_key(etc, "admin"), seal_key(etc, "nudge")
        return cls(admin, nudge, store_secrets(etc) if settings.enabled else None)
