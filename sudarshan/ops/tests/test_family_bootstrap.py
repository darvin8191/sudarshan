"""O2' (cross-design Y1, Y3.1, Y25.3): the owner's one-time set-up, ``ops/family/bootstrap.sh``.

The script runs on a folder of the test's own (``BOOTSTRAP_ROOT``, put before every path it reads or
writes) with fakes first on PATH: ``id`` and ``useradd`` keep a small account list, ``chown`` records
the owner asked for (the accounts are not on this machine) and ``stat`` reports it, ``systemctl`` and
``nft`` record what they were asked and keep the unit's state, and a fake deploy tool says what a
provision step would. What these pin: what it makes and with which owners and modes, that a second
run changes nothing and says so, that a dry run changes nothing, that it stops before changing
anything when what it needs is missing, that it names OpenAlgo's open folders without changing
them, and that it never touches another table, unit or service.

The allow-list itself is loaded for real in a network namespace of its own (root, nft and unshare
needed) and met by the family check's own connection lines, run as another account.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "sudarshan" / "ops" / "family" / "bootstrap.sh"
ACCOUNT = "sudarshan-family"
FW_UNIT = "nft-sudarshan-family.service"
APP_DIR = "/opt/app"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

FAKES = {
    # accounts: "name uid group..." lines in FAKE/passwd
    "id": r"""
if [[ $# -le 1 && ( $# -eq 0 || "$1" == -u ) ]]; then exec REAL_ID "$@"; fi
line="$(grep -E "^$2 " "${FAKE}/passwd" 2>/dev/null | head -n 1)" || exit 1
[[ -n "${line}" ]] || { echo "id: '$2': no such user" >&2; exit 1; }
read -r -a f <<< "${line}"
case "$1" in -u) echo "${f[1]}" ;; -nG) echo "${f[@]:2}" ;; *) exit 2 ;; esac
""",
    "useradd": r"""
echo "$*" >> "${FAKE}/useradd"
echo "${@: -1} 999 ${@: -1}" >> "${FAKE}/passwd"
""",
    # records the owner asked for, for the fake stat; never changes it
    "chown": r"""
echo "$*" >> "${FAKE}/chown"
args=(); for a in "$@"; do [[ "$a" == -* ]] || args+=("$a"); done
for p in "${args[@]:1}"; do
    printf '%s\n' "${args[0]}" > "${FAKE}/owner-$(printf %s "$p" | sha256sum | cut -c1-16)"
done
""",
    "stat": r"""
if [[ "$1" == -c && "$2" == "%U:%G %a" && "$3" == -- ]]; then
    mode="$(REAL_STAT -c %a -- "$4")" || exit 1
    rec="${FAKE}/owner-$(printf %s "$4" | sha256sum | cut -c1-16)"
    if [[ -f "${rec}" ]]; then echo "$(cat "${rec}") ${mode}"; else REAL_STAT -c '%U:%G %a' -- "$4"; fi
    exit 0
fi
exec REAL_STAT "$@"
""",
    "systemctl": r"""
echo "$*" >> "${FAKE}/systemctl"
case "$1" in
    is-enabled) if [[ -f "${FAKE}/enabled-$2" ]]; then echo enabled; else echo disabled; exit 1; fi ;;
    is-active) [[ -f "${FAKE}/active-${@: -1}" ]] ;;
    enable) touch "${FAKE}/enabled-$2" ;;
    restart) touch "${FAKE}/active-$2" "${FAKE}/loaded" ;;
    show|daemon-reload) ;;
    *) exit 1 ;;
esac
""",
    "nft": r"""
echo "$*" >> "${FAKE}/nft"
case "$1" in
    list) [[ -f "${FAKE}/loaded" ]] ;;
    -c) grep -q "table inet sudarshan_family {" "$3" && cp "$3" "${FAKE}/checked.nft" ;;
    *) exit 1 ;;
