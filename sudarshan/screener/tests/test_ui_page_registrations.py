"""The Screener page (M9.3) needs all three registrations (CLAUDE.md, "Adding a page").

The Flask view only serves the React app, so a refresh or a bookmark opened while signed out is a
known route, not a 404 counted against the visitor's IP.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def test_route_in_app_tsx():
    source = (REPO / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "const Screener = lazy(() => import('@/sudarshan/pages/Screener'))" in source
    assert re.search(r'<Route path="/screener" element=\{<Screener />\} />', source)


def test_known_path_in_react_app():
    from flask import Flask

    from blueprints.react_app import react_bp

    app = Flask("registrations")
    app.register_blueprint(react_bp)
    adapter = app.url_map.bind("localhost")
    for path in ("/screener", "/screener/"):
        endpoint, _ = adapter.match(path)
        assert endpoint == "react.react_screener"


def test_view_serves_the_react_app_only():
    source = (REPO / "blueprints" / "react_app.py").read_text(encoding="utf-8")
    view = (
        '@react_bp.route("/screener", strict_slashes=False)\n'
        "def react_screener():\n"
        "    return serve_react_app()\n"
    )
    assert view in source
