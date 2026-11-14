"""The offsite copy of the nightly backup (M18.11, part 2): encrypted on the server, then uploaded.

    local archive (backup.py) -> openssl CMS encrypt to the owner's PUBLIC certificate
      -> one PUT to a write-only pre-authenticated request (PAR) URL of one OCI bucket

What the server holds, in ``/etc/sudarshan/offsite.json`` (owner ``sudarshan``, mode 600, never in
the repository, never in a log line, a notice or the archive itself):

    par_url      the write-only PAR URL of the bucket, ending in ``/o/``
    expires      the PAR's expiry date as the console shows it, ``YYYY-MM-DD``: not used from then
    certificate  the public X.509 certificate (PEM) the archive is encrypted to

The server holds no OCI credential and no decryption key. The private key is made on the owner's
laptop and never reaches the server, so a server that is taken over cannot read the offsite
copies. It can still upload with the PAR: new objects under any name, junk or a forged archive
(the certificate is public, so a forged copy decrypts and passes ``backup --verify``), also under
the name of a copy already there. The bucket keeps versions (README, step 2), so such an upload
becomes the newest version and the copy already uploaded stays, as a previous version, until the
owner's lifecycle rule deletes it 30 days after it was replaced (step 3); the PAR cannot read, list
or delete anything. A restore takes a version by the upload time Oracle shows, from before anything
suspicious happened, which ignores whatever was uploaded later. This code never deletes an object.

Why openssl CMS and not ``age``: it is on every Ubuntu already, so there is nothing to install and
nothing for the job to refuse over. The cipher is AES-256-CBC with an RSA-OAEP wrapped key. CBC does
not authenticate by itself; the archive inside carries a manifest of SHA-256 sums that
``backup --verify`` checks after the owner decrypts it, which catches a damaged file. It cannot tell
a forged one (whoever holds the PAR can make an archive whose manifest matches): the bucket's
versions and their upload times do that.

A PAR expires. The date in the file is the expiry date the console shows, which has a time of day,
so it is the first day the link is not used (review L1). From 15 days before it the owner is told
once a day; from that date the job does not try, and says so. Without the file, or with the empty
file the set-up makes first, the step is off: one line in the journal a day.
"""

from __future__ import annotations

import contextlib
import errno
import getpass
import hashlib
import http.client
import json
import logging
import os
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from .files import read_regular

log = logging.getLogger("sudarshan.backup")

IST = ZoneInfo("Asia/Kolkata")
CONFIG_NAME = "offsite.json"
#: The owner is told every day from this many days before the expiry date (review L1: the date is
#: the first day the link is not used, so the last copy goes the day before it).
WARN_DAYS = 15
#: How long to wait for an answer the storage may have sent before the connection closed.
EARLY_ANSWER_S = 10.0
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MAX_CONFIG = 32 << 10
MAX_CERT = 64 << 10
CHUNK = 1 << 20
TRIES = 3
PAUSE_S = 30.0
SOCKET_TIMEOUT_S = 300.0
#: What each failure is called in the notice (backup.py records the code; alerts words it).
REASONS = ("openssl", "encrypt", "space", "network", "refused", "busy", "expired", "config")
_PAR_END = re.compile(r".*/o/?")
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_CERT_BEGIN = "-----BEGIN CERTIFICATE-----"
_PEM_CERT = re.compile(
    r"-----BEGIN CERTIFICATE-----\r?\n[A-Za-z0-9+/=\r\n]+?-----END CERTIFICATE-----"
)


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
    days_left: int | None = None  # set from 15 days before the expiry, also when the upload worked
    expires: date | None = None


def say_date(day: date) -> str:
    """'26 Nov 2026', whatever the locale."""
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


#: A financial year's archive is uploaded under this prefix instead of the nightly one, so the
#: owner's lifecycle rules, which name the nightly prefix, never expire it (M18.11, M19.10).
FY_PREFIX = "sudarshan-fy-"
NIGHTLY_PREFIX = "sudarshan-backup-"
_FY = re.compile(r"[0-9]{4}-[0-9]{2}")


