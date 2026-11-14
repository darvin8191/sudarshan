"""O2' (cross-design Y2.3, Y3.2, Y25): ``python -m sudarshan.family.check`` as the family account.

The file checks run against a folder of the test's own, as an account that owns nothing in it: as
root, in a child process that drops to ``nobody`` (65534); as any other user, as that user. The modes
are all-or-nothing (000, 111, 444, 555), so the owner is held exactly as anybody else is and the
outcome is the same either way. The connections go through a fake (the real firewall is proven in
``sudarshan/ops/tests/test_family_bootstrap.py``, in a network namespace of its own).
"""

from __future__ import annotations

import errno
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from sudarshan.family import check
from sudarshan.tests.fdwatch import FdWatch

REPO = Path(__file__).resolve().parents[3]

NOBODY = 65534
SECRET = "API_KEY_PEPPER=not-a-real-pepper-but-it-must-never-be-shown"


def as_another_account(func: Callable[[], object]) -> object:
    """``func()``, run as an account that owns nothing here; its result must be JSON."""
    if os.geteuid() != 0:
        return func()
    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:  # the child: never back into pytest, whatever happens
        code = 1
        try:
            os.close(read_end)
            os.setgroups([])
            os.setresgid(NOBODY, NOBODY, NOBODY)
            os.setresuid(NOBODY, NOBODY, NOBODY)
            os.write(write_end, json.dumps(func()).encode())
            code = 0
        finally:
            os._exit(code)
    os.close(write_end)
    data = b""
    while chunk := os.read(read_end, 65536):
        data += chunk
    os.close(read_end)
    _pid, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0, "the check failed in the other account"
    return json.loads(data)


@pytest.fixture
def base():
    """A folder any account can walk into (pytest's own temporary folders are root's alone)."""
    folder = Path(tempfile.mkdtemp(prefix="family-check-", dir="/tmp"))
    folder.chmod(0o755)
    yield folder
    for root, dirs, files in os.walk(folder):  # open every mode up again, then remove
        for name in dirs + files:
            path = os.path.join(root, name)
            if not os.path.islink(path):
                os.chmod(path, 0o700)
    shutil.rmtree(folder)


