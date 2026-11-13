"""The offsite copy of the nightly backup (M18.11, part 2): encrypted on the server, then uploaded.

    local archive (backup.py) -> openssl CMS encrypt to the owner's PUBLIC certificate
      -> one PUT to a write-only pre-authenticated request (PAR) URL of one OCI bucket

What the server holds, in ``/etc/sudarshan/offsite.json`` (owner ``sudarshan``, mode 600, never in
the repository, never in a log line, a notice or the archive itself):

    par_url      the write-only PAR URL of the bucket, ending in ``/o/``
    expires      the date the PAR stops working, ``YYYY-MM-DD`` (the owner copies it from the console)
    certificate  the public X.509 certificate (PEM) the archive is encrypted to

The server holds no OCI credential and no decryption key. The private key is made on the owner's
laptop and never reaches the server, so a server that is taken over can neither read the offsite
copies nor delete them (the PAR can only write; retention is a lifecycle rule in the console and
this code never deletes an object).

Why openssl CMS and not ``age``: it is on every Ubuntu already, so there is nothing to install and
nothing for the job to refuse over. The cipher is AES-256-CBC with an RSA-OAEP wrapped key. CBC does
not authenticate by itself; the archive inside carries a manifest of SHA-256 sums that
``backup --verify`` checks after the owner decrypts it, which is what catches a damaged or altered
file.

A PAR expires. From 14 days before the date in the file the owner is told once a day; after it the
job does not try, and says so. Without the file the step is off: one line in the journal a day.
"""

from __future__ import annotations

import contextlib
import getpass
import http.client
import json
import logging
import os
import pwd
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlsplit

from .files import read_regular

log = logging.getLogger("sudarshan.backup")

CONFIG_NAME = "offsite.json"
WARN_DAYS = 14
MAX_CONFIG = 32 << 10
CHUNK = 1 << 20
TRIES = 3
PAUSE_S = 30.0
SOCKET_TIMEOUT_S = 300.0
#: What each failure is called in the notice (backup.py records the code; alerts words it).
REASONS = ("openssl", "encrypt", "space", "network", "refused", "busy", "expired", "config")
_PAR_END = re.compile(r".*/o/?")
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_CERT_BEGIN = "-----BEGIN CERTIFICATE-----"