def object_name(archive: str, release: str | None, fy: str | None = None) -> str:
    """``sudarshan-backup-20261109-163102-v0.7.0.tar.gz.cms``: the date and the release, no host.
    With ``fy`` (``2026-27``), the financial year's archive:
    ``sudarshan-fy-2026-27-20270331-163102-v0.13.1.tar.gz.cms``."""
    stem = archive[: -len(".tar.gz")] if archive.endswith(".tar.gz") else archive
    partial = "-partial" if stem.endswith("-partial") else ""
    stem = stem.removesuffix("-partial")
    if fy and _FY.fullmatch(fy) and not partial and stem.startswith(NIGHTLY_PREFIX):
        stem = f"{FY_PREFIX}{fy}-{stem.removeprefix(NIGHTLY_PREFIX)}"
    return f"{stem}-{release or 'norelease'}{partial}.tar.gz.cms"


# ---- the file ---------------------------------------------------------------------------------


def load(etc_dir: Path) -> Config | None:
    """The settings, or None when the file is not there or is empty (the set-up makes it empty as
    root, then fills it in as ``sudarshan``). A file that is there and cannot be used raises
    ``OffsiteError("config")``, whatever the reason: one this user cannot read, a link, a folder,
    or unreadable settings. The owner meant it to work, so he is told (review L2)."""
    path = etc_dir / CONFIG_NAME
    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        log.error("%s cannot be looked at by this user", CONFIG_NAME)
        raise OffsiteError("config") from None
    raw = read_regular(path, MAX_CONFIG)
    if raw is None:
        log.error("%s is there but is not a plain file this user can read", CONFIG_NAME)
        raise OffsiteError("config")
    if not raw.strip():
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


def render(config: Config) -> bytes:
    """The settings file's whole content."""
    document = {
        "par_url": config.par_url,
        "expires": config.expires.isoformat(),
        "certificate": config.certificate,
    }
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def _not_there(path: Path) -> str:
    return (
        f"Not saved: {path} is not there. The .bat makes it first; by hand: "
        f"sudo install -o sudarshan -g sudarshan -m 600 /dev/null {path}"
    )


def _remove_and_again(path: Path, what: str) -> str:
    return (
        f"Not saved: {path} {what}. Remove it (sudo rm -r {path}) and run the .bat again: "
        "it makes the file afresh."
    )


def _not_this_users(path: Path) -> str:
    """The file is not the running user's to write: either this was not run as sudarshan, or the
    file was made by someone else. Both are named, since either can be the cause."""
    return (
        f"Not saved: {path} is not this user's to write. The .bat runs this as sudarshan; if it "
        f"did, remove the file (sudo rm {path}) and run the .bat again: it makes the file afresh."
    )


def _kind_problem(path: Path, info: os.stat_result, euid: int) -> str:
    """What is wrong with the file the settings go into; "" when nothing is."""
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return _remove_and_again(path, "is not a plain file")
    if info.st_uid != euid:
        return _not_this_users(path)
    if info.st_mode & 0o077:
        return (
            f"Not saved: other users can read {path}. Make it private (sudo chmod 600 {path}) "
            "and run the .bat again."
        )
    return ""


def settings_file_problem(path: Path, euid: int | None = None) -> str:
    """Whether the settings could be written into ``path`` now, looked at without opening it;
    "" when they could. Asked before the owner types anything."""
    euid = os.geteuid() if euid is None else euid
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return _not_there(path)
    except OSError:
        return _remove_and_again(path, "cannot be looked at")
    return _kind_problem(path, info, euid)


def write_settings(path: Path, config: Config, *, euid: int | None = None) -> str:
    """Write the settings into the file the set-up made, in place; "" when done, else why not.

    Root makes the file, empty, owner ``sudarshan``, mode 600, with coreutils (``install``), and
    runs no Python from the app folder (review H2). This runs as the file's owner and never
    replaces the file or changes its owner or mode. It refuses a file that is not there (never
    made here), a link (never followed), anything but a plain file, a file of another user, and
    one other users could read.
    """
    euid = os.geteuid() if euid is None else euid
    flags = os.O_WRONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return _not_there(path)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM):
            return _not_this_users(path)
        return _remove_and_again(path, "is not a plain file")  # a link, a folder, a pipe
    try:
        problem = _kind_problem(path, os.fstat(fd), euid)
        if problem:
            return problem
        try:
            os.ftruncate(fd, 0)
            view = memoryview(render(config))
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        except OSError:
            with contextlib.suppress(OSError):
                os.ftruncate(fd, 0)  # empty reads as "not set up", never as half a file
            return (
                f"Not saved: {path} could not be written. Check that the server's disk has free "
                "space (df -h /etc), then run the .bat again."
            )
    finally:
        os.close(fd)
    return ""


