"""O3 (P6; M2.11, M2.16; cross-design Y7, Y33): the deploy tool's check of the family view's nginx lines.

``sudarshan-deploy --nginx-check`` says, reading only, whether the three files the owner installs from
``sudarshan/ops/nginx`` are in place, this release's and loaded by nginx, whether the guard sits in the
server block for port 443, whether that server's location / goes where the invite page goes, whether the
guard knows OpenAlgo's cookie as OpenAlgo's .env makes it, and whether the running nginx was reloaded
after the files changed. A timer run logs the first problem once while it holds and says nothing while
none of the files is there; the family set-up adds one line before its PASS.

The pieces of the tool run on their own (``_function``); the whole tool runs on the fake server of
``test_deploy_calendar.py``, with ``NGX_DIR`` pointed into the box (``monkeypatch`` on its
``DEPLOY_TOOL``, as ``test_deploy_family.py`` does), a fake ``nginx`` that prints what ``nginx -T``
would (written by the test, in nginx's own format) and a fake ``ps`` for its workers.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import test_deploy_calendar as calendar_harness
from test_deploy_calendar import DEPLOY_TOOL, Box, _function, _shim, _snapshot, whole_tool
from test_deploy_family import bootstrapped, quiet_box

REPO = Path(__file__).resolve().parents[3]
NGINX_SRC = REPO / "sudarshan" / "ops" / "nginx"
FILES = (
    "conf.d/sudarshan-family-zones.conf",
    "snippets/sudarshan-family.conf",
    "snippets/sudarshan-family-proxy.conf",
)
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def tool_text() -> str:
    return DEPLOY_TOOL.read_text()


def bash(script: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script, "bash", *args], input=stdin, capture_output=True,
                          text=True, timeout=60)  # fmt: skip


def ngx_text(path: Path) -> str:
    done = bash(_function("ngx_text") + 'ngx_text "$1"', str(path))
    assert done.returncode == 0, done.stderr
    return done.stdout


def sums_in_tool() -> list[str]:
    block = re.search(r"\nNGX_SUMS=\(\n(.*?)\n\)\n", tool_text(), re.S)
    assert block, "NGX_SUMS is not in the tool"
    return block.group(1).split()


# ---- the tool knows this release's files ---------------------------------------------------------
def test_the_tool_carries_the_sums_of_this_releases_files():
    """A change to a file in sudarshan/ops/nginx is a change of NGX_SUMS: until both move together the
    owner would be told that the release's own copy is not the release's."""
    files = re.search(r"\nNGX_FILES=\((.*?)\)\n", tool_text())
    assert files and tuple(files.group(1).split()) == FILES
    got = [hashlib.sha256(ngx_text(NGINX_SRC / Path(f).name).encode()).hexdigest() for f in FILES]
    assert sums_in_tool() == got, f"put these in NGX_SUMS of sudarshan-deploy: {got}"


def test_the_owners_steps_install_what_the_tool_checks():
    """The family README's install line puts each file where NGX_FILES looks for it, and its steps
    name the include line, the reload and the check the tool's FAIL lines send the owner to."""
    readme = (REPO / "sudarshan" / "family" / "README.md").read_text(encoding="utf-8")
    assert re.search(
        r'\nNGX_HOWTO=\'sudarshan/family/README\.md, "Installing the nginx lines"\'\n', tool_text()
    )
    section = readme.split("\n## Installing the nginx lines (O3)\n", 1)[1]
    (line,) = [x for x in section.splitlines() if x.lstrip().startswith("sudo bash -c 'set -e;")]
    placed = {}
    for sources, folder in re.findall(
        r'install -m 644 -o root -g root ((?:"\$d/[^"]+" )+)/etc/nginx/([a-z.]+)/;', line
    ):
        for name in re.findall(r'"\$d/([^"]+)"', sources):
            placed[name] = folder
    assert placed == {Path(f).name: f.split("/")[0] for f in FILES}
    assert "for f in " + " ".join(Path(f).name for f in FILES) + "; do" in line
    assert "/sudarshan/ops/nginx/$f" in line and "$(cat /var/lib/sudarshan/deployed_tag)" in line
    for step in ("    include snippets/sudarshan-family.conf;", "sudo nginx -t && sudo systemctl reload nginx",
                 "sudo sudarshan-deploy --nginx-check", "map $http_cookie $sud_owner"):  # fmt: skip
        assert step in section, step