class OffsiteError(Exception):
    """A step failed; ``reason`` is one of REASONS (a word the notice knows, never a status)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Config:
    par_url: str
    expires: date
    certificate: str


@dataclass(frozen=True)
class Outcome:
    state: str  # off, ok, failed
    reason: str = ""
    days_left: int | None = None  # set from 14 days before the expiry, also when the upload worked
    expires: date | None = None


def object_name(archive: str, release: str | None) -> str:
    """``sudarshan-backup-20261109-163102-v0.7.0.tar.gz.cms``: the date and the release, no host."""
    stem = archive[: -len(".tar.gz")] if archive.endswith(".tar.gz") else archive
    partial = "-partial" if stem.endswith("-partial") else ""
    stem = stem.removesuffix("-partial")
    return f"{stem}-{release or 'norelease'}{partial}.tar.gz.cms"


# ---- the file ---------------------------------------------------------------------------------


def load(etc_dir: Path) -> Config | None:
    """The settings, or None when the file is not there. A file that is there and cannot be used
    raises ``OffsiteError("config")``: the owner meant it to work."""
    path = etc_dir / CONFIG_NAME
    raw = read_regular(path, MAX_CONFIG)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        par, expires, cert = data["par_url"], data["expires"], data["certificate"]
        config = Config(par.strip(), date.fromisoformat(expires), cert.strip() + "\n")
    except (ValueError, KeyError, TypeError, AttributeError):
        raise OffsiteError("config") from None
    problem = _problem(config)
    if problem:
        log.error("%s: %s", CONFIG_NAME, problem)
        raise OffsiteError("config")
    with contextlib.suppress(OSError):
        if path.stat().st_mode & 0o077:
            log.warning("%s can be read by other users; set its mode to 600", CONFIG_NAME)
    return config


def _problem(config: Config) -> str:
    """What is wrong with the settings, in words that hold no part of them; "" when none."""
    url = urlsplit(config.par_url)
    if url.scheme != "https" and not (url.scheme == "http" and url.hostname in _LOOPBACK):
        return "the access link must start with https://"
    if not url.hostname or url.query or url.fragment or not _PAR_END.fullmatch(url.path):
        return "the access link must be a bucket link ending in /o/"
    cert = config.certificate
    if _CERT_BEGIN not in cert or "PRIVATE KEY" in cert:
        return "the certificate must be a public certificate and nothing else"
    return ""


def write_config(etc_dir: Path, config: Config) -> Path:
    """Write the file whole and private (root runs this; the job's own user owns the result)."""
    document = {
        "par_url": config.par_url,
        "expires": config.expires.isoformat(),
        "certificate": config.certificate,
    }
    target = etc_dir / CONFIG_NAME
    fd, temp = tempfile.mkstemp(dir=etc_dir, prefix=f".{CONFIG_NAME}.")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")
    os.chmod(temp, 0o600)
    if os.geteuid() == 0:
        with contextlib.suppress(KeyError):
            account = pwd.getpwnam("sudarshan")
            os.chown(temp, account.pw_uid, account.pw_gid)
    os.replace(temp, target)
    return target


# ---- encrypt and upload -----------------------------------------------------------------------


def encrypt(archive: Path, dest: Path, certificate: str, work: Path, log_file: Path) -> None:
    """openssl CMS, streamed (a database-sized archive never sits in memory)."""
    openssl = shutil.which("openssl")
    if not openssl:
        raise OffsiteError("openssl")
    cert = work / "offsite-recipient.pem"
    cert.write_text(certificate, encoding="ascii")
    command = [
        openssl, "cms", "-encrypt", "-binary", "-stream", "-aes-256-cbc",
        "-outform", "DER", "-in", str(archive), "-out", str(dest),
        "-recip", str(cert), "-keyopt", "rsa_padding_mode:oaep",
    ]  # fmt: skip
    try:
        fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as sink:
            done = subprocess.run(  # noqa: S603 - a fixed command, no shell, no owner text in it
                command, stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT,
                timeout=3600, check=False,
            )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        raise OffsiteError("encrypt") from None
    finally:
        cert.unlink(missing_ok=True)
    if done.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        raise OffsiteError("encrypt")


def upload(par_url: str, path: Path, name: str, timeout: float = SOCKET_TIMEOUT_S) -> None:
    """One PUT of ``path`` to ``<par_url><name>``. Nothing here names the URL in an error."""
    url = urlsplit(par_url)
    target = url.path.rstrip("/") + "/" + quote(name, safe="")
    if url.scheme == "https":
        conn = http.client.HTTPSConnection(
            url.hostname, url.port, timeout=timeout, context=ssl.create_default_context()
        )
    else:
        conn = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
    try:
        conn.putrequest("PUT", target)
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Content-Length", str(path.stat().st_size))
        conn.endheaders()
        with open(path, "rb") as source:
            while chunk := source.read(CHUNK):
                conn.send(chunk)
        status = conn.getresponse().status
    except (OSError, http.client.HTTPException):
        raise OffsiteError("network") from None
    finally:
        conn.close()
    if status in (200, 201, 204):
        return
    raise OffsiteError("busy" if status == 429 or status >= 500 else "refused")


def send(
    *,
    etc_dir: Path,
    archive: Path,
    work: Path,
    log_file: Path,
    release: str | None,
    today: date,
    free_bytes: int,
    sleep: Callable[[float], None] = time.sleep,
) -> Outcome:
    """Encrypt and upload one archive. Never raises: the outcome says what happened."""
    try:
        config = load(etc_dir)
    except OffsiteError as exc:
        return Outcome("failed", exc.reason)
    if config is None:
        return Outcome("off")
    left = (config.expires - today).days
    if left < 0:
        return Outcome("failed", "expired", left, config.expires)
    warn = left if left <= WARN_DAYS else None
    name = object_name(archive.name, release)
    sealed = work / f"offsite-{name}"
    try:
        work.mkdir(mode=0o700, parents=True, exist_ok=True)
        if free_bytes < archive.stat().st_size + (64 << 20):
            raise OffsiteError("space")
        encrypt(archive, sealed, config.certificate, work, log_file)
        for attempt in range(1, TRIES + 1):
            try:
                upload(config.par_url, sealed, name)
                break
            except OffsiteError as exc:
                if exc.reason not in ("network", "busy") or attempt == TRIES:
                    raise
                sleep(PAUSE_S)
        log.info("offsite: %s uploaded (%d bytes)", name, sealed.stat().st_size)
        return Outcome("ok", "", warn, config.expires)
    except OffsiteError as exc:
        log.warning("offsite: %s was not uploaded (%s)", name, exc.reason)
        return Outcome("failed", exc.reason, warn, config.expires)
    except OSError:
        log.warning("offsite: %s was not uploaded (local file problem)", name)
        return Outcome("failed", "encrypt", warn, config.expires)
    finally:
        sealed.unlink(missing_ok=True)


# ---- the owner's one-time setup ---------------------------------------------------------------


def configure(etc_dir: Path, certificate_file: Path) -> int:
    """``--configure``: asks for the PAR URL (not shown as it is typed) and its expiry date, reads
    the public certificate from a file, checks all three and writes the settings."""
    try:
        certificate = certificate_file.read_text(encoding="ascii")
        expires = date.fromisoformat(input("PAR expiry date (YYYY-MM-DD): ").strip())
    except (OSError, ValueError):
        sys.stderr.write("The certificate file or the date could not be read.\n")
        return 2
    config = Config(
        getpass.getpass("PAR URL (hidden): ").strip(), expires, certificate.strip() + "\n"
    )
    problem = _problem(config)
    if problem:
        sys.stderr.write(f"Not saved: {problem}.\n")
        return 2
    path = write_config(etc_dir, config)
    sys.stdout.write(f"Saved {path} (mode 600). Run the check to see it: backup --check\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m sudarshan.ops.offsite")
    parser.add_argument(
        "--configure",
        metavar="CERTIFICATE",
        help="save the offsite settings (run with sudo; asks for the PAR URL and its expiry date)",
    )
    parser.add_argument("--etc-dir", default="/etc/sudarshan", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.configure:
        parser.print_help()
        return 2
    return configure(Path(args.etc_dir), Path(args.configure))


if __name__ == "__main__":
    sys.exit(main())
