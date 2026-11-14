"""O3 (P6; M2.11, M2.16; cross-design Y7, Y33): the deploy tool's check of the family view's nginx lines.

``sudarshan-deploy --nginx-check`` says, reading only, whether the three files the owner installs from
``sudarshan/ops/nginx`` are in place and this release's, whether nginx's own test passes, whether the
include line sits in the server block for port 443 of the SUDARSHAN site file (whose path the owner
records in /etc/sudarshan/nginx-site), whether that server's location / goes where the invite page goes,
whether the guard knows OpenAlgo's cookie as OpenAlgo's .env makes it, whether the running nginx was
reloaded after the four files changed, and warns when OpenAlgo trusts forwarded addresses while that
location / passes on the two headers a caller can fake. Of nginx it reads those four files and nothing
else: never another site's file, never a folder listing, and of ``nginx -t`` only its exit status. A
timer run logs the first problem once while it holds and says nothing while none of the files is there;
the family set-up adds one line before its PASS.

The pieces of the tool run on their own (``_function``); the whole tool runs on the fake server of
``test_deploy_calendar.py``, with ``NGX_DIR`` pointed into the box (``monkeypatch`` on its
``DEPLOY_TOOL``, as ``test_deploy_family.py`` does), a fake ``nginx`` whose ``-t`` answers what a test
says, and a fake ``ps`` for its workers. With an nginx binary the whole tool also runs against a real
``nginx -t`` (skipped without one), and with strace a run is traced to show it opens nothing else.
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
HOWTO = '(sudarshan/family/README.md, "Installing the nginx lines")'
CLEAR = ('proxy_set_header CF-Connecting-IP "";', 'proxy_set_header True-Client-IP "";')
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def tool_text() -> str:
    return DEPLOY_TOOL.read_text()


def bash(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script, "bash", *args], capture_output=True, text=True,
                          timeout=60)  # fmt: skip


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
    name the include line, the record of the site file, the two lines the warning asks for, the
    reload and the check the tool's lines send the owner to."""
    readme = (REPO / "sudarshan" / "family" / "README.md").read_text(encoding="utf-8")
    text = tool_text()
    assert re.search(
        r'\nNGX_HOWTO=\'sudarshan/family/README\.md, "Installing the nginx lines"\'\n', text
    )
    assert (
        '\nCAL_DIR="/etc/sudarshan"\n' in text
        and '\nNGX_SITE_RECORD="${CAL_DIR}/nginx-site"' in text
    )
    section = readme.split("\n## Installing the nginx lines (O3)\n", 1)[1]
    (line,) = [x for x in section.splitlines() if x.lstrip().startswith("sudo bash -c 'set -e;")]
    placed = {}
    pattern = r'install -m 644 -o root -g root ((?:"\$d/[^"]+" )+)/etc/nginx/([a-z.]+)/;'
    for sources, folder in re.findall(pattern, line):
        for name in re.findall(r'"\$d/([^"]+)"', sources):
            placed[name] = folder
    assert placed == {Path(f).name: f.split("/")[0] for f in FILES}
    assert "for f in " + " ".join(Path(f).name for f in FILES) + "; do" in line
    assert "/sudarshan/ops/nginx/$f" in line and "$(cat /var/lib/sudarshan/deployed_tag)" in line
    for step in (
        "    include snippets/sudarshan-family.conf;",
        "echo /etc/nginx/sites-enabled/<that file> | sudo tee /etc/sudarshan/nginx-site",
        "sudo nginx -t && sudo systemctl reload nginx",
        "sudo sudarshan-deploy --nginx-check",
        "map $http_cookie $sud_owner",
    ):
        assert step in section, step
    # the two lines of the optional step, one under the other, as they go inside location /
    assert re.search(r"\n +" + re.escape(CLEAR[0]) + r"\n +" + re.escape(CLEAR[1]) + r"\n", section)
    in_the_warning = " ".join(
        line.replace('"', '\\"') for line in CLEAR
    )  # as the tool's WARN line writes them
    assert in_the_warning in text


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
    read_back = _function("ngx_cookie") + 'ngx_cookie "$1"'
    assert bash(read_back, str(own)).stdout == "__Secure-app_session\n"
    assert bash(read_back, str(zones)).stdout == "__Secure-session\n"
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