def first_certificate(text: str) -> str | None:
    """The first PEM certificate in ``text``, alone and tidy; None when there is none."""
    found = _PEM_CERT.search(text)
    return found.group(0).replace("\r\n", "\n") + "\n" if found else None


def fingerprint(certificate: str) -> str:
    """The certificate's SHA-256 fingerprint as ``openssl x509 -fingerprint -sha256`` prints it
    (``AB:CD:...``), so the owner can hold the server's against his laptop's (review M1)."""
    block = first_certificate(certificate)
    if block is None:
        raise ValueError("not a certificate")
    digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(block)).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def can_encrypt_to(certificate: str) -> bool:
    """Run the nightly encryption once, on a few bytes, to ``certificate`` (review L3): an EC key
    passes every look at the PEM and then fails every night. Raises ``OffsiteError("openssl")``
    when there is no openssl. The trial runs in a private folder of its own with a random name."""
    with tempfile.TemporaryDirectory(prefix="sudarshan-offsite-") as folder:
        work = Path(folder)
        sample = work / "sample.txt"
        sample.write_bytes(b"SUDARSHAN offsite set-up check\n")
        try:
            encrypt(sample, work / "sample.cms", certificate, work, work / "openssl.log")
        except OffsiteError as exc:
            if exc.reason == "openssl":
                raise
            return False
    return True


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
        status = _early_answer(conn)
        if status is None:
            raise OffsiteError("network") from None
    finally:
        conn.close()
    if status in (200, 201, 204):
        return
    raise OffsiteError("busy" if status == 429 or status >= 500 else "refused")


def _early_answer(conn: http.client.HTTPConnection) -> int | None:
    """The status of an answer the storage sent before it read the whole file, or None when none
    came (review L4). A link that is gone is refused as soon as the request's headers arrive, and
    the connection is closed under the upload: that is a refusal, not a network fault, and trying
    it again changes nothing."""
    if conn.sock is None:  # never connected
        return None
    try:
        conn.sock.settimeout(EARLY_ANSWER_S)
        return conn.getresponse().status
    except Exception:
        return None


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
    fy: str | None = None,
) -> Outcome:
    """Encrypt and upload one archive. Never raises: the outcome says what happened."""
    try:
        config = load(etc_dir)
    except OffsiteError as exc:
        return Outcome("failed", exc.reason)
    if config is None:
        return Outcome("off")
    left = (config.expires - today).days
    if left <= 0:  # the expiry date the console shows: the link may stop at any time that day
        return Outcome("failed", "expired", left, config.expires)
    warn = left if left <= WARN_DAYS else None
    name = object_name(archive.name, release, fy)
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


class Terminal:
    """The owner's terminal (``/dev/tty``). The questions are asked and answered here because
    standard input carries the certificate (review M1); ``getpass`` reads this same terminal, and
    falls back to standard input only when there is no terminal, which ``main`` refuses."""

    def __init__(self, path: str = "/dev/tty"):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | getattr(os, "O_CLOEXEC", 0))

    def _write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[os.write(self.fd, view) :]

    def say(self, text: str) -> None:
        self._write((text + "\n").encode("utf-8", "replace"))

    def ask(self, prompt: str) -> str:
        """One line, as typed (the terminal shows it)."""
        self._write(prompt.encode("utf-8", "replace"))
        line = b""
        while not line.endswith(b"\n") and len(line) < 4096:
            chunk = os.read(self.fd, 4096)
            if not chunk:  # Ctrl-D
                break
            line += chunk
        return line.decode("utf-8", "replace").strip()

    def ask_hidden(self, prompt: str) -> str:
        """One line that is not shown as it is typed or pasted."""
        return getpass.getpass(prompt)

    def close(self) -> None:
        os.close(self.fd)


NOT_ROOT = (
    "Not saved: run this as sudarshan, not as root: cd /opt/sudarshan/apps/openalgo && "
    "sudo -u sudarshan .venv/bin/python -m sudarshan.ops.offsite --configure "
    "< sudarshan-backup.crt (the .bat does this)."
)
CHECK_COMMAND = (
    "cd /opt/sudarshan/apps/openalgo && "
    "sudo -u sudarshan .venv/bin/python -m sudarshan.ops.backup --check"
)


