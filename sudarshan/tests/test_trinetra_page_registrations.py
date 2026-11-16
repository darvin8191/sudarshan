"""The TRINETRA page (M13.7) needs all three registrations (CLAUDE.md, "Adding a page"). The Flask
view only serves the React app, so a refresh or a bookmark opened while signed out is a known
route, not a 404 counted against the visitor's IP; the page's data comes from the owner's routes in
blueprints/sudarshan_signals.py, and the G5 suite classes the path as the React shell."""

from __future__ import annotations

import re
from pathlib import Path

from sudarshan.signals import contract

REPO = Path(__file__).resolve().parents[2]


def test_the_page_is_the_contracts():
    assert contract.PAGE == "/trinetra"


def test_route_in_app_tsx():
    source = (REPO / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "const Trinetra = lazy(() => import('@/sudarshan/pages/Trinetra'))" in source
    assert re.search(r'<Route path="/trinetra" element=\{<Trinetra />\} />', source)


def test_known_path_in_react_app():
    from flask import Flask

    from blueprints.react_app import react_bp

    app = Flask("registrations")
    app.register_blueprint(react_bp)
    adapter = app.url_map.bind("localhost")
    for url in ("/trinetra", "/trinetra/"):
        endpoint, _ = adapter.match(url)
        assert endpoint == "react.react_trinetra"


def test_view_serves_the_react_app_only():
    source = (REPO / "blueprints" / "react_app.py").read_text(encoding="utf-8")
    assert (
        '@react_bp.route("/trinetra", strict_slashes=False)\n'
        "def react_trinetra():\n"
        "    return serve_react_app()\n"
    ) in source


def test_entry_in_the_menu():
    source = (REPO / "frontend" / "src" / "config" / "navigation.ts").read_text(encoding="utf-8")
    assert "href: '/trinetra', label: 'TRINETRA'" in source


def test_the_g5_suite_classes_the_path_as_the_shell():
    source = (REPO / "sudarshan" / "family" / "tests" / "route_classes.py").read_text(
        encoding="utf-8"
    )
    assert re.search(r"^public_shell\s+/trinetra\s+react\.react_trinetra$", source, re.M)