def put(path: Path, text: str = SECRET, mode: int = 0o000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")
    path.chmod(mode)
    return path


def make(base: Path, **modes: int) -> check.Places:
    """A server laid out as the set-up leaves it, with ``modes`` changed (by the keys below)."""
    app, etc, fam, var = base / "app", base / "etc-sudarshan", base / "etc-family", base / "var-lib"
    put(app / ".env", mode=modes.get("env", 0o000))
    put(app / "db" / "openalgo.db", mode=modes.get("db_file", 0o000))
    put(app / "db" / "sudarshan-vault" / "sudarshan_vault.db", mode=modes.get("vault_file", 0o000))
    put(app / "log" / "errors.jsonl", mode=modes.get("log_file", 0o000))
    put(etc / "alerts.env", mode=modes.get("etc_file", 0o000))
    put(var / "sudarshan-alerts" / "alerts.db", mode=modes.get("alerts_file", 0o000))
    put(var / "sudarshan-portfolio" / "portfolio.db", mode=0o000)
    put(var / "sudarshan-feed" / "subscriptions.json", mode=modes.get("claims", 0o000))
    for name in ("seal-admin.key", "store.key", "feed-token"):
        put(
            fam / name,
            "a-family-key-file",
            mode=0o000 if name == modes.get("family_closed") else 0o444,
        )
    (app / "db" / "sudarshan-vault").chmod(modes.get("vault", 0o000))
    (app / "db").chmod(modes.get("db", 0o000))
    (app / "log").chmod(modes.get("log", 0o000))
    app.chmod(0o555)
    etc.chmod(modes.get("etc", 0o000))
    (var / "sudarshan-alerts").chmod(modes.get("alerts", 0o000))
    (var / "sudarshan-portfolio").chmod(0o000)
    (var / "sudarshan-feed").chmod(0o555)
    var.chmod(0o555)
    fam.chmod(modes.get("family", 0o555))
    return check.Places(app, etc, fam, var)


#: The loopback ports the set-up's allow-list lets the family account reach.
ALLOWED = frozenset({5000, 5580, 5581, 5583, 5586})


def firewall(host: str, port: int, timeout: float) -> int:
    """The set-up's firewall, as the family account meets it: a port on its list is not refused
    (nothing listens in a test), every other loopback port is."""
    return errno.ECONNREFUSED if port in ALLOWED else errno.EHOSTUNREACH


def results(places: check.Places, connect=firewall) -> list[list]:
    return as_another_account(lambda: [list(r) for r in check.run(places, connect)])


def failed(found: list[list]) -> list[str]:
    return [line for passed, line in found if not passed]


# ---- the files ---------------------------------------------------------------------------------


def test_a_server_laid_out_right_passes_every_check(base):
    found = results(make(base))
    assert failed(found) == [], found
    assert len(found) == 15
    assert all(line.startswith("PASS ") for _p, line in found)
    assert found[8][1].startswith("PASS the family folder: this account opens all 3 files in ")


@pytest.mark.parametrize(
    ("modes", "says"),
    [
        ({"env": 0o444}, "FAIL OpenAlgo's .env: this account can open {app}/.env"),
        ({"db": 0o555, "db_file": 0o444}, "FAIL OpenAlgo's db folder: this account can open {app}/db/openalgo.db"),
        ({"log": 0o555, "log_file": 0o444}, "FAIL OpenAlgo's log folder: this account can open {app}/log/errors.jsonl"),
        ({"db": 0o555, "vault": 0o555, "vault_file": 0o444},
         "FAIL the owner vault: this account can open {app}/db/sudarshan-vault/sudarshan_vault.db"),
        ({"etc": 0o555, "etc_file": 0o444}, "FAIL the ring's settings and keys: this account can open {base}/etc-sudarshan/alerts.env"),
        ({"alerts": 0o111},
         "FAIL the alerts' state: this account can enter {base}/var-lib/sudarshan-alerts without listing it, so the files in it could be opened by name"),
        ({"claims": 0o444}, "FAIL the feed's claims: this account can open {base}/var-lib/sudarshan-feed/subscriptions.json"),
        ({"family": 0o000}, "FAIL the family folder: this account cannot list {base}/etc-family"),
        ({"family_closed": "store.key"}, "FAIL the family folder: this account cannot open {base}/etc-family/store.key"),
    ],
)  # fmt: skip
def test_each_place_the_account_reaches_or_misses_fails_with_its_path(base, modes, says):
    found = results(make(base, **modes))
    assert says.format(app=base / "app", base=base) in failed(found), found


def test_a_folder_it_can_enter_but_not_list_fails_even_when_nothing_in_it_opens(base):
    """Its files could be opened by a name the account guesses; the check cannot prove they cannot."""
    found = results(make(base, db=0o111))
    assert any(
        line.startswith("FAIL OpenAlgo's db folder: this account can enter ")
        for line in failed(found)
    )


def test_the_family_folder_missing_fails_and_says_what_to_run(base):
    places = make(base)
    places.family_etc.chmod(
        0o755
    )  # its owner may remove what is in it (as root this needs nothing)
    shutil.rmtree(places.family_etc)
    found = results(places)
    assert (
        f"FAIL the family folder: {places.family_etc} is not there; run the family set-up"
        in failed(found)
    )


def test_places_that_are_not_on_this_server_cannot_be_read(base):
    found = results(check.Places(base / "no-app", base / "no-etc", base / "fam", base / "no-var"))
    assert [line for line in failed(found) if "family folder" not in line] == []


def test_nothing_secret_is_printed(base):
    places = make(base, env=0o444, db=0o555, db_file=0o444)

    def go():
        out = io.StringIO()
        code = check.main(
            ["--app-dir", str(places.app_dir), "--ring-etc", str(places.ring_etc),
             "--family-etc", str(places.family_etc), "--ring-var", str(places.ring_var)],
            env={}, connect=firewall, out=out,
        )  # fmt: skip
        return [code, out.getvalue()]

    code, out = as_another_account(go)
    assert code == 1 and "FAIL OpenAlgo's .env" in out
    assert "PEPPER" not in out and "not-a-real" not in out and "a-family-key-file" not in out
    assert all(line.startswith(("PASS ", "FAIL ")) for line in out.splitlines())


def test_the_paths_can_come_from_the_environment(base):
    places = make(base)

    def go():
        out = io.StringIO()
        env = {
            "SUDARSHAN_OPENALGO_DIR": str(places.app_dir),
            "SUDARSHAN_ETC_DIR": str(places.ring_etc),
            "SUDARSHAN_FAMILY_ETC_DIR": str(places.family_etc),
            "SUDARSHAN_RING_VAR": str(places.ring_var),
        }
        return [check.main([], env=env, connect=firewall, out=out), out.getvalue()]

    code, out = as_another_account(go)
    assert code == 0, out


def test_the_defaults_are_the_servers_paths(tmp_path):
    places = check.places_from([], {})
    assert places.ring_etc == Path("/etc/sudarshan")
    assert places.family_etc == Path("/etc/sudarshan-family")
    assert places.ring_var == Path("/var/lib")
    assert places.app_dir == Path(check.__file__).resolve().parents[2]  # the app it runs from


def test_a_walk_stops_at_its_budget(tmp_path):
    for n in range(30):
        (tmp_path / f"f{n}").write_text("x")
    found = check.Walk([], [], budget=10)
    check.walk(str(tmp_path), found)
    assert found.exhausted


def test_a_loop_of_links_is_walked_once(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "back").symlink_to(tmp_path)
    (tmp_path / "a" / "file").write_text("x")
    found = check.Walk([], [])
    check.walk(str(tmp_path), found)
    assert not found.exhausted


# ---- the connections ---------------------------------------------------------------------------


def connections(answers: dict[int, int]) -> list[tuple[bool, str]]:
    def connect(host: str, port: int, timeout: float) -> int:
        assert host == "127.0.0.1" and timeout == check.CONNECT_TIMEOUT_S
        return answers[port]

    return [check.blocked_line(5555, "bus", connect), check.blocked_line(8765, "proxy", connect),
            check.open_line(5583, "portfolio", connect)]  # fmt: skip


@pytest.mark.parametrize("refusal", [errno.EHOSTUNREACH, errno.EACCES, errno.EPERM])
def test_the_firewalls_refusal_passes_on_the_closed_ports(refusal):
    found = connections({5555: refusal, 8765: refusal, 5583: errno.ECONNREFUSED})
    assert [p for p, _line in found] == [True, True, True]
    assert found[0][1] == "PASS bus (127.0.0.1:5555): refused by the firewall"
    assert (
        found[2][1]
        == "PASS portfolio (127.0.0.1:5583): not blocked (nothing is listening there now)"
    )


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        (errno.ECONNREFUSED, "the port itself refused it, so no firewall rule stops it"),
        (0, "this account connected to it; no firewall rule stops it"),
        (errno.ETIMEDOUT, "no answer in 3 s, so it is not proven blocked"),
        (errno.ENETUNREACH, "the connection failed with ENETUNREACH, not the firewall's refusal"),
    ],
)
def test_anything_but_the_firewalls_refusal_fails_on_the_closed_ports(answer, says):
    found = connections({5555: answer, 8765: errno.EHOSTUNREACH, 5583: 0})
    assert found[0] == (False, f"FAIL bus (127.0.0.1:5555): {says}")
    assert found[1][0] and found[2] == (
        True,
        "PASS portfolio (127.0.0.1:5583): not blocked (it answered)",
    )


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        (errno.EHOSTUNREACH, "refused by the firewall, and the family view reads through it"),
        (errno.ETIMEDOUT, "no answer in 3 s"),
    ],
)
def test_the_portfolio_port_must_not_be_blocked(answer, says):
    found = connections({5555: errno.EHOSTUNREACH, 8765: errno.EHOSTUNREACH, 5583: answer})
    assert found[2] == (False, f"FAIL portfolio (127.0.0.1:5583): {says}")