def test_the_invite_pages_address_is_the_one_the_tool_compares():
    server = (NGINX_SRC / "sudarshan-family.conf").read_text(encoding="utf-8")
    want = re.search(r'\nNGX_OPENALGO="([^"]+)"', tool_text())
    assert want and f"proxy_pass {want.group(1)}/family/join/;" in server


def test_the_owners_cookie_name_is_set_aside_and_read_back(tmp_path):
    zones = NGINX_SRC / "sudarshan-family-zones.conf"
    text = zones.read_text(encoding="utf-8")
    own = tmp_path / "zones.conf"
    own.write_text(text.replace("__Secure-session=", "__Secure-app_session="), encoding="utf-8")
    assert ngx_text(own) == ngx_text(zones)  # the one line the owner may edit
    done = bash(_function("ngx_cookie") + 'ngx_cookie "$1"', str(own))
    assert done.stdout == "__Secure-app_session\n"
    assert (
        bash(_function("ngx_cookie") + 'ngx_cookie "$1"', str(zones)).stdout == "__Secure-session\n"
    )
    for change in (
        (
            "__Host-sudarshan_family=",
            "__Host-other=",
        ),  # the family cookie is not the owner's to edit
        ('"~^1:/assets/" 0;', '"~^1:/assets/" 0;\n    "~^1:/api/" 0;'),
        ("rate=5r/m", "rate=50r/m"),
    ):
        changed = tmp_path / "changed.conf"
        changed.write_text(text.replace(*change), encoding="utf-8")
        assert ngx_text(changed) != ngx_text(zones), change


# ---- reading nginx -T: which server block takes the include ------------------------------------------
def dump(files: dict[str, str]) -> str:
    """What nginx -T prints: each file as "# configuration file <path>:", its text, then a newline."""
    return "".join(f"# configuration file {path}:\n{text}\n" for path, text in files.items())


SITE = """\
server {
    listen 80;
    listen [::]:80;
    location / { return 301 https://$host$request_uri; }
}
server {
    listen 443 ssl;          # the owner's site
    listen [::]:443 ssl;
    # include snippets/sudarshan-family.conf;   (a comment, not the line)
    add_header X-Note "a { brace ; in quotes";
    set $openalgo_loggable 1;
    include snippets/sudarshan-family.conf;
    location /socket.io/ { proxy_pass http://127.0.0.1:5000; }
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
    }
}
"""


def servers(text: str) -> list[str]:
    done = bash(_function("ngx_servers") + "ngx_servers", stdin=text)
    assert done.returncode == 0, done.stderr
    return done.stdout.splitlines()


def test_the_include_is_found_in_the_server_block_for_443():
    text = dump({
        "/etc/nginx/nginx.conf": "http {\n    include conf.d/*.conf;\n    include sites-enabled/*;\n}\n",
        "/etc/nginx/sites-enabled/site": SITE,
        "/etc/nginx/snippets/sudarshan-family.conf": "location @x { return 200; }\n"
                                                     "include snippets/sudarshan-family.conf;\n",
    })  # fmt: skip
    assert servers(text) == [
        "server 0 0 - /etc/nginx/sites-enabled/site",
        "server 1 1 http://127.0.0.1:5000 /etc/nginx/sites-enabled/site",
    ]


@pytest.mark.parametrize(
    ("site", "records"),
    [
        (SITE.replace("    include snippets/sudarshan-family.conf;\n    location /socket.io/",
                      "    location /socket.io/").replace(
                          "listen [::]:80;\n", "listen [::]:80;\n    include snippets/sudarshan-family.conf;\n"),
         ["server 0 1 - S", "server 1 0 http://127.0.0.1:5000 S"]),
        ("include /etc/nginx/snippets/sudarshan-family.conf;\n" + SITE,
         ["stray S", "server 0 0 - S", "server 1 1 http://127.0.0.1:5000 S"]),
        (SITE.replace("listen 443 ssl;", "listen 8443 ssl;").replace("listen [::]:443 ssl;", ""),
         ["server 0 0 - S", "server 0 1 http://127.0.0.1:5000 S"]),
        (SITE.replace("listen 443 ssl;", "listen 192.0.2.1:443 ssl http2;"),
         ["server 0 0 - S", "server 1 1 http://127.0.0.1:5000 S"]),
        (SITE.replace("proxy_pass http://127.0.0.1:5000;\n        proxy_set_header",
                      "proxy_pass http://unix:/run/openalgo.sock;\n        proxy_set_header"),
         ["server 0 0 - S", "server 1 1 http://unix:/run/openalgo.sock S"]),
    ],
)  # fmt: skip
def test_where_the_include_stands_is_read_as_nginx_reads_it(site, records):
    assert servers(dump({"S": site})) == records


