"""The family view's shell and its two root files in OpenAlgo (P6 B2; M10.6, M20.10).

/sw.js and /offline.html come from the built frontend, revalidated on every load, and sw.js with
the header that holds its scope to the family shell (cross-design Y26); /family and every page
under it get index.html with exactly one manifest href replaced and every other byte as built;
/family-members is the React app. The files are the frontend lane's, so each test builds a
temporary frontend folder of its own; a file the build lacks answers a clean 404, never one that
counts toward a visitor's 404 ban.
"""

from __future__ import annotations

import pytest
from flask import Flask

import blueprints.react_app as react_app

OWNER_LINK = b'<link rel="manifest" href="/images/sudarshan.webmanifest" />'
FAMILY_LINK = b'<link rel="manifest" href="/images/sudarshan-family.webmanifest" />'
INDEX = (
    '<!doctype html>\n<html lang="en">\n  <head>\n    <meta charset="UTF-8" />\n'
    "    <!-- /images/sudarshan.webmanifest is the owner's; the family shell names its own -->\n"
    '    <!-- <link rel="manifest" href="/images/old.webmanifest" /> -->\n'
    f"    {OWNER_LINK.decode()}\n"
    "    <title>सुदर्शन · SUDARSHAN</title>\n"
    '    <script type="module" crossorigin src="/assets/index-AbC123.js"></script>\n'
    '  </head>\n  <body>\n    <div id="root"></div>\n  </body>\n</html>\n'
).encode()
SW = b"self.addEventListener('fetch', () => {});\n"
OFFLINE = b"<!doctype html><title>Offline</title><p>You are offline.</p>\n"


@pytest.fixture
def dist(tmp_path, monkeypatch):
    root = tmp_path / "dist"
    (root / "images").mkdir(parents=True)
    (root / "index.html").write_bytes(INDEX)
    (root / "sw.js").write_bytes(SW)
    (root / "offline.html").write_bytes(OFFLINE)
    (root / "images" / "sudarshan.webmanifest").write_text("{}")
    (root / "images" / "sudarshan-family.webmanifest").write_text("{}")
    monkeypatch.setattr(react_app, "FRONTEND_DIST", root)
    return root


@pytest.fixture
def app():
    app = Flask("sudarshan-family-shell-tests")
    app.config["TESTING"] = True
    app.register_blueprint(react_app.react_bp)
    app.raised_404 = []

    @app.errorhandler(404)
    def tracked(error):  # stands in for app.py's handler, which feeds the 404 ban
        app.raised_404.append(error)
        return "tracked", 404

    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_the_five_rules_are_registered(app):
    rules = {
        rule.rule: (rule.endpoint, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})),
                    rule.strict_slashes)
        for rule in app.url_map.iter_rules()
    }  # fmt: skip
    assert rules["/sw.js"] == ("react.serve_service_worker", ("GET",), True)
    assert rules["/offline.html"] == ("react.serve_offline_page", ("GET",), True)
    assert rules["/family"] == ("react.react_family_shell", ("GET",), False)
    assert rules["/family/<path:rest>"] == ("react.react_family_shell", ("GET",), False)
    assert rules["/family-members"] == ("react.react_family_members", ("GET",), False)


def test_the_service_worker_is_revalidated_and_held_to_the_family_scope(client, dist):
    response = client.get("/sw.js")
    assert response.status_code == 200 and response.data == SW
    assert response.headers["Cache-Control"] == "no-cache"
    assert response.headers["Service-Worker-Allowed"] == "/family/"
    assert response.mimetype == "text/javascript"  # a worker script in any other type is refused


def test_the_offline_page_is_revalidated(client, dist):
    response = client.get("/offline.html")
    assert response.status_code == 200 and response.data == OFFLINE
    assert response.headers["Cache-Control"] == "no-cache"
    assert response.mimetype == "text/html"


@pytest.mark.parametrize("name", ["sw.js", "offline.html"])
def test_a_root_file_the_build_lacks_is_a_clean_404(app, client, dist, name):
    (dist / name).unlink()
    response = client.get(f"/{name}")
    assert response.status_code == 404 and response.data == b"Not found"
    assert app.raised_404 == []  # returned, not raised: nothing counts toward a ban
    assert response.headers["Cache-Control"] == "no-cache"  # the file is fetched once it ships
    assert "Service-Worker-Allowed" not in response.headers


def test_the_family_shell_replaces_exactly_one_href(client, dist):
    response = client.get("/family")
    assert response.status_code == 200
    assert response.data == INDEX.replace(OWNER_LINK, FAMILY_LINK)
    assert INDEX.count(OWNER_LINK) == 1
    # the comments that name a manifest are as built, byte for byte
    assert response.data.count(b"/images/sudarshan.webmanifest") == 1
    assert b'<!-- <link rel="manifest" href="/images/old.webmanifest" /> -->' in response.data
    assert response.headers["Cache-Control"] == "no-cache"
    assert response.mimetype == "text/html"


