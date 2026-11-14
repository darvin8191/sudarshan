"""G5: /test_chart is gone.

It was upstream's test page for a chart library (blueprints/pnltracker.py): no session check, and a
template that is in neither this repository nor upstream's, so it answered every caller, the owner
included, with the error page. Nothing links to it (the chart test page is the React /chart/test).
The G5 route table no longer names it, so a rebase that brings it back fails the suite with
"classify this route".
"""

from __future__ import annotations

from flask import Flask

import blueprints.pnltracker as pnltracker


def test_the_pnl_tracker_registers_no_test_chart_page():
    """Before G5 the rule was there and its view raised TemplateNotFound for every caller."""
    app = Flask("sudarshan-g5-test-chart-tests")
    app.config["TESTING"] = True
    app.register_blueprint(pnltracker.pnltracker_bp)
    assert "/test_chart" not in {rule.rule for rule in app.url_map.iter_rules()}
    assert not hasattr(pnltracker, "test_chart")
    assert app.test_client().get("/test_chart").status_code == 404