def test_the_real_connection_says_what_happened():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    open_port = listener.getsockname()[1]
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    closed_port = closed.getsockname()[1]
    closed.close()  # nothing listens there now
    try:
        assert check.tcp_connect("127.0.0.1", open_port, 2.0) == 0
        assert check.tcp_connect("127.0.0.1", closed_port, 2.0) == errno.ECONNREFUSED
    finally:
        listener.close()


# ---- the run as a whole ------------------------------------------------------------------------


def test_the_exit_status_is_0_only_when_every_line_passes(base):
    places = make(base)
    argv = ["--app-dir", str(places.app_dir), "--ring-etc", str(places.ring_etc),
            "--family-etc", str(places.family_etc), "--ring-var", str(places.ring_var)]  # fmt: skip

    def go(connect):
        def inner():
            out = io.StringIO()
            return [check.main(argv, env={}, connect=connect, out=out), out.getvalue()]

        return inner

    assert as_another_account(go(firewall))[0] == 0
    code, out = as_another_account(go(lambda h, p, t: errno.ECONNREFUSED))
    assert code == 1 and out.count("FAIL ") == 4


def test_rules_that_stop_only_the_two_named_ports_fail(base):
    """L10: the check proves the firewall's default refusal too, on the alerts' port and on a port
    nothing uses: rules for 5555 and 8765 alone would have passed."""

    def two_ports(host: str, port: int, timeout: float) -> int:
        return {5555: errno.EHOSTUNREACH, 8765: errno.EHOSTUNREACH}.get(port, errno.ECONNREFUSED)

    places = make(base)
    refused = "the port itself refused it, so no firewall rule stops it"
    assert failed(results(places, two_ports)) == [
        f"FAIL the alerts service (127.0.0.1:5582): {refused}",
        f"FAIL a port nothing uses (127.0.0.1:1): {refused}",
    ]
    passed = [line for _p, line in results(places)]
    assert "PASS a port nothing uses (127.0.0.1:1): refused by the firewall" in passed


