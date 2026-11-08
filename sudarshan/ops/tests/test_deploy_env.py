"""M18.2: the OpenAlgo .env settings a release ships, applied by sudarshan-deploy.

``env_sync`` and ``env_restore`` are cut out of the deploy tool exactly as they ship (the pattern of
``sudarshan/autologin/tests/test_autologin_units.py``) and run under the script's own shell
options. The last test runs the whole tool's ``--check`` with a fake ``curl``.
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[3]
DEPLOY_TOOL = REPO / "sudarshan" / "ops" / "deploy" / "sudarshan-deploy"
SHIPPED_LIST = REPO / "sudarshan" / "ops" / "deploy" / "openalgo-env.conf"
LIST = b"WEBSOCKET_BROKER_LINGER_SECONDS  300  0\nMAX_WEBSOCKET_CONNECTIONS  1  3\n"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _function(name: str) -> str:
    text = DEPLOY_TOOL.read_text()
    start = text.index(f"\n{name}() {{") + 1
    return text[start : text.index("\n}\n", start) + 3]


class Server:
    """A fake server: OpenAlgo's .env, the private work dir and this deploy's backup folder."""

    def __init__(self, root: Path, env: bytes | None, settings: bytes = LIST):
        self.root, self.env = root, root / "app" / ".env"
        self.list, self.work = root / "list.conf", root / "work"
        self.backup = root / "bk" / "openalgo.env"
        for folder in (self.env.parent, self.work, self.backup.parent):
            folder.mkdir(parents=True)
        self.work.chmod(0o700)
        self.list.write_bytes(settings)
        if env is not None:
            self.env.write_bytes(env)

    def run(self, *args: str) -> subprocess.CompletedProcess:
        # As the script calls them: set -Eeuo pipefail, from an if (errexit off inside, nounset on),
        # in the UTF-8 locale a systemd service gets on Ubuntu.
        script = (
            'set -Eeuo pipefail\nlog() { echo "$*"; }\n'
            + _function("env_sync")
            + _function("env_restore")
            + 'if "$@"; then exit 0; else exit $?; fi\n'
        )
        return subprocess.run(
            ["bash", "-c", script, "deploy", *args],
            capture_output=True,
            text=True,
            errors="replace",
            cwd=self.root,
            env={**os.environ, "LC_ALL": "C.UTF-8"},
        )

    def sync(self, mode: str = "apply") -> subprocess.CompletedProcess:
        extra = [str(self.backup)] if mode == "apply" else []
        return self.run("env_sync", mode, str(self.list), str(self.env), str(self.work), *extra)

    def restore(self) -> subprocess.CompletedProcess:
        return self.run("env_restore", str(self.work), str(self.env))


def _applied(tmp_path: Path, env: bytes, settings: bytes = LIST) -> tuple[bytes, str]:
    server = Server(tmp_path, env, settings)
    done = server.sync()
    assert done.returncode == 0, done.stdout + done.stderr
    return server.env.read_bytes(), done.stdout


def _read_by_openalgo(env: bytes) -> dict[str, str | None]:
    """What OpenAlgo itself gets from the file: python-dotenv, which it loads .env with."""
    return dotenv_values(stream=io.StringIO(env.decode(errors="replace")))


def test_a_missing_key_is_added(tmp_path):
    original = b"APP_KEY = 'k'\n# MAX_WEBSOCKET_CONNECTIONS='3' is only a comment\n"
    env, out = _applied(tmp_path, original)
    assert env == original + (
        b"WEBSOCKET_BROKER_LINGER_SECONDS='300'\nMAX_WEBSOCKET_CONNECTIONS='1'\n"
    )
    assert "added WEBSOCKET_BROKER_LINGER_SECONDS='300'" in out
    assert "added MAX_WEBSOCKET_CONNECTIONS='1'" in out


@pytest.mark.parametrize(
    ("line", "becomes"),
    [
        (b"MAX_WEBSOCKET_CONNECTIONS='3'", b"MAX_WEBSOCKET_CONNECTIONS='1'"),
        (b"MAX_WEBSOCKET_CONNECTIONS = '3'", b"MAX_WEBSOCKET_CONNECTIONS = '1'"),
        (b'MAX_WEBSOCKET_CONNECTIONS="3"', b"MAX_WEBSOCKET_CONNECTIONS='1'"),
        (b"MAX_WEBSOCKET_CONNECTIONS=3", b"MAX_WEBSOCKET_CONNECTIONS='1'"),
        (b"export MAX_WEBSOCKET_CONNECTIONS=3  # upstream", b"export MAX_WEBSOCKET_CONNECTIONS='1'  # upstream"),
        (b"  MAX_WEBSOCKET_CONNECTIONS\t= '3'#x ", b"  MAX_WEBSOCKET_CONNECTIONS\t= '1'#x "),
        (b"'MAX_WEBSOCKET_CONNECTIONS'='3'", b"'MAX_WEBSOCKET_CONNECTIONS'='1'"),
        (b"MAX_WEBSOCKET_CONNECTIONS='3' # stray byte \xfe", b"MAX_WEBSOCKET_CONNECTIONS='1' # stray byte \xfe"),
    ],
)  # fmt: skip
def test_a_shipped_default_is_replaced(tmp_path, line, becomes):
    original = b"A='x'\n" + line + b"\nWEBSOCKET_BROKER_LINGER_SECONDS='0'\n"
    assert _read_by_openalgo(original)["MAX_WEBSOCKET_CONNECTIONS"] == "3"  # the form really says 3
    env, out = _applied(tmp_path, original)
    assert env == b"A='x'\n" + becomes + b"\nWEBSOCKET_BROKER_LINGER_SECONDS='300'\n"
    assert "set MAX_WEBSOCKET_CONNECTIONS='1'" in out
    read = _read_by_openalgo(env)
    assert read["MAX_WEBSOCKET_CONNECTIONS"] == "1"
    assert read["WEBSOCKET_BROKER_LINGER_SECONDS"] == "300"


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        (b"MAX_WEBSOCKET_CONNECTIONS='2'", "the owner chose its value"),
        (b"MAX_WEBSOCKET_CONNECTIONS = 2  # mine", "the owner chose its value"),
        (b"MAX_WEBSOCKET_CONNECTIONS=''", "the owner chose its value"),
        (b"MAX_WEBSOCKET_CONNECTIONS='3 '", "the owner chose its value"),
        (b"MAX_WEBSOCKET_CONNECTIONS=3#x", "the owner chose its value"),  # python-dotenv reads 3#x
        (b"MAX_WEBSOCKET_CONNECTIONS='2' # stray byte \xff", "the owner chose its value"),
        (b"MAX_WEBSOCKET_CONNECTIONS='3'\nMAX_WEBSOCKET_CONNECTIONS='3'", "sets it 2 times"),
        (b"MAX_WEBSOCKET_CONNECTIONS='3' trailing", "a form this tool does not rewrite"),
        (b'MAX_WEBSOCKET_CONNECTIONS="${OTHER}"', "a form this tool does not rewrite"),
    ],
)
def test_an_owner_value_is_left_alone(tmp_path, line, reason):
    original = b"A='x'\n" + line + b"\nWEBSOCKET_BROKER_LINGER_SECONDS='300'\n"
    env, out = _applied(tmp_path, original)
    assert env == original
    assert "MAX_WEBSOCKET_CONNECTIONS left alone: " in out and reason in out
    assert "nothing to change" in out


def test_a_second_run_changes_nothing(tmp_path):
    server = Server(tmp_path, b"MAX_WEBSOCKET_CONNECTIONS='3'\n")
    assert server.sync().returncode == 0
    before = server.env.stat()
    again = server.sync()
    assert again.returncode == 0
    assert "nothing to change" in again.stdout
    after = server.env.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)  # not rewritten


def test_every_other_line_stays_byte_for_byte(tmp_path):
    head = (
        b"# OpenAlgo .env\nENV_CONFIG_VERSION = '1.0.7'\n\n   # indented, trailing spaces   \n"
        b"APP_KEY = 'abc\\def'  # a backslash\nBROKER_API_SECRET=\"p@ss#word\"\nTABS\t=\t'x'\t\n"
        b"NOTE='\xe0\xa4\xb8\xe0\xa5\x81'\nRAW=\xff\xfe\x00z\n"
    )
    tail = b"ENABLE_CONNECTION_POOLING='true'\nLAST = 'no newline at the end'"
    env, _ = _applied(tmp_path, head + b"MAX_WEBSOCKET_CONNECTIONS = '3'\n" + tail)
    assert env == (
        head + b"MAX_WEBSOCKET_CONNECTIONS = '1'\n" + tail + b"\n"
        b"WEBSOCKET_BROKER_LINGER_SECONDS='300'\n"
    )


def test_windows_line_endings_are_kept(tmp_path):
    env, _ = _applied(tmp_path, b"A='x'\r\nMAX_WEBSOCKET_CONNECTIONS='3'\r\n")
    expected = (
        b"A='x'\r\nMAX_WEBSOCKET_CONNECTIONS='1'\r\nWEBSOCKET_BROKER_LINGER_SECONDS='300'\r\n"
    )
    assert env == expected


def _other_owner() -> tuple[int, int]:
    """Ids a test may give a file: any as root, else this user and one of their other groups."""
    if os.geteuid() == 0:
        return 4242, 4343
    groups = [g for g in os.getgroups() if g != os.getegid()]
    if not groups:
        pytest.skip("needs root or a second group to tell the group apart")
    return os.geteuid(), groups[0]


def test_owner_group_and_mode_are_kept(tmp_path):
    uid, gid = _other_owner()
    server = Server(tmp_path, b"MAX_WEBSOCKET_CONNECTIONS='3'\n")
    os.chown(server.env, uid, gid)
    server.env.chmod(0o640)
    done = server.sync()
    assert done.returncode == 0, done.stdout + done.stderr
    live, backup = server.env.stat(), server.backup.stat()
    assert (live.st_uid, live.st_gid, stat.S_IMODE(live.st_mode)) == (uid, gid, 0o640)
    assert (backup.st_uid, backup.st_gid, stat.S_IMODE(backup.st_mode)) == (uid, gid, 0o600)
    assert server.backup.read_bytes() == b"MAX_WEBSOCKET_CONNECTIONS='3'\n"


def test_no_env_value_is_ever_printed(tmp_path):
    secrets = [b"pepper-zq1", b"broker-zq2", b"owner-zq3", b"odd-zq4", b"twice-zq5"]
    server = Server(
        tmp_path,
        b"API_KEY_PEPPER = 'pepper-zq1'\nBROKER_API_SECRET=\"broker-zq2\"\n"
        b"MAX_WEBSOCKET_CONNECTIONS='owner-zq3'\nWEBSOCKET_BROKER_LINGER_SECONDS='odd-zq4' x\n"
        b"OTHER_MANAGED=twice-zq5\nOTHER_MANAGED=twice-zq5\n",
        LIST + b"OTHER_MANAGED  on  off\n",
    )
    runs = [server.sync("check"), server.sync(), server.sync(), server.restore()]
    shown = "".join(r.stdout + r.stderr for r in runs)
    assert all(r.returncode == 0 for r in runs), shown
    assert "left alone" in shown  # every managed key was looked at
    for secret in secrets:
        assert secret.decode() not in shown


def test_rollback_puts_the_original_back(tmp_path):
    uid, gid = _other_owner()
    original = b"APP_KEY='k'\nMAX_WEBSOCKET_CONNECTIONS='3'\n"
    server = Server(tmp_path, original)
    os.chown(server.env, uid, gid)
    server.env.chmod(0o640)
    assert server.sync().returncode == 0
    assert server.env.read_bytes() != original
    # The failed start rewrote .env, as OpenAlgo does when it saves a FERNET_SALT (mode 600).
    server.env.write_bytes(b"FERNET_SALT='new'\n")
    server.env.chmod(0o600)
    done = server.restore()
    assert done.returncode == 0, done.stdout + done.stderr
    restored = server.env.stat()
    assert server.env.read_bytes() == original
    assert (restored.st_uid, restored.st_gid, stat.S_IMODE(restored.st_mode)) == (uid, gid, 0o640)


def test_rollback_without_a_settings_step_changes_nothing(tmp_path):
    server = Server(tmp_path, b"A='x'\n")
    before = server.env.stat()
    assert server.restore().returncode == 0
    assert server.env.stat().st_ino == before.st_ino


def _snapshot(*roots: Path) -> dict[str, tuple]:
    """Every file and folder under ``roots``: bytes, inode, mtime and mode."""
    return {
        str(p): (p.read_bytes() if p.is_file() else b"", p.stat().st_ino, p.stat().st_mtime_ns,
                 p.stat().st_mode)
        for root in roots for p in [root, *(sorted(root.rglob("*")) if root.is_dir() else [])]
    }  # fmt: skip


def test_check_changes_nothing(tmp_path):
    server = Server(tmp_path, b"APP_KEY='k'\nMAX_WEBSOCKET_CONNECTIONS='3'\n")
    before = _snapshot(server.env.parent, server.backup.parent, server.list)
    done = server.sync("check")
    assert done.returncode == 0, done.stdout + done.stderr
    assert _snapshot(server.env.parent, server.backup.parent, server.list) == before
    assert list(server.work.iterdir()) == []  # its private copy of .env is gone again
    assert "would set MAX_WEBSOCKET_CONNECTIONS='1'" in done.stdout
    assert "would add WEBSOCKET_BROKER_LINGER_SECONDS='300'" in done.stdout


def test_no_env_file_changes_nothing(tmp_path):
    server = Server(tmp_path, None)
    done = server.sync()
    assert done.returncode == 0
    assert done.stdout.count("\n") == 1 and "there is no" in done.stdout
    assert not server.env.exists() and not server.backup.exists()


def test_a_symlinked_env_is_not_followed(tmp_path):
    secret = tmp_path / "elsewhere"
    secret.write_bytes(b"MAX_WEBSOCKET_CONNECTIONS='3'\nKEY='zq-private'\n")
    server = Server(tmp_path, None)
    server.env.symlink_to(secret)
    for mode in ("check", "apply"):
        done = server.sync(mode)
        assert done.returncode == 0
        assert "not a plain file" in done.stdout and "zq-private" not in done.stdout
    assert server.env.is_symlink()
    assert secret.read_bytes() == b"MAX_WEBSOCKET_CONNECTIONS='3'\nKEY='zq-private'\n"
    assert not server.backup.exists()


def test_a_value_running_over_lines_leaves_the_file_alone(tmp_path):
    original = b"CERT='-----BEGIN\nMAX_WEBSOCKET_CONNECTIONS='3'\n-----END'\n"
    env, out = _applied(tmp_path, original)
    assert env == original
    assert "running over several lines" in out


@pytest.mark.parametrize(
    "bad",
    [
        b"MAX_WEBSOCKET_CONNECTIONS\n",
        b"9KEY 1\n",
        b"KEY 1 2\nKEY 3\n",
        b"KEY 'one'\n",
        b"KEY a$b\n",
        b'KEY "1"\n',
        b"KEY 1 3 *\n",
    ],
)
def test_a_bad_list_is_refused(tmp_path, bad):
    server = Server(tmp_path, b"MAX_WEBSOCKET_CONNECTIONS='3'\n", bad)
    done = server.sync()
    assert done.returncode == 1
    assert "settings: list line" in done.stdout
    assert server.env.read_bytes() == b"MAX_WEBSOCKET_CONNECTIONS='3'\n"
    assert not server.backup.exists()


def test_the_shipped_list_on_a_fresh_install(tmp_path):
    """On a .env made from .sample.env, the shipped list changes exactly its own keys."""
    sample = (REPO / ".sample.env").read_bytes()
    env, out = _applied(tmp_path, sample, SHIPPED_LIST.read_bytes())
    before, after = _read_by_openalgo(sample), _read_by_openalgo(env)
    assert after["MAX_WEBSOCKET_CONNECTIONS"] == "1"
    assert after["WEBSOCKET_BROKER_LINGER_SECONDS"] == "300"
    assert after["SUDARSHAN_RISK_GUARD"] == "sandbox"  # M5.13: Analyze mode first
    assert after["UPSTOX_ORDER_MAX_PER_SECOND"] == "5"  # M5.14: orders paced at 5 a second
    managed = {
        "MAX_WEBSOCKET_CONNECTIONS",
        "WEBSOCKET_BROKER_LINGER_SECONDS",
        "SUDARSHAN_RISK_GUARD",
        "UPSTOX_ORDER_MAX_PER_SECOND",
    }
    assert {k: v for k, v in after.items() if k not in managed} == {
        k: v for k, v in before.items() if k not in managed
    }
    assert "left alone" not in out


def test_the_shipped_list_is_whole_and_reads(tmp_path):
    """CI: the list in the repo is not empty, ends with a newline (the deploy tool takes a list
    only when it arrived whole, and one it cannot take stops every install of the release) and
    reads with the tool's own parser."""
    data = SHIPPED_LIST.read_bytes()
    assert data.strip(), "the settings list is empty"
    assert data.endswith(b"\n"), "the settings list does not end with a newline"
    done = Server(tmp_path, (REPO / ".sample.env").read_bytes(), data).sync("check")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "list line" not in done.stdout


