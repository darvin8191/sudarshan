"""The Portfolio page's known path on the Flask side (M7.18, PD1): /portfolio is served by the
SPA's own view, so a refresh or a bookmark opened while signed out is not a 404 counted against
the visitor's IP, and the view carries SUDARSHAN's comment."""

from __future__ import annotations

from pathlib import Path

from flask import Flask

import blueprints.react_app as react_app

SOURCE = Path(react_app.__file__).read_text()


def test_portfolio_is_a_known_path_served_by_the_spa(monkeypatch):
    monkeypatch.setattr(react_app, "serve_react_app", lambda: "the single page app")
    app = Flask("portfolio-registrations")
    app.register_blueprint(react_app.react_bp)
    client = app.test_client()
    response = client.get("/portfolio")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "the single page app"


def test_the_view_has_sudarshans_comment_and_no_redirect_story():
    view = '@react_bp.route("/portfolio")\ndef react_portfolio_legacy():'
    assert view in SOURCE
    comment = SOURCE[: SOURCE.index(view)].rsplit("\n\n", 1)[-1]
    assert "SUDARSHAN M7.18" in comment
    assert "redirects it to the new one" not in SOURCE


def test_the_backtester_keeps_its_own_path():
    assert '@react_bp.route("/portfolio-backtester")' in SOURCE