def test_another_site_on_443_without_the_line_is_left_alone():
    other = "server {\n    listen 443 ssl;\n    server_name other.example;\n    location / { return 404; }\n}\n"
    got = servers(
        dump({"/etc/nginx/sites-enabled/site": SITE, "/etc/nginx/sites-enabled/other": other})
    )
    assert got[-1] == "server 1 0 - /etc/nginx/sites-enabled/other"


# ---- the whole tool --------------------------------------------------------------------------------
WORKERS = "  5 nginx: master process /usr/sbin/nginx\n  5 nginx: worker process\n  5 nginx: worker process\n"


class Edge:
    """The box's nginx: the three files installed as the README says, a site like the server's, and the
    fakes that nginx -T and ps answer through."""

    def __init__(self, box: Box):
        self.box, self.dir = box, box.root / "nginx"
        self.site = self.dir / "sites-enabled" / "sudarshan"
        for f in FILES:
            target = self.dir / f
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(NGINX_SRC / Path(f).name, target)
            target.chmod(0o644)
        self.site.parent.mkdir(parents=True)
        self.site.write_text(SITE)
        self.age(60)
        _shim(box.bin, "nginx", 'echo "$*" >> "${FAKE_WEB}/nginx_args"\n'
              'if [[ "$1" == "-T" ]]; then cat "${FAKE_WEB}/nginx_dump"; '
              'echo "nginx: configuration file test is successful" >&2; '
              'exit "$(cat "${FAKE_WEB}/nginx_rc" 2>/dev/null || echo 0)"; fi\nexit 0')  # fmt: skip
        _shim(box.bin, "ps", 'if [[ "$*" == "-C nginx -o etimes=,args=" ]]; then '
              'cat "${FAKE_WEB}/ps_out" 2>/dev/null; exit 0; fi\nexec /bin/ps "$@"')  # fmt: skip
        (box.web / "ps_out").write_text(WORKERS)
        (box.app / ".env").write_text("SESSION_COOKIE_NAME = 'session'\n"
                                      "HOST_SERVER = 'https://example.invalid'\n")  # fmt: skip
        self.loads()

    def loads(self, site: str | None = None, rc: int = 0, leave_out: tuple[str, ...] = ()) -> None:
        """What nginx -T prints next: nginx.conf, the files it reaches, the site."""
        files = {str(self.dir / "nginx.conf"): "http {\n    include conf.d/*.conf;\n}\n"}
        for f in FILES:
            if f not in leave_out:
                files[str(self.dir / f)] = (self.dir / f).read_text(encoding="utf-8")
        files[str(self.site)] = SITE if site is None else site
        (self.box.web / "nginx_dump").write_text(dump(files))
        (self.box.web / "nginx_rc").write_text(str(rc))

    def age(self, seconds: int) -> None:
        then = time.time() - seconds
        for path in [*(self.dir / f for f in FILES), self.site]:
            if path.exists():
                os.utime(path, (then, then))


def pointed_box(tmp_path: Path, monkeypatch) -> Box:
    """A quiet box whose tool looks for nginx's files in <box>/nginx instead of /etc/nginx."""
    tool = tmp_path / "tool"
    text = tool_text()
    assert text.count('NGX_DIR="/etc/nginx"') == 1
    box_root = tmp_path / "box"
    box_root.mkdir()
    tool.write_text(text.replace('NGX_DIR="/etc/nginx"', f'NGX_DIR="{box_root / "nginx"}"'))
    monkeypatch.setattr(calendar_harness, "DEPLOY_TOOL", tool)
    return quiet_box(box_root)


@pytest.fixture
def edge(tmp_path, monkeypatch):
    return Edge(pointed_box(tmp_path, monkeypatch))


def check(edge: Edge) -> tuple[int, list[str]]:
    done = edge.box.run("--nginx-check")
    assert not done.stderr, done.stderr
    return done.returncode, done.stdout.splitlines()