def test_an_empty_list_changes_nothing(tmp_path):
    """A release whose settings list is empty (or missing) sets nothing."""
    env, out = _applied(tmp_path, b"MAX_WEBSOCKET_CONNECTIONS='3'\n", b"")
    assert env == b"MAX_WEBSOCKET_CONNECTIONS='3'\n"
    assert out.startswith("settings: nothing to change in "), out


def test_the_deploy_applies_it_between_checkout_and_start():
    text = DEPLOY_TOOL.read_text()
    begin = text.index("\nrollback() {")
    rollback = text[begin : text.index("\n}\n", begin)]
    assert 'env_restore "${tmp}/env" "${ENV_FILE}"' in rollback
    stop = text.index('\nsystemctl stop "${SERVICE}"\n')
    checkout = text.index('checkout -f --detach "refs/tags/${tag}"')
    apply = text.index("if ! env_sync apply ")
    start = text.index('\nsystemctl start "${SERVICE}"\n')
    assert text.index('env_sync check "${env_conf}"') < stop < checkout < apply < start
    assert 'env_sync apply "${env_conf}" "${ENV_FILE}" "${tmp}/env" "${bdir}/openalgo.env"' in text


FAKE_CURL = r"""#!/usr/bin/env bash
# A fake curl for --check: the latest release, and the settings list at its tag.
out="" write="" url=""
while [[ $# -gt 0 ]]; do
    case "$1" in -o) out="$2"; shift 2 ;; -w) write="$2"; shift 2 ;; -H) shift 2 ;; -*) shift ;; *) url="$1"; shift ;; esac
done
echo "${url}" >> "${FAKE_CURL_LOG}"
case "${url}" in
    https://api.github.com/repos/owner/sudarshan/releases/latest)
        body='{"tag_name": "v9.9.9", "assets": [{"name": "frontend-dist.tar.gz", "browser_download_url": "https://example.invalid/a"}]}'
        code=200 ;;
    https://raw.githubusercontent.com/owner/sudarshan/v9.9.9/sudarshan/ops/deploy/openalgo-env.conf)
        code="${FAKE_LIST_CODE}"; body="$(cat "${FAKE_LIST}")" ;;
    *) code=404; body="" ;;
esac
if [[ "${code}" == "000" ]]; then [[ -n "${write}" ]] && printf '000'; exit 7; fi
if [[ -n "${out}" ]]; then printf '%s\n' "${body}" > "${out}"; else printf '%s\n' "${body}"; fi
[[ -n "${write}" ]] && printf '%s' "${code}"
exit 0
"""