@pytest.mark.parametrize(
    "path",
    ["/family", "/family/", "/family/positions", "/family/join/AbC-123_xyz", "/family/a/b/c"],
)
def test_every_family_page_is_the_shell(client, dist, path):
    response = client.get(path)
    assert response.status_code == 200, path
    assert response.data == INDEX.replace(OWNER_LINK, FAMILY_LINK)


def test_the_shell_keeps_the_owners_link_until_the_build_has_the_family_manifest(client, dist):
    (dist / "images" / "sudarshan-family.webmanifest").unlink()
    assert client.get("/family/positions").data == INDEX  # never a link that answers 404


def test_an_index_without_a_manifest_link_is_served_as_built(client, dist):
    bare = INDEX.replace(OWNER_LINK, b"")
    (dist / "index.html").write_bytes(bare)
    assert client.get("/family").data == bare


def test_a_new_build_is_served_at_once(client, dist):
    client.get("/family")
    rebuilt = INDEX.replace(b"index-AbC123.js", b"index-ZyX987.js")
    (dist / "index.html").write_bytes(rebuilt)
    assert client.get("/family").data == rebuilt.replace(OWNER_LINK, FAMILY_LINK)


def test_the_shell_answers_a_revalidation_without_the_body(client, dist):
    first = client.get("/family")
    etag = first.headers["ETag"]
    again = client.get("/family/holdings", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.data == b""


@pytest.mark.parametrize("path", ["/family/api/me", "/family/api", "/family/ws", "/family/api/x/y"])
def test_the_family_services_own_paths_never_get_the_shell(app, client, dist, path):
    response = client.get(path)
    assert response.status_code == 503
    assert response.get_json() == {"status": "error", "code": "off",
                                   "message": react_app.FAMILY_UNREACHED}  # fmt: skip
    assert response.headers["Cache-Control"] == "no-store"
    assert app.raised_404 == []


def test_the_owners_family_page_is_the_react_app(client, dist):
    response = client.get("/family-members")
    assert response.status_code == 200 and response.data == INDEX  # the owner's manifest
    assert client.get("/family-members/").status_code == 200


def test_without_a_build_the_shell_says_so(client, dist):
    (dist / "index.html").unlink()
    response = client.get("/family/positions")
    assert response.status_code == 503 and b"Frontend Not Built" in response.data


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (b"<link rel='manifest' href='/images/sudarshan.webmanifest'>",
         b"<link rel='manifest' href='/images/sudarshan-family.webmanifest'>"),
        (b'<LINK HREF="/m.webmanifest" REL="Manifest">',
         b'<LINK HREF="/images/sudarshan-family.webmanifest" REL="Manifest">'),
        (b'<link rel=manifest href="/m.webmanifest">',
         b'<link rel=manifest href="/images/sudarshan-family.webmanifest">'),
        (b'<link rel="manifest" data-href="/keep" href="/m.webmanifest" />',
         b'<link rel="manifest" data-href="/keep" href="/images/sudarshan-family.webmanifest" />'),
        (b'<link rel="manifest" href="/a"><link rel="manifest" href="/b">',
         b'<link rel="manifest" href="/images/sudarshan-family.webmanifest">'
         b'<link rel="manifest" href="/b">'),
        (b'<!-- <link rel="manifest" href="/c"> --><link rel="manifest" href="/d">',
         b'<!-- <link rel="manifest" href="/c"> -->'
         b'<link rel="manifest" href="/images/sudarshan-family.webmanifest">'),
        (b'<link rel="icon" href="/favicon.ico"><link rel="manifestly" href="/e">',
         b'<link rel="icon" href="/favicon.ico"><link rel="manifestly" href="/e">'),
        (b'<!-- <link rel="manifest" href="/only-in-a-comment"> -->',
         b'<!-- <link rel="manifest" href="/only-in-a-comment"> -->'),
    ],
)  # fmt: skip
def test_the_href_of_the_first_manifest_link_and_nothing_else(index, expected):
    assert react_app.family_shell(index) == expected


def test_no_descriptor_is_left_open(client, dist):
    """Each answer reads its file and closes it (the root files through send_from_directory)."""
    from sudarshan.tests.fdwatch import FdWatch

    client.get("/family")
    watch = FdWatch()
    for _ in range(100):
        for path in ("/family", "/family/positions", "/sw.js", "/offline.html", "/family/api/me"):
            response = client.get(path)
            response.get_data()
            response.close()
    watch.assert_clean()