# ---- reading the SUDARSHAN site file ------------------------------------------------------------------
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
CLEARED = SITE.replace(
    "        proxy_set_header Host $host;\n",
    f"        proxy_set_header Host $host;\n        {CLEAR[0]}\n        {CLEAR[1]}\n",
)


def site_records(tmp_path: Path, text: str) -> list[str]:
    site = tmp_path / "site"
    site.write_text(text)
    done = bash(_function("ngx_site") + 'ngx_site "$1"', str(site))
    assert done.returncode == 0, done.stderr
    return done.stdout.splitlines()


@pytest.mark.parametrize(
    ("site", "records"),
    [
        (SITE, ["server 0 0 - 0 0", "server 1 1 http://127.0.0.1:5000 0 0"]),
        (SITE.replace("    include snippets/sudarshan-family.conf;\n    location /socket.io/",
                      "    location /socket.io/").replace(
                          "listen [::]:80;\n", "listen [::]:80;\n    include snippets/sudarshan-family.conf;\n"),
         ["server 0 1 - 0 0", "server 1 0 http://127.0.0.1:5000 0 0"]),
        ("include /etc/nginx/snippets/sudarshan-family.conf;\n" + SITE,
         ["stray", "server 0 0 - 0 0", "server 1 1 http://127.0.0.1:5000 0 0"]),
        (SITE.replace("listen 443 ssl;", "listen 8443 ssl;").replace("listen [::]:443 ssl;", ""),
         ["server 0 0 - 0 0", "server 0 1 http://127.0.0.1:5000 0 0"]),
        (SITE.replace("listen 443 ssl;", "listen 192.0.2.1:443 ssl http2;"),
         ["server 0 0 - 0 0", "server 1 1 http://127.0.0.1:5000 0 0"]),
        (SITE.replace("proxy_pass http://127.0.0.1:5000;\n        proxy_set_header",
                      "proxy_pass http://unix:/run/openalgo.sock;\n        proxy_set_header"),
         ["server 0 0 - 0 0", "server 1 1 http://unix:/run/openalgo.sock 0 0"]),
        (CLEARED, ["server 0 0 - 0 0", "server 1 1 http://127.0.0.1:5000 1 1"]),
        (SITE.replace("        proxy_set_header Host $host;\n", f"        {CLEAR[0].lower()}\n"),
         ["server 0 0 - 0 0", "server 1 1 http://127.0.0.1:5000 1 0"]),
        # at server level the two lines reach location / only while it has no proxy_set_header of its own
        (SITE.replace("    set $openalgo_loggable 1;\n", f"    {CLEAR[0]}\n    {CLEAR[1]}\n"),
         ["server 0 0 - 0 0", "server 1 1 http://127.0.0.1:5000 0 0"]),
        (SITE.replace("    set $openalgo_loggable 1;\n", f"    {CLEAR[0]}\n    {CLEAR[1]}\n").replace(
            "        proxy_set_header Host $host;\n", ""),
         ["server 0 0 - 0 0", "server 1 1 http://127.0.0.1:5000 1 1"]),
    ],
)  # fmt: skip
def test_the_site_file_is_read_as_nginx_reads_it(tmp_path, site, records):
    assert site_records(tmp_path, site) == records


# ---- the whole tool --------------------------------------------------------------------------------
WORKERS = "  5 nginx: master process /usr/sbin/nginx\n  5 nginx: worker process\n  5 nginx: worker process\n"
ENV = "SESSION_COOKIE_NAME = 'session'\nHOST_SERVER = 'https://example.invalid'\n"


class Edge:
    """The box's nginx: the three files installed as the README says, the SUDARSHAN site recorded as
    the README says, and the fakes nginx -t and ps answer through."""

    def __init__(self, box: Box, site: str = SITE):
        self.box, self.dir = box, box.root / "nginx"
        self.site = self.dir / "sites-enabled" / "sudarshan"
        self.record = box.etc / "nginx-site"
        for f in FILES:
            target = self.dir / f
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(NGINX_SRC / Path(f).name, target)
            target.chmod(0o644)
        self.site.parent.mkdir(parents=True)
        self.site.write_text(site)
        self.record.write_text(f"{self.site}\n")
        self.record.chmod(0o644)
        self.age(60)
        _shim(box.bin, "nginx", 'echo "$*" >> "${FAKE_WEB}/nginx_args"\n'
              'exit "$(cat "${FAKE_WEB}/nginx_rc" 2>/dev/null || echo 0)"')  # fmt: skip
        _shim(box.bin, "ps", 'if [[ "$*" == "-C nginx -o etimes=,args=" ]]; then '
              'cat "${FAKE_WEB}/ps_out" 2>/dev/null; exit 0; fi\nexec /bin/ps "$@"')  # fmt: skip
        (box.web / "ps_out").write_text(WORKERS)
        (box.app / ".env").write_text(ENV)

    def nginx_test(self, rc: int) -> None:
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