@pytest.mark.skipif(
    shutil.which("jq") is None or shutil.which("flock") is None, reason="needs jq and flock"
)
@pytest.mark.parametrize(
    ("list_code", "says"),
    [
        ("200", ["would set MAX_WEBSOCKET_CONNECTIONS='1'", "would add WEBSOCKET_BROKER_LINGER_SECONDS='300'",
                 "would add SUDARSHAN_RISK_GUARD='sandbox'",
                 "would add UPSTOX_ORDER_MAX_PER_SECOND='5'"]),
        ("404", ["settings: nothing to change in"]),
        ("000", ["settings: could not fetch the list of v9.9.9"]),
    ],
)  # fmt: skip
def test_the_check_option_reports_without_changing(tmp_path, list_code, says):
    """The whole tool with --check: it reports what the latest release would change, and only that."""
    etc, state, app, bin_dir = (tmp_path / d for d in ("etc", "state", "app", "bin"))
    for folder in (etc, state, app, bin_dir, tmp_path / "tmp"):
        folder.mkdir()
    (etc / "deploy.env").write_text(
        f"REPO=https://github.com/owner/sudarshan\nASSET=frontend-dist.tar.gz\nAPP_DIR={app}\n"
        "SVC_USER=nobody\nSERVICE=openalgo\n"
    )
    (etc / "nse_holidays.txt").write_text("")
    (app / ".env").write_bytes(b"APP_KEY = 'zq-secret'\nMAX_WEBSOCKET_CONNECTIONS='3'\n")
    (bin_dir / "curl").write_text(FAKE_CURL)
    (bin_dir / "curl").chmod(0o755)
    script = (
        DEPLOY_TOOL.read_text()
        .replace("/etc/sudarshan/", f"{etc}/")
        .replace('STATE="/var/lib/sudarshan"', f'STATE="{state}"')
    )
    before = _snapshot(app)
    done = subprocess.run(
        ["bash", "-c", script, "sudarshan-deploy", "--check"],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "TMPDIR": str(tmp_path / "tmp"),
            "FAKE_CURL_LOG": str(tmp_path / "curl.log"),
            "FAKE_LIST": str(SHIPPED_LIST),
            "FAKE_LIST_CODE": list_code,
        },
    )
    shown = done.stdout + done.stderr
    assert done.returncode == 0, shown
    assert "latest release: v9.9.9 | deployed: none" in done.stdout
    for line in says:
        assert line in done.stdout, shown
    assert "zq-secret" not in shown
    assert _snapshot(app) == before
    assert sorted(p.name for p in state.iterdir()) == ["deploy.lock"]
    assert list((tmp_path / "tmp").iterdir()) == []  # its private copy went with its temp folder
    assert "/v9.9.9/sudarshan/ops/deploy/openalgo-env.conf" in (tmp_path / "curl.log").read_text()
