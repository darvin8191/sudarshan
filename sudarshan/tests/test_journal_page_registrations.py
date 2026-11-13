"""The Journal page (M14) and the Reports page (M15.10) need all three registrations (CLAUDE.md,
"Adding a page"). The Flask views only serve the React app, so a refresh or a bookmark opened
while signed out is a known route, not a 404 counted against the visitor's IP."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PAGES = (
    ("journal", "Journal", "react_journal"),
    ("reports", "Reports", "react_reports"),
)


@pytest.mark.parametrize(("path", "component", "view"), PAGES)
def test_route_in_app_tsx(path, component, view):
    source = (REPO / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert f"const {component} = lazy(() => import('@/sudarshan/pages/{component}'))" in source
    assert re.search(rf'<Route path="/{path}" element=\{{<{component} />\}} />', source)


@pytest.mark.parametrize(("path", "component", "view"), PAGES)
def test_known_path_in_react_app(path, component, view):
    from flask import Flask

    from blueprints.react_app import react_bp

    app = Flask("registrations")
    app.register_blueprint(react_bp)
    adapter = app.url_map.bind("localhost")
    for url in (f"/{path}", f"/{path}/"):
        endpoint, _ = adapter.match(url)
        assert endpoint == f"react.{view}"


@pytest.mark.parametrize(("path", "component", "view"), PAGES)
def test_view_serves_the_react_app_only(path, component, view):
    source = (REPO / "blueprints" / "react_app.py").read_text(encoding="utf-8")
    text = (
        f'@react_bp.route("/{path}", strict_slashes=False)\n'
        f"def {view}():\n"
        "    return serve_react_app()\n"
    )
    assert text in source


@pytest.mark.parametrize(("path", "component", "view"), PAGES)
def test_entry_in_the_profile_menu(path, component, view):
    source = (REPO / "frontend" / "src" / "config" / "navigation.ts").read_text(encoding="utf-8")
    assert f"href: '/{path}', label: '{component}'" in source