def check(edge: Edge, prefix: tuple[str, ...] = ()) -> tuple[int, list[str]]:
    done = edge.box.run("--nginx-check", prefix=prefix)
    assert not done.stderr, done.stderr
    return done.returncode, done.stdout.splitlines()


def fails(out: list[str]) -> list[str]:
    return [line.removeprefix("nginx: FAIL ") for line in out if line.startswith("nginx: FAIL ")]


def warns(out: list[str]) -> list[str]:
    return [line.removeprefix("nginx: WARN ") for line in out if line.startswith("nginx: WARN ")]


def passing(edge: Edge, tested: bool = True) -> list[str]:
    d = edge.dir
    loads = ": it covers every location there, and nginx loads all three files through it"
    return [
        f"nginx: PASS {d}/conf.d/sudarshan-family-zones.conf: in place and this release's",
        f"nginx: PASS {d}/snippets/sudarshan-family.conf: in place and this release's",
        f"nginx: PASS {d}/snippets/sudarshan-family-proxy.conf: in place and this release's",
        "nginx: PASS nginx's own test passes",
        f"nginx: PASS the guard sits in the server block for port 443 of {edge.site}"
        + (loads if tested else ", so it covers every location there"),
        "nginx: PASS the invite page goes to OpenAlgo at 127.0.0.1:5000, as location / of that server does",
        "nginx: PASS the guard knows OpenAlgo's session cookie, __Secure-session, as OpenAlgo's .env makes it",
        "nginx: PASS the running nginx has loaded them (it was reloaded after they last changed)",
    ]


@whole_tool
def test_installed_loaded_and_this_releases(edge):
    rc, out = check(edge)
    assert rc == 0, out
    assert out == [
        *passing(edge),
        "nginx: PASS. The family view's nginx lines are installed, loaded and this release's.",
    ]
    assert (edge.box.web / "nginx_args").read_text() == "-t\n"  # its test, never a dump


@whole_tool
def test_nothing_installed(edge):
    for f in FILES:
        (edge.dir / f).unlink()
    rc, out = check(edge)
    assert rc == 1
    assert out == [
        f"nginx: FAIL none of the family view's nginx files is in {edge.dir}: install them {HOWTO}"
    ]
    assert not (edge.box.web / "nginx_args").exists()  # nginx is not even asked


@whole_tool
def test_a_file_missing_or_not_the_releases_or_open_to_others(edge):
    (edge.dir / FILES[2]).unlink()
    server = edge.dir / FILES[1]
    server.write_text(server.read_text() + "# changed by hand\n")
    (edge.dir / FILES[0]).chmod(0o664)
    edge.age(60)
    rc, out = check(edge)
    assert rc == 1
    assert fails(out) == [
        f"{edge.dir}/{FILES[0]} is not a plain file of root's that only root can write: install the files again",
        f"{server} is not this release's copy (an older release's, or changed by hand): install the files "
        f"again {HOWTO}",
        f"{edge.dir}/{FILES[2]} is missing: install the files again {HOWTO}",
    ]
    assert out[-1] == "nginx: FAIL. Nothing was changed; each FAIL line above says what to do."


def record_command(edge: Edge) -> str:
    return f"echo {edge.dir}/sites-enabled/<the SUDARSHAN site> | sudo tee {edge.record}"


@whole_tool
def test_the_site_file_must_be_recorded(edge):
    edge.record.unlink()
    rc, out = check(edge)
    assert rc == 1
    assert fails(out) == [
        "the SUDARSHAN site file is not recorded, so where its include line stands cannot be checked: "
        f"record it with {record_command(edge)}"
    ]
    assert (
        len(out) == len(passing(edge)) - 2 + 2
    )  # no line about the include, the invite page or warnings