esac
""",
}
TOOL = r"""#!/usr/bin/env bash
# a deploy tool that knows the family step: --family-provision) is the line the set-up looks for
echo "$*" >> "${FAKE}/tool"
case "$(cat "${FAKE}/tool_mode" 2>/dev/null || echo pass)" in
    pass) echo "family: written in /etc/sudarshan-family: seal-admin.key store.key"
          echo "family: the check passed: /etc/sudarshan-family/provisioned written, so the family view may start"
          echo "family: PASS: the family account is set up"; exit 0 ;;
    same) echo "family: nothing to write: every key and copy in /etc/sudarshan-family was already in place"
          echo "family: the check passed; /etc/sudarshan-family/provisioned was already there"
          echo "family: PASS: the family account is set up"; exit 0 ;;
    fail) echo "family: check: FAIL OpenAlgo's db folder: this account can open /opt/app/db/openalgo.db"
          echo "family: FAIL: OpenAlgo's db folder: this account can open /opt/app/db/openalgo.db"; exit 1 ;;
    busy) echo "An install is still running. Nothing was changed: run the set-up again in a few minutes."; exit 75 ;;
esac
"""


class Server:
    """A pretend server under ``root``: the deploy tool's settings, the app, and the fakes."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.root, self.fake, self.bin = tmp_path / "root", tmp_path / "fake", tmp_path / "bin"
        for folder in (
            self.fake,
            self.bin,
            self.root / "etc" / "systemd" / "system",
            self.root / "var" / "lib",
        ):
            folder.mkdir(parents=True)
        (self.root / "etc" / "sudarshan").mkdir()
        (self.root / "etc" / "sudarshan" / "deploy.env").write_text(
            f"REPO=https://github.com/owner/sudarshan\nAPP_DIR={APP_DIR}\nSVC_USER=sudarshan\n"
        )
        (self.root / "etc" / "resolv.conf").write_text("nameserver 127.0.0.53\noptions edns0\n")
        self.app = self.root / APP_DIR.lstrip("/")
        for name in ("db", "log"):
            (self.app / name).mkdir(parents=True)
            (self.app / name).chmod(0o750)
        (self.app / ".env").write_text("API_KEY_PEPPER=x\n")
        (self.app / ".env").chmod(0o600)
        (self.fake / "passwd").write_text("sudarshan 1001 sudarshan\n")
        for name, body in FAKES.items():
            text = body.replace("REAL_ID", shutil.which("id") or "id").replace(
                "REAL_STAT", shutil.which("stat") or "stat"
            )
            (self.bin / name).write_text("#!/usr/bin/env bash\n" + text)
            (self.bin / name).chmod(0o755)
        self.tool = tmp_path / "tool" / "sudarshan-deploy"
        self.tool.parent.mkdir()
        self.tool.write_text(TOOL)
        self.tool.chmod(0o755)

    @property
    def fam(self) -> Path:
        return self.root / "etc" / "sudarshan-family"

    @property
    def export(self) -> Path:
        return self.root / "var" / "lib" / "sudarshan-family-export"

    @property
    def nft_file(self) -> Path:
        return self.root / "etc" / "nftables-sudarshan-family.nft"

    @property
    def unit_file(self) -> Path:
        return self.root / "etc" / "systemd" / "system" / FW_UNIT

    def log(self, name: str) -> list[str]:
        path = self.fake / name
        return path.read_text().splitlines() if path.exists() else []

    def owner(self, path: Path) -> str:
        import hashlib

        rec = self.fake / f"owner-{hashlib.sha256(str(path).encode()).hexdigest()[:16]}"
        return rec.read_text().strip() if rec.exists() else ""

    def run(
        self, *args: str, tool: bool = True, path: str | None = None
    ) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH'] if path is None else path}",
            "FAKE": str(self.fake),
            "BOOTSTRAP_ROOT": str(self.root),
            "TMPDIR": str(self.tmp),
        }
        extra = ("--deploy-tool", str(self.tool)) if tool else ()
        return subprocess.run(
            ["bash", str(SCRIPT), *extra, *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )


@pytest.fixture
def server(tmp_path) -> Server:
    return Server(tmp_path)


def changes(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(("changed: ", "would change: "))]


# ---- a first run --------------------------------------------------------------------------------


def test_a_first_run_makes_the_account_its_folders_and_its_firewall_then_provisions(server):
    done = server.run()
    out = done.stdout
    assert done.returncode == 0, out + done.stderr
    assert server.log("useradd") == [
        "--system --user-group --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin "
        f"--comment SUDARSHAN family view {ACCOUNT}"
    ]
    assert (server.owner(server.fam), stat.S_IMODE(server.fam.stat().st_mode)) == (
        f"root:{ACCOUNT}",
        0o750,
    )
    assert (server.owner(server.export), stat.S_IMODE(server.export.stat().st_mode)) == (
        f"{ACCOUNT}:sudarshan",
        0o2750,
    )
    assert server.nft_file.read_text() == (server.fake / "checked.nft").read_text()  # checked first
    assert stat.S_IMODE(server.nft_file.stat().st_mode) == 0o644
    assert server.unit_file.exists()
    asked = server.log("systemctl")
    assert (
        "daemon-reload" in asked and f"enable {FW_UNIT}" in asked and f"restart {FW_UNIT}" in asked
    )
    changing = [
        a
        for a in asked
        if a.split()[0] in ("enable", "restart", "start", "stop", "disable", "reload")
    ]
    assert all(a.split()[-1] == FW_UNIT for a in changing), changing  # nothing else is touched
    assert server.log("tool") == ["--family-provision"]
    assert "family: PASS: the family account is set up" in out  # the step's own words, shown
    assert (
        out.splitlines()[-2]
        == "PASS. The family account is set up and closed off from the owner's secrets."
    )
    assert out.splitlines()[-1].startswith(
        "The family view itself comes in a later release, switched off"
    )
    said = changes(out)
    assert (
        f"changed: new account {ACCOUNT} (no home, no login shell, not in the group sudarshan)"
        in said
    )
    assert f"changed: new folder {server.fam} (root:{ACCOUNT}, mode 750)" in said
    assert f"changed: new folder {server.export} ({ACCOUNT}:sudarshan, mode 2750)" in said
    assert "changed: the allow-list loaded (table inet sudarshan_family)" in said


def test_a_second_run_changes_nothing_and_says_so(server):
    assert server.run().returncode == 0
    (server.fake / "tool_mode").write_text("same")
    files = {
        p: (p.stat().st_ino, p.stat().st_mtime_ns)
        for p in (server.nft_file, server.unit_file, server.fam, server.export)
    }
    asked = len(server.log("systemctl"))
    again = server.run()
    assert again.returncode == 0, again.stdout + again.stderr
    assert changes(again.stdout) == []
    assert (
        again.stdout.splitlines()[-2]
        == "PASS. Nothing was changed: everything was already in place, and the check passes."
    )
    assert len(server.log("useradd")) == 1
    later = server.log("systemctl")[asked:]
    assert not [a for a in later if a.split()[0] in ("daemon-reload", "enable", "restart")], later
    assert {p: (p.stat().st_ino, p.stat().st_mtime_ns) for p in files} == files
    assert "in place: the account sudarshan-family" in again.stdout


def test_a_dry_run_changes_nothing_and_runs_no_step(server):
    done = server.run("--dry-run")
    assert done.returncode == 0, done.stdout + done.stderr
    assert not server.fam.exists() and not server.export.exists()
    assert not server.nft_file.exists() and not server.unit_file.exists()
    assert server.log("useradd") == [] and server.log("tool") == [] and server.log("chown") == []
    assert not [
        a for a in server.log("systemctl") if a.split()[0] in ("daemon-reload", "enable", "restart")
    ]
    said = changes(done.stdout)
    assert said and all(line.startswith("would change: ") for line in said)
    assert done.stdout.splitlines()[-1] == "Dry run: nothing was changed."


def test_a_run_puts_wrong_owners_and_modes_right_and_says_what_they_were(server):
    assert server.run().returncode == 0
    server.fam.chmod(0o755)
    (server.fam / "seal-admin.key").write_text("k\n")
    (server.fam / "seal-admin.key").chmod(0o644)
    (server.fake / "tool_mode").write_text("same")
    done = server.run()
    said = changes(done.stdout)
    assert (
        f"changed: {server.fam} set to root:{ACCOUNT}, mode 750 (it was root:{ACCOUNT} 755)" in said
    )
    assert f"changed: {server.fam / 'seal-admin.key'} set to root:{ACCOUNT}, mode 640" in said
    assert stat.S_IMODE(server.fam.stat().st_mode) == 0o750
    assert stat.S_IMODE((server.fam / "seal-admin.key").stat().st_mode) == 0o640


# ---- what it needs, checked before anything changes ---------------------------------------------


def nothing_changed(server: Server, done: subprocess.CompletedProcess) -> None:
    assert done.returncode == 2, done.stdout + done.stderr
    assert done.stdout.splitlines()[-1] == "Nothing was changed."
    assert not server.fam.exists() and not server.nft_file.exists()
    assert server.log("useradd") == [] and server.log("tool") == []


def test_an_account_in_the_rings_group_is_refused(server):
    with (server.fake / "passwd").open("a") as handle:
        handle.write(f"{ACCOUNT} 999 {ACCOUNT} sudarshan\n")
    done = server.run()
    nothing_changed(server, done)
    assert f"gpasswd -d {ACCOUNT} sudarshan" in done.stdout


def test_a_deploy_tool_older_than_this_release_is_waited_for(server):
    server.tool.write_text("#!/usr/bin/env bash\ncase $1 in --check) ;; esac\n")
    done = server.run()
    nothing_changed(server, done)
    assert "older than the release that brings the family set-up" in done.stdout


def test_a_deploy_tool_others_can_change_is_not_run_as_root(server):
    server.tool.chmod(0o777)
    done = server.run()
    nothing_changed(server, done)
    assert "is not root's alone" in done.stdout


def test_a_deploy_tool_that_cannot_be_found_is_asked_for(server):
    done = server.run(tool=False)  # the fake unit names none, and none is on PATH
    nothing_changed(server, done)
    assert "Give its path with --deploy-tool" in done.stdout


def test_no_deploy_settings_no_set_up(server):
    (server.root / "etc" / "sudarshan" / "deploy.env").unlink()
    nothing_changed(server, server.run())


def test_a_server_without_nft_is_told_how_to_install_it(server):
    """L8: the owner is given the action, not only the missing command."""
    (server.bin / "nft").unlink()
    dirs = [d for d in os.environ["PATH"].split(os.pathsep) if d and not (Path(d) / "nft").exists()]
    if shutil.which("bash", path=os.pathsep.join(dirs)) is None:
        pytest.skip("nft sits beside bash on this machine")
    done = server.run(path=os.pathsep.join(dirs))
    nothing_changed(server, done)
    assert done.stdout.splitlines() == [
        "This server has no nft command, which the set-up needs. Install it with: apt install "
        "nftables, then run this again.",
        "Nothing was changed.",
    ]


def test_it_is_for_root(tmp_path):
    """Without the test folder it is the server: anyone but root is stopped before anything."""
    command = ["bash", str(SCRIPT), "--dry-run"]
    if os.geteuid() == 0:
        if shutil.which("setpriv") is None:
            pytest.skip("needs setpriv to run as another account")
        command = ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", *command]
    env = {k: v for k, v in os.environ.items() if k != "BOOTSTRAP_ROOT"}
    done = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
    assert done.returncode == 2 and done.stdout.splitlines() == [
        "Run this as root (sudo).",
        "Nothing was changed.",
    ]


def test_a_wrong_option_is_refused():
    done = subprocess.run(
        ["bash", str(SCRIPT), "--yes"], capture_output=True, text=True, timeout=30
    )
    assert done.returncode == 2 and "usage: bootstrap.sh" in done.stderr


def test_help_prints_the_header():
    done = subprocess.run(
        ["bash", str(SCRIPT), "--help"], capture_output=True, text=True, timeout=30
    )
    assert done.returncode == 0 and "Usage: bootstrap.sh [--dry-run]" in done.stdout


# ---- OpenAlgo's open folders are named, never changed ------------------------------------------


def test_folders_others_can_read_are_named_with_the_chmod_to_run_and_left_as_they_are(server):
    (server.app / "db").chmod(0o755)
    (server.app / "log").chmod(0o751)
    (server.app / ".env").chmod(0o644)
    done = server.run()
    for path in ("db", "log", ".env"):
        assert f"    chmod o-rwx '{APP_DIR}/{path}'" in done.stdout
    assert stat.S_IMODE((server.app / "db").stat().st_mode) == 0o755
    assert stat.S_IMODE((server.app / "log").stat().st_mode) == 0o751
    assert stat.S_IMODE((server.app / ".env").stat().st_mode) == 0o644


def test_closed_folders_need_no_line(server):
    done = server.run()
    assert "chmod o-rwx" not in done.stdout


def test_a_resolver_other_than_the_local_one_is_noted(server):
    (server.root / "etc" / "resolv.conf").write_text("nameserver 169.254.169.254\n")
    done = server.run()
    assert "NOTE: " in done.stdout and "127.0.0.53" in done.stdout


# ---- the provision step's result ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "says"),
    [
        ("fail", "FAIL: the provision step did not pass."),
        (
            "busy",
            "FAIL: an install was running, so the provision step did not run. Run this again in a few minutes.",
        ),
    ],
)
def test_a_provision_step_that_does_not_pass_is_a_fail(server, mode, says):
    (server.fake / "tool_mode").write_text(mode)
    done = server.run()
    assert done.returncode == 1
    assert done.stdout.splitlines()[-1].startswith(says)


