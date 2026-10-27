"""The Alerts page (M10.3) needs all three registrations (CLAUDE.md, "Adding a page").

The page lives at /market-alerts because upstream's blueprints/alerts.py owns /alerts (the /trading
chart's alerts, with JSON children such as /alerts/log). The Flask view only serves the React app,
so a refresh or a bookmark opened while signed out is a known route, not a 404 counted against the
visitor's IP.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def test_route_in_app_tsx():
    source = (REPO / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "const MarketAlerts = lazy(() => import('@/sudarshan/pages/MarketAlerts'))" in source
    assert re.search(r'<Route path="/market-alerts" element=\{<MarketAlerts />\} />', source)


def test_known_path_in_react_app():
    from flask import Flask

    from blueprints.react_app import react_bp

    app = Flask("registrations")
    app.register_blueprint(react_bp)
    adapter = app.url_map.bind("localhost")
    for path in ("/market-alerts", "/market-alerts/"):
        endpoint, _ = adapter.match(path)
        assert endpoint == "react.react_market_alerts"


def test_view_serves_the_react_app_only():
    source = (REPO / "blueprints" / "react_app.py").read_text(encoding="utf-8")
    view = (
        '@react_bp.route("/market-alerts", strict_slashes=False)\n'
        "def react_market_alerts():\n"
        "    return serve_react_app()\n"
    )
    assert source.count(view) == 1
    # Beside the auto-login view, its anchor (cross-design R2).
    assert source.index(view) > source.index("def react_autologin():")


def test_entry_in_the_profile_menu():
    source = (REPO / "frontend" / "src" / "config" / "navigation.ts").read_text(encoding="utf-8")
    menu = source.split("export const profileMenuItems: NavItem[] = [", 1)[1].split("\n]\n", 1)[0]
    chart = "{ href: '/chart', label: 'Chart', icon: ChartCandlestick },"
    alerts = "{ href: '/market-alerts', label: 'Alerts', icon: BellRing }"
    assert alerts in menu
    # Right after Chart: only its comment line between them.
    between = menu.split(chart, 1)[1].split(alerts, 1)[0]
    assert [line for line in between.splitlines() if line.strip() and "//" not in line] == []