@whole_tool
@pytest.mark.parametrize(
    ("record", "mode", "what"),
    [
        ("{dir}/sites-available/sudarshan\n", 0o644, "record"),
        ("sites-enabled/sudarshan\n", 0o644, "record"),
        ("{dir}/sites-enabled/../sites-enabled/sudarshan\n", 0o644, "record"),
        ("{dir}/conf.d/sudarshan\n", 0o644, "record"),  # in conf.d only a .conf is read by nginx
        ("{site}\n", 0o666, "record"),  # anyone could point the check elsewhere
        ("{dir}/sites-enabled/gone\n", 0o644, "file"),
        ("  {site}  \n", 0o644, None),  # spaces typed around it are fine
        ("{site}", 0o644, None),  # and no newline
    ],
)
def test_what_the_record_may_name(edge, record, mode, what):
    edge.record.write_text(record.format(dir=edge.dir, site=edge.site))
    edge.record.chmod(mode)
    rc, out = check(edge)
    if what is None:
        assert rc == 0, out
        return
    assert rc == 1
    says = {
        "record": f"{edge.record} does not name one site file in {edge.dir}/sites-enabled (or a .conf in "
        f"{edge.dir}/conf.d), or is not root's alone: record it again with {record_command(edge)}",
        "file": f"the site file that {edge.record} names is missing, or is not root's alone: check it, then "
        f"record it again with {record_command(edge)}",
    }
    assert fails(out) == [says[what]]


@whole_tool
def test_a_site_file_others_can_write_is_not_trusted(edge):
    edge.site.chmod(0o664)
    assert fails(check(edge)[1]) == [
        f"the site file that {edge.record} names is missing, or is not root's alone: check it, then record "
        f"it again with {record_command(edge)}"
    ]


@whole_tool
def test_the_owners_own_cookie_name(edge):
    zones = edge.dir / FILES[0]
    zones.write_text(zones.read_text().replace("__Secure-session=", "__Secure-app_session="))
    edge.age(60)
    rc, out = check(edge)
    assert rc == 1
    assert fails(out) == [
        "the guard knows OpenAlgo's session cookie as __Secure-app_session, but OpenAlgo's .env makes it "
        f"__Secure-session: put __Secure-session in its place in {zones} (under map $http_cookie "
        "$sud_owner), then reload nginx"
    ]
    (edge.box.app / ".env").write_text(ENV.replace("'session'", "'app_session'  # mine"))
    rc, out = check(edge)
    assert rc == 0 and "__Secure-app_session, as OpenAlgo's .env makes it" in out[6]
    (edge.box.app / ".env").write_text(
        "SESSION_COOKIE_NAME = 'app_session'\nHOST_SERVER = 'http://x'\n"
    )
    # no __Secure- prefix on plain http
    assert "makes it app_session: put app_session" in fails(check(edge)[1])[0]


@whole_tool
def test_nginxs_own_test_fails(edge):
    edge.nginx_test(1)
    rc, out = check(edge)
    assert rc == 1
    assert fails(out) == ["nginx's configuration does not pass its own test, so no change of it can be "
                          "loaded: run sudo nginx -t to see why"]  # fmt: skip
    # nothing is said about loading what does not load, nor about a reload that cannot happen
    assert out[4] == passing(edge, tested=False)[4] and len(out) == len(passing(edge))


@whole_tool
@pytest.mark.parametrize(
    ("site", "says"),
    [
        (SITE.replace("    include snippets/sudarshan-family.conf;\n", ""),
         "SITE does not include snippets/sudarshan-family.conf in its server block for port 443: add the "
         f"include line {HOWTO}"),
        (SITE.replace("    include snippets/sudarshan-family.conf;\n", "").replace(
            "listen [::]:80;\n", "listen [::]:80;\n    include snippets/sudarshan-family.conf;\n"),
         "snippets/sudarshan-family.conf is included in a server block of SITE that does not listen on port "
         "443: move the line into its server block for port 443"),
        ("include snippets/sudarshan-family.conf;\n" + SITE,
         "snippets/sudarshan-family.conf is included outside a server block in SITE: put the line inside its "
         "server block for port 443"),
        (SITE.replace("proxy_pass http://127.0.0.1:5000;\n        proxy_set_header",
                      "proxy_pass http://unix:/run/openalgo.sock;\n        proxy_set_header"),
         "location / of that server goes to http://unix:/run/openalgo.sock, but the invite page goes to "
         "http://127.0.0.1:5000: invite links will not open until the two agree"),
    ],
)  # fmt: skip
def test_where_the_line_stands_in_the_site(edge, site, says):
    edge.site.write_text(site)
    edge.age(60)
    rc, out = check(edge)
    assert rc == 1 and fails(out) == [says.replace("SITE", str(edge.site))]