def test_the_check_leaves_no_file_or_socket_open(base):
    places = make(base, db=0o555, db_file=0o444, family=0o555)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    port = listener.getsockname()[1]
    try:
        watch = FdWatch()
        for _ in range(25):
            check.run(places, firewall)
            check.tcp_connect("127.0.0.1", port, 1.0)
        watch.assert_clean()
    finally:
        listener.close()


def test_the_python_line_names_the_account_it_ran_as():
    passed, line = check.python_line()
    assert passed and line.startswith("PASS the app's Python: it runs as ")
    assert line.endswith(" and loads the family service")


# ---- L6 (review, feat/family-t2): the check loads the service's code, not only the package ----------
def test_the_python_line_fails_when_the_service_module_cannot_be_loaded():
    def missing(name):
        assert name == "sudarshan.family.service"
        raise ModuleNotFoundError("No module named 'argon2'", name="argon2")

    def closed(name):
        raise PermissionError(13, "Permission denied", "/opt/sudarshan/apps/openalgo/.env")

    def broken(name):
        raise ValueError("a fault in the release")

    passed, line = check.python_line(missing)
    assert not passed and line.startswith("FAIL the app's Python: as ")
    assert "argon2 is missing" in line and "run the deploy again" in line
    passed, line = check.python_line(closed)
    assert not passed and "/opt/sudarshan/apps/openalgo/.env cannot be opened" in line
    passed, line = check.python_line(broken)
    assert not passed and "install the previous release" in line and "ValueError" not in line


def test_the_check_fails_in_a_python_where_a_package_of_the_service_is_missing(tmp_path):
    """In a fresh interpreter where argon2 (which the accounts import) cannot be imported, the
    check's Python line fails: it truly loads the service and what the service imports."""
    script = (
        "import sys; sys.modules['argon2'] = None\n"
        "from sudarshan.family import check\n"
        "passed, line = check.python_line()\n"
        "print(passed); print(line)\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          timeout=120, check=False, cwd=REPO)  # fmt: skip
    assert done.returncode == 0, done.stderr
    passed, line = done.stdout.splitlines()[:2]
    assert passed == "False" and "argon2 is missing" in line, done.stdout


def test_the_modes_this_file_relies_on_hold_for_the_account_it_runs_as(base):
    """The premise of every test above: as the account the checks run as, 000 cannot be opened and
    444 can, 111 can be entered and not listed."""
    put(base / "closed", mode=0o000)
    put(base / "open", mode=0o444)
    (base / "enter").mkdir()
    (base / "enter").chmod(0o111)

    def go():
        try:
            os.listdir(base / "enter")
            listed = True
        except PermissionError:
            listed = False
        return [check.can_open(str(base / "closed")), check.can_open(str(base / "open")), listed,
                os.access(base / "enter", os.X_OK), stat.S_IMODE(os.stat(base / "open").st_mode)]  # fmt: skip

    assert as_another_account(go) == [False, True, False, True, 0o444]
