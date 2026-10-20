"""The Auto login page needs all three registrations (CLAUDE.md, "Adding a page")."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def test_route_in_app_tsx():
    source = (REPO / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "const AutoLogin = lazy(() => import('@/sudarshan/pages/AutoLogin'))" in source
    assert re.search(r'<Route path="/autologin" element=\{<AutoLogin />\} />', source)


def test_known_path_in_react_app():
    from flask import Flask

    from blueprints.react_app import react_bp

    app = Flask("registrations")
    app.register_blueprint(react_bp)
    adapter = app.url_map.bind("localhost")
    endpoint, _ = adapter.match("/autologin")
    assert endpoint == "react.react_autologin"


def test_entry_in_the_profile_menu():
    source = (REPO / "frontend" / "src" / "config" / "navigation.ts").read_text(encoding="utf-8")
    menu = source.split("export const profileMenuItems: NavItem[] = [", 1)[1].split("\n]\n", 1)[0]
    assert "{ href: '/autologin', label: 'Auto login', icon: KeyRound }" in menu


def test_blueprint_registered_once_in_app_py():
    source = (REPO / "app.py").read_text(encoding="utf-8")
    assert source.count("app.register_blueprint(sudarshan_autologin_bp)") == 1
    assert "from blueprints.sudarshan_autologin import sudarshan_autologin_bp" in source