WARNING = (
    "OpenAlgo's .env trusts the address a request names (TRUST_PROXY_HEADERS), and location / of SITE "
    "passes on the CF-Connecting-IP and True-Client-IP a caller writes, which OpenAlgo reads first: anyone "
    "can give OpenAlgo's ban list and sign-in limiter any address. Add these two lines inside that "
    f"location, then reload nginx: {CLEAR[0]} {CLEAR[1]}"
)


@whole_tool
@pytest.mark.parametrize(
    ("trust", "site", "warned"),
    [
        ("TRUST_PROXY_HEADERS = 'TRUE'\n", SITE, True),
        ("TRUST_PROXY_HEADERS = 'true'  # behind nginx\n", SITE, True),
        ("TRUST_PROXY_HEADERS=1\n", SITE, True),
        ("TRUST_PROXY_HEADERS = 'yes'\n", SITE, True),
        ("TRUST_PROXY_HEADERS = 'TRUE'\n", CLEARED, False),
        ("TRUST_PROXY_HEADERS = 'TRUE'\n", CLEARED.replace(f"        {CLEAR[1]}\n", ""), True),
        ("TRUST_PROXY_HEADERS = 'FALSE'\n", SITE, False),
        ("", SITE, False),  # OpenAlgo's own default is FALSE
    ],
)
def test_the_warning_about_addresses_a_caller_can_fake(edge, trust, site, warned):
    (edge.box.app / ".env").write_text(ENV + trust)
    edge.site.write_text(site)
    edge.age(60)
    rc, out = check(edge)
    assert rc == 0, out  # a warning is not a failure of the family view's lines
    if warned:
        assert warns(out) == [WARNING.replace("SITE", str(edge.site))]
        assert out[-1] == ("nginx: PASS. The family view's nginx lines are installed, loaded and this "
                           "release's (one warning above).")  # fmt: skip
    else:
        assert warns(out) == [] and out[-1].endswith("and this release's.")


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
        # an install holding the lock does not hold the check up
        assert rc == 0 and time.monotonic() - started < 20
    finally:
        holder.kill()
        holder.wait()
    assert server_files(edge.box) == before


@whole_tool
@pytest.mark.skipif(shutil.which("strace") is None, reason="needs strace")
def test_it_reads_no_other_site_and_lists_no_folder(edge, tmp_path):
    """The fork's rule: nothing of TRINETRA is ever read. Another site beside the SUDARSHAN one, in
    sites-enabled and in conf.d, is never opened, looked at or listed, and no folder of nginx's is
    listed: every file call the run makes is traced."""
    others = (edge.dir / "sites-enabled" / "other", edge.dir / "conf.d" / "other.conf")
    others[0].write_text("server { listen 443 ssl; server_name other.example; }\n")
    others[1].write_text("map $host $other { default 0; }\n")
    log = tmp_path / "trace"
    rc, out = check(edge, prefix=("strace", "-f", "-qq", "-y", "-e", "trace=%file,getdents64",
                                  "-o", str(log)))  # fmt: skip
    assert rc == 0, out
    trace = log.read_text(errors="replace")
    assert str(edge.site) in trace and str(edge.dir / FILES[0]) in trace  # it does read its own
    for line in trace.splitlines():
        assert not any(str(path) in line for path in others), line
        if "getdents" in line:
            assert str(edge.dir) not in line, line
    assert (edge.box.web / "nginx_args").read_text() == "-t\n"


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
    (edge.box.app / ".env").write_text(
        ENV + "TRUST_PROXY_HEADERS = 'TRUE'\n"
    )  # a warning: said once too
    said = [line for line in edge.box.run().stdout.splitlines() if "family: nginx:" in line]
    assert len(said) == 1 and "family: nginx: WARN OpenAlgo's .env trusts the address" in said[0]
    assert "family: nginx:" not in edge.box.run().stdout
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
    (edge.box.app / ".env").write_text(ENV + "TRUST_PROXY_HEADERS = 'TRUE'\n")
    out = edge.box.run("--family-provision").stdout.splitlines()
    assert out[-2].endswith("this release's (one warning: sudarshan-deploy --nginx-check says it)")
    for f in FILES:
        (edge.dir / f).unlink()
    out = edge.box.run("--family-provision").stdout.splitlines()
    assert out[-2] == f"family: nginx: the family view's nginx lines are not installed yet {HOWTO}"