def configure(
    etc_dir: Path,
    certificate_text: str,
    terminal,
    *,
    today: date | None = None,
    euid: int | None = None,
) -> int:
    """``--configure``: check the public certificate that came on standard input, show its
    fingerprint, and once the owner types yes ask for the link's expiry date and the link (not
    shown as it is typed); then write the settings into the file root made. 0 saved, 1 the owner
    stopped it, 2 not saved for a reason it names. Nothing is asked before the certificate and
    the file have been checked, so nothing is typed for nothing."""
    euid = os.geteuid() if euid is None else euid
    say = terminal.say
    path = etc_dir / CONFIG_NAME
    if euid == 0:
        say(NOT_ROOT)
        return 2
    certificate = first_certificate(certificate_text)
    if certificate is None or "PRIVATE KEY" in certificate_text:
        say(
            "Not saved: what came in is not a public certificate. The .bat sends "
            "sudarshan-backup.crt; the .key file never leaves the laptop."
        )
        return 2
    problem = settings_file_problem(path, euid)
    if problem:
        say(problem)
        return 2
    try:
        usable = can_encrypt_to(certificate)
    except OffsiteError:
        say(
            "Not saved: openssl is not installed on the server. Install it "
            "(sudo apt install openssl) and run the .bat again."
        )
        return 2
    if not usable:
        say(
            "Not saved: this certificate cannot be used for the nightly encryption. Make the key "
            "pair exactly as step 7 shows, then run the .bat again."
        )
        return 2
    say("The certificate the server received has this SHA-256 fingerprint:")
    say(f"  {fingerprint(certificate)}")
    say("The laptop printed its own before sending it (after sha256 Fingerprint=).")
    if terminal.ask("Type yes if the two are the same, or press Enter to stop: ").lower() != "yes":
        say("Not saved. Nothing was changed.")
        return 1
    typed = terminal.ask("Expiry date of the upload link, as the console shows it (YYYY-MM-DD): ")
    try:
        expires = date.fromisoformat(typed)
    except ValueError:
        say("Not saved: that is not a date written like 2027-11-12. Run the .bat again.")
        return 2
    if expires <= (today or datetime.now(IST).date()):
        say(
            "Not saved: that date is today or has passed, and the link stops working on it. "
            "Make a new link (steps 4 and 5) and run the .bat again."
        )
        return 2
    link = terminal.ask_hidden(
        "Upload link (nothing shows as you paste or type it; press Enter after it): "
    ).strip()
    config = Config(link, expires, certificate)
    problem = _problem(config)
    if problem:
        say(f"Not saved: {problem}. Run the .bat again.")
        return 2
    problem = write_settings(path, config, euid=euid)
    if problem:
        say(problem)
        return 2
    say("Saved. The next night's backup sends the first offsite copy.")
    say(f"Check it now: {CHECK_COMMAND}")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m sudarshan.ops.offsite")
    parser.add_argument(
        "--configure",
        action="store_true",
        help=(
            "save the offsite settings: run as sudarshan with the public certificate on standard "
            "input (--configure < sudarshan-backup.crt); asks for the link's expiry date and the "
            "link on the terminal"
        ),
    )
    parser.add_argument("--etc-dir", default="/etc/sudarshan", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.configure:
        parser.print_help()
        return 2
    if sys.stdin is None or sys.stdin.isatty():
        sys.stderr.write(
            "Give the public certificate on standard input: ... --configure "
            "< sudarshan-backup.crt (the .bat does this).\n"
        )
        return 2
    text = sys.stdin.buffer.read(MAX_CERT + 1)
    if len(text) > MAX_CERT:
        sys.stderr.write("Not saved: what came in is too long to be a certificate.\n")
        return 2
    try:
        terminal = Terminal()
    except OSError:
        sys.stderr.write(
            "Not saved: this asks questions on a terminal and none is open. Log in with "
            "ssh -t, as the .bat does.\n"
        )
        return 2
    try:
        return configure(Path(args.etc_dir), text.decode("ascii", "replace"), terminal)
    finally:
        terminal.close()


if __name__ == "__main__":
    sys.exit(main())