@whole_tool
def test_installed_loaded_and_this_releases(edge):
    rc, out = check(edge)
    assert rc == 0, out
    d = edge.dir
    assert out == [
        f"nginx: PASS {d}/conf.d/sudarshan-family-zones.conf: in place and this release's, loaded by nginx",
        f"nginx: PASS {d}/snippets/sudarshan-family.conf: in place and this release's, loaded by nginx",
        f"nginx: PASS {d}/snippets/sudarshan-family-proxy.conf: in place and this release's, loaded by nginx",
        f"nginx: PASS the guard sits in the server block for port 443 of {edge.site}, so it covers every "
        "location there",
        "nginx: PASS the invite page goes to OpenAlgo at 127.0.0.1:5000, as location / of that server does",
        "nginx: PASS the guard knows OpenAlgo's session cookie, __Secure-session, as OpenAlgo's .env makes it",
        "nginx: PASS the running nginx has loaded them (it was reloaded after they last changed)",
        "nginx: PASS. The family view's nginx lines are installed, loaded and this release's.",
    ]
    assert (edge.box.web / "nginx_args").read_text() == "-T\n"  # it only asks


def fails(out: list[str]) -> list[str]:
    return [line.removeprefix("nginx: FAIL ") for line in out if line.startswith("nginx: FAIL ")]


@whole_tool
def test_nothing_installed(edge):
    for f in FILES:
        (edge.dir / f).unlink()
    rc, out = check(edge)
    assert rc == 1
    assert out == [f"nginx: FAIL none of the family view's nginx files is in {edge.dir}: install them "
                   '(sudarshan/family/README.md, "Installing the nginx lines")']  # fmt: skip
    assert not (edge.box.web / "nginx_args").exists()  # nginx is not even asked


@whole_tool
def test_a_file_missing_or_not_the_releases_or_open_to_others(edge):
    (edge.dir / FILES[2]).unlink()
    server = edge.dir / FILES[1]
    server.write_text(server.read_text() + "# changed by hand\n")
    (edge.dir / FILES[0]).chmod(0o664)
    edge.age(60)
    edge.loads(leave_out=(FILES[2],))
    rc, out = check(edge)
    assert rc == 1
    assert fails(out)[:3] == [
        f"{edge.dir}/{FILES[0]} is not a plain file of root's that only root can write: install the files again",
        f"{server} is not this release's copy (an older release's, or changed by hand): install the files "
        'again (sudarshan/family/README.md, "Installing the nginx lines")',
        f"{edge.dir}/{FILES[2]} is missing: install the files again "
        '(sudarshan/family/README.md, "Installing the nginx lines")',
    ]
    assert out[-1] == "nginx: FAIL. Nothing was changed; each FAIL line above says what to do."


@whole_tool
def test_the_owners_own_cookie_name(edge):
    zones = edge.dir / FILES[0]
    zones.write_text(zones.read_text().replace("__Secure-session=", "__Secure-app_session="))
    edge.age(60)
    edge.loads()
    rc, out = check(edge)
    assert rc == 1
    assert fails(out) == [
        "the guard knows OpenAlgo's session cookie as __Secure-app_session, but OpenAlgo's .env makes it "
        f"__Secure-session: put __Secure-session in its place in {zones} (under map $http_cookie "
        "$sud_owner), then reload nginx"
    ]
    (edge.box.app / ".env").write_text("SESSION_COOKIE_NAME = 'app_session'  # mine\n"
                                       "HOST_SERVER = 'https://example.invalid'\n")  # fmt: skip
    rc, out = check(edge)
    assert rc == 0 and "__Secure-app_session, as OpenAlgo's .env makes it" in out[5]
    (edge.box.app / ".env").write_text(
        "SESSION_COOKIE_NAME = 'app_session'\nHOST_SERVER = 'http://x'\n"
    )
    assert (
        "makes it app_session: put app_session" in fails(check(edge)[1])[0]
    )  # no __Secure- on http


@whole_tool
def test_nginxs_own_test_fails(edge):
    edge.loads(rc=1)
    rc, out = check(edge)
    assert rc == 1
    assert fails(out) == ["nginx's configuration does not pass its own test, so no change of it can be "
                          "loaded: run sudo nginx -t to see why"]  # fmt: skip


