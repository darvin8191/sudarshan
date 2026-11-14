"""The System page (M20.8) needs all three registrations (CLAUDE.md, "Adding a page"). The
Flask view only serves the React app, so a refresh or a bookmark opened while signed out is a
known route, not a 404 counted against the visitor's IP; the page's data comes from the owner's
routes in blueprints/sudarshan_system.py."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_route_in_app_tsx():
    source = (REPO / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "const System = lazy(() => import('@/sudarshan/pages/System'))" in source
    assert re.search(r'<Route path="/system" element=\{<System />\} />', source)


def test_known_path_in_react_app():
    from flask import Flask

    from blueprints.react_app import react_bp

    app = Flask("registrations")
    app.register_blueprint(react_bp)
    adapter = app.url_map.bind("localhost")
    for url in ("/system", "/system/"):
        endpoint, _ = adapter.match(url)
        assert endpoint == "react.react_system"


def test_view_serves_the_react_app_only():
    source = (REPO / "blueprints" / "react_app.py").read_text(encoding="utf-8")
    assert (
        '@react_bp.route("/system", strict_slashes=False)\n'
        "def react_system():\n"
        "    return serve_react_app()\n"
    ) in source


def test_entry_in_the_profile_menu():
    source = (REPO / "frontend" / "src" / "config" / "navigation.ts").read_text(encoding="utf-8")
    assert "href: '/system', label: 'System'" in source


def test_the_notices_name_the_page_the_menu_has():
    from sudarshan.alerts import notices

    assert "open the System page" in notices.LOOK