# ---- the allow-list and its unit ----------------------------------------------------------------


def test_the_allow_list_says_exactly_what_the_account_may_reach(server):
    assert server.run().returncode == 0
    rules = server.nft_file.read_text()
    body = [
        line.strip()
        for line in rules.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert body == [
        "table inet sudarshan_family",
        "delete table inet sudarshan_family",
        "table inet sudarshan_family {",
        "chain output {",
        "type filter hook output priority 0; policy accept;",
        f'meta skuid "{ACCOUNT}" jump family',
        "}",
        "chain family {",
        "ct state established,related accept",
        "ip daddr 127.0.0.1 tcp dport { 5000, 5580, 5581, 5583, 5586 } accept",
        "ip daddr 127.0.0.53 udp dport 53 accept",
        "ip daddr 127.0.0.53 tcp dport 53 accept",
        'oifname "lo" reject with icmpx type admin-prohibited',
        "tcp dport 443 accept",
        "reject with icmpx type admin-prohibited",
        "}",
        "}",
    ]
    assert "flush" not in rules


def test_the_unit_loads_it_at_boot_and_has_no_stop(server):
    assert server.run().returncode == 0
    unit = server.unit_file.read_text()
    assert not FW_UNIT.startswith("sudarshan-")  # the deploy tool never manages it
    assert re.search(r"^ExecStart=\S*nft -f /etc/nftables-sudarshan-family\.nft$", unit, re.M)
    assert "ExecStop" not in unit and "Type=oneshot" in unit and "RemainAfterExit=yes" in unit
    assert "Before=sudarshan-family.service" in unit and "WantedBy=multi-user.target" in unit


def test_the_script_touches_no_other_service_or_table():
    code = [line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")]
    text = "\n".join(code)
    assert "nginx" not in text and "flush" not in text and "trinetra" not in text.lower()
    restarts = re.findall(r"systemctl (?:restart|start|stop|reload|enable|disable) (\S+)", text)
    assert restarts and set(restarts) == {'"${FW_UNIT}"'}
    assert "python" not in text  # root runs none of the app's code


def test_the_script_parses_and_is_shellcheck_clean():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck is not installed")
    done = subprocess.run(["shellcheck", "-x", str(SCRIPT)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout


# ---- the real allow-list, in a network namespace of its own -----------------------------------

PROBE = textwrap.dedent(
    """
    import errno, fcntl, json, os, socket, struct, subprocess, sys
    from sudarshan.family import check

    def lo_up():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        flags = struct.unpack("16sH14s", fcntl.ioctl(s.fileno(), 0x8913, struct.pack("16sH14s", b"lo", 0, b"")))[1]
        fcntl.ioctl(s.fileno(), 0x8914, struct.pack("16sH14s", b"lo", flags | 1, b""))
        s.close()

    def as_nobody():
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(r)
            os.setgroups([]); os.setresgid(65534, 65534, 65534); os.setresuid(65534, 65534, 65534)
            lines = [check.blocked_line(5555, "bus", check.tcp_connect),
                     check.blocked_line(8765, "proxy", check.tcp_connect),
                     check.open_line(5583, "portfolio", check.tcp_connect)]
            os.write(w, json.dumps(lines).encode()); os._exit(0)
        os.close(w)
        data = b""
        while chunk := os.read(r, 65536):
            data += chunk
        os.waitpid(pid, 0)
        return json.loads(data)

    lo_up()
    found = {"before": as_nobody()}
    subprocess.run(["nft", "-f", sys.argv[1]], check=True)
    found["after"] = as_nobody()
    # a listener of the account's own: the replies it sends to a connection made to it go through
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.setgroups([]); os.setresgid(65534, 65534, 65534); os.setresuid(65534, 65534, 65534)
        ls = socket.socket(); ls.bind(("127.0.0.1", 5585)); ls.listen(1); os.write(w, b"x")
        c, _ = ls.accept(); c.sendall(b"pong:" + c.recv(16)); c.close(); os._exit(0)
    os.read(r, 1)
    c = socket.create_connection(("127.0.0.1", 5585), timeout=3); c.sendall(b"ping")
    found["reply"] = c.recv(16).decode(); c.close(); os.waitpid(pid, 0)
    found["root"] = check.tcp_connect("127.0.0.1", 5555, 3.0)
    print(json.dumps(found))
    """
)


def namespaces_work() -> bool:
    if os.geteuid() != 0 or not (shutil.which("nft") and shutil.which("unshare")):
        return False
    done = subprocess.run(
        ["unshare", "-n", "nft", "list", "tables"], capture_output=True, timeout=30
    )
    return done.returncode == 0


@pytest.mark.skipif(
    not namespaces_work(), reason="needs root, nft and unshare (a network namespace)"
)
def test_the_allow_list_refuses_the_closed_ports_at_once_and_lets_the_portfolio_port_through(
    server,
):
    """The real kernel: a refusal the check takes as the firewall's on 5555 and 8765 (and none before
    the rules were loaded), the portfolio's port not blocked, the account's own listener answering,
    and root, another account, untouched. Only the account name is changed, to one that exists here."""
    assert server.run().returncode == 0
    rules = server.tmp / "rules.nft"
    rules.write_text(server.nft_file.read_text().replace(f'"{ACCOUNT}"', '"nobody"'))
    done = subprocess.run(
        ["unshare", "-n", sys.executable, "-c", PROBE, str(rules)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    import json

    found = json.loads(done.stdout)
    assert [passed for passed, _line in found["before"]] == [
        False,
        False,
        True,
    ]  # no rule: refused by the port
    assert "the port itself refused it" in found["before"][0][1]
    assert found["after"] == [
        [True, "PASS bus (127.0.0.1:5555): refused by the firewall"],
        [True, "PASS proxy (127.0.0.1:8765): refused by the firewall"],
        [True, "PASS portfolio (127.0.0.1:5583): not blocked (nothing is listening there now)"],
    ]
    assert found["reply"] == "pong:ping"
    import errno

    assert found["root"] == errno.ECONNREFUSED  # another account is not filtered