@whole_tool
@pytest.mark.parametrize(
    ("site", "says"),
    [
        (SITE.replace("    include snippets/sudarshan-family.conf;\n", ""),
         "no server block includes snippets/sudarshan-family.conf: add the include line to the server block "
         'for port 443 (sudarshan/family/README.md, "Installing the nginx lines")'),
        (SITE.replace("    include snippets/sudarshan-family.conf;\n", "").replace(
            "listen [::]:80;\n", "listen [::]:80;\n    include snippets/sudarshan-family.conf;\n"),
         "snippets/sudarshan-family.conf is included in a server block that does not listen on port 443 "
         "(SITE): move the line into the server block for port 443"),
        (SITE.replace("proxy_pass http://127.0.0.1:5000;\n        proxy_set_header",
                      "proxy_pass http://unix:/run/openalgo.sock;\n        proxy_set_header"),
         "location / of that server goes to http://unix:/run/openalgo.sock, but the invite page goes to "
         "http://127.0.0.1:5000: invite links will not open until the two agree"),
    ],
)  # fmt: skip
def test_where_the_line_stands_in_the_site(edge, site, says):
    edge.loads(site=site)
    rc, out = check(edge)
    assert rc == 1 and fails(out) == [says.replace("SITE", str(edge.site))]


@whole_tool
def test_not_reloaded_since_the_files_changed_or_not_running(edge):
    edge.age(0)  # changed now; the workers started 5 seconds ago
    (edge.box.web / "ps_out").write_text(WORKERS + "  1 nginx: worker process is shutting down\n")
    rc, out = check(edge)
    assert rc == 1
    assert fails(out) == ["nginx has not loaded these files since they last changed: run sudo nginx -t && "
                          "sudo systemctl reload nginx"]  # fmt: skip
    (edge.box.web / "ps_out").write_text("")
    assert fails(check(edge)[1]) == ["nginx is not running"]


def server_files(box: Box) -> dict[str, tuple]:
    """Everything on the box but the fakes' notes, the tool's own temporary folder and its lock."""
    skip = (str(box.web), str(box.tmp), str(box.state / "deploy.lock"))
    return {k: v for k, v in _snapshot(box.root).items() if not k.startswith(skip)}


@whole_tool
def test_it_changes_nothing_and_takes_no_lock(edge):
    lock = edge.box.state / "deploy.lock"
    lock.touch()
    before = server_files(edge.box)
    holder = subprocess.Popen(["flock", str(lock), "sleep", "30"])
    try:
        time.sleep(0.2)
        started = time.monotonic()
        rc, _ = check(edge)
        assert (
            rc == 0 and time.monotonic() - started < 20
        )  # an install holding the lock does not hold it up
    finally:
        holder.kill()
        holder.wait()
    assert server_files(edge.box) == before


@whole_tool
def test_a_run_says_a_problem_once_and_nothing_while_none_is_installed(edge):
    edge.age(0)  # not reloaded yet
    first = edge.box.run()
    assert first.returncode == 0, first.stdout + first.stderr
    said = [line for line in first.stdout.splitlines() if "family: nginx:" in line]
    assert len(said) == 1 and said[0].endswith(
        "family: nginx: nginx has not loaded these files since they last changed: run sudo nginx -t && "
        "sudo systemctl reload nginx (sudarshan-deploy --nginx-check says more)")  # fmt: skip
    assert "family: nginx:" not in edge.box.run().stdout  # once while it holds
    edge.age(60)  # reloaded: nothing to say
    assert "nginx" not in edge.box.run().stdout
    assert not (edge.box.state / "family_nginx_note").exists()
    for f in FILES:
        (edge.dir / f).unlink()
    assert "nginx" not in edge.box.run().stdout  # the owner took them out: nothing is said


@whole_tool
def test_a_server_without_the_files_gets_nothing_new(tmp_path, monkeypatch):
    """On a server where the owner has not installed them, a run neither asks nginx nor says anything."""
    box = pointed_box(tmp_path, monkeypatch)
    (box.root / "nginx" / "conf.d").mkdir(
        parents=True
    )  # nginx is there; the family's files are not
    _shim(box.bin, "nginx", 'echo "$*" >> "${FAKE_WEB}/nginx_args"; exit 0')
    box.feed_tokens_in_place()
    done = box.run()
    assert (done.returncode, done.stdout) == (0, "")
    assert not (box.web / "nginx_args").exists()
    assert not (box.state / "family_nginx_note").exists()


@whole_tool
def test_the_family_set_up_says_one_line_before_its_pass(edge):
    bootstrapped(edge.box)
    done = edge.box.run("--family-provision")
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout.splitlines()
    assert (
        out[-2]
        == "family: nginx: the family view's nginx lines are installed, loaded and this release's"
    )
    assert out[-1].startswith("family: PASS: ")
    for f in FILES:
        (edge.dir / f).unlink()
    out = edge.box.run("--family-provision").stdout.splitlines()
    assert out[-2] == ("family: nginx: the family view's nginx lines are not installed yet "
                       '(sudarshan/family/README.md, "Installing the nginx lines")')  # fmt: skip