# ---- a real nginx -t ------------------------------------------------------------------------------------
def nginx_binary() -> str | None:
    for found in (os.environ.get("SUDARSHAN_NGINX"), shutil.which("nginx"), "/usr/sbin/nginx"):
        if found and os.path.isfile(found) and os.access(found, os.X_OK):
            return found
    return None


NGINX = nginx_binary()
# `nginx -t` opens and binds every listen socket (only "already in use" is let pass), so a real test can
# use neither port 443 without root nor [::] on a host without IPv6: the site listens on free loopback
# ports, and the check's port 443 rule is held by the tests above, with the fake nginx.
OTHER = "server {\n    listen 127.0.0.1:{port};\n    server_name other.test;\n    location / { return 404; }\n}\n"


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def real_site(include: bool = True) -> str:
    site = SITE.replace("    listen [::]:80;\n", "").replace("    listen [::]:443 ssl;\n", "")
    site = site.replace("listen 80;", f"listen 127.0.0.1:{free_port()};")
    site = site.replace("listen 443 ssl;", f"listen 127.0.0.1:{free_port()};")
    return site if include else site.replace("    include snippets/sudarshan-family.conf;\n", "")


@whole_tool
@pytest.mark.skipif(NGINX is None, reason="no nginx binary (set SUDARSHAN_NGINX)")
def test_a_real_nginx_test_decides_whether_the_files_load(edge):
    """The box's nginx folder made a real nginx's configuration, as Ubuntu lays it out (conf.d and
    sites-enabled, another site beside SUDARSHAN's), and the check's `nginx -t` run by the real nginx:
    the include line takes in the other two files, so nginx's own test fails without either of them,
    and passes without the line, which the check then says is missing."""
    d = edge.dir
    (d / "tmp").mkdir()
    (d / "nginx.conf").write_text(
        f"pid {d}/nginx.pid;\nerror_log stderr;\nevents {{ worker_connections 64; }}\nhttp {{\n"
        f"    access_log off;\n    client_body_temp_path {d}/tmp/body;\n    proxy_temp_path {d}/tmp/proxy;\n"
        f"    fastcgi_temp_path {d}/tmp/fastcgi;\n    uwsgi_temp_path {d}/tmp/uwsgi;\n"
        f"    scgi_temp_path {d}/tmp/scgi;\n    include conf.d/*.conf;\n    include sites-enabled/*;\n}}\n"
    )
    (d / "sites-enabled" / "other").write_text(OTHER.replace("{port}", str(free_port())))
    _shim(edge.box.bin, "nginx", f'exec "{NGINX}" -p "{d}" -c "{d}/nginx.conf" "$@"')
    not_443 = (f"snippets/sudarshan-family.conf is included in a server block of {edge.site} that does not "
               "listen on port 443: move the line into its server block for port 443")  # fmt: skip
    edge.site.write_text(real_site())
    edge.age(60)
    rc, out = check(edge)
    assert out[3] == "nginx: PASS nginx's own test passes", out
    assert fails(out) == [not_443]  # the real test passes; only this layout's port differs
    zones = d / FILES[0]
    kept = zones.read_bytes()
    zones.unlink()  # the snippet's guard and limits need the zones file
    rc, out = check(edge)
    assert rc == 1 and fails(out) == [
        f"{zones} is missing: install the files again {HOWTO}",
        "nginx's configuration does not pass its own test, so no change of it can be loaded: run sudo nginx "
        "-t to see why",
        not_443,
    ]
    zones.write_bytes(kept)
    zones.chmod(0o644)
    proxy = d / FILES[2]
    kept = proxy.read_bytes()
    proxy.unlink()  # the family service's locations include it
    assert check(edge)[1][3].startswith(
        "nginx: FAIL nginx's configuration does not pass its own test"
    )
    proxy.write_bytes(kept)
    proxy.chmod(0o644)
    edge.site.write_text(real_site(include=False))
    edge.age(60)
    rc, out = check(
        edge
    )  # without the line the files are inert: the test passes, the line is missing
    assert rc == 1 and out[3] == "nginx: PASS nginx's own test passes"
    assert fails(out) == [f"{edge.site} does not include snippets/sudarshan-family.conf in its server block "
                          f"for port 443: add the include line {HOWTO}"]  # fmt: skip
