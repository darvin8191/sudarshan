"""OpenAlgo's REST API routes for a test, loaded one module at a time (the G5 tests).

restx_api/__init__.py imports every namespace: some open databases or start threads at import, and
restx_api/portfolio.py imports the repository's ``portfolio`` package, which a pytest run that has
collected a test under a sudarshan/ package resolves to sudarshan/portfolio instead (pytest puts
sudarshan/ first on the import path then). So each module is loaded from its own file under a
stand-in ``restx_api`` parent that is removed again, the pattern of
sudarshan/alerts/tests/test_alerts_notify_file.py, and its namespace is put on a fresh Flask-RESTX
Api under /api/v1, where app.py mounts OpenAlgo's.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

from flask import Blueprint, Flask
from flask_restx import Api

from limiter import limiter

ROOT = Path(__file__).resolve().parents[2]


def _restx_modules() -> dict[str, types.ModuleType]:
    return {name: module for name, module in sys.modules.items()
            if name == "restx_api" or name.startswith("restx_api.")}  # fmt: skip


def load(name: str) -> types.ModuleType:
    """restx_api/<name>.py as a module of its own. sys.modules is left as it was found."""
    before = _restx_modules()
    parent = types.ModuleType("restx_api")
    parent.__path__ = [str(ROOT / "restx_api")]
    sys.modules["restx_api"] = parent
    try:
        spec = importlib.util.spec_from_file_location(
            f"restx_api.{name}", ROOT / "restx_api" / f"{name}.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for stale in set(_restx_modules()) - set(before):
            del sys.modules[stale]
        sys.modules.update(before)


def api_app(*mounts: tuple[types.ModuleType, str]) -> Flask:
    """A bare app with each (module, path) namespace under /api/v1, the limiter off."""
    blueprint = Blueprint("sudarshan_g5_api", __name__, url_prefix="/api/v1")
    api = Api(blueprint, doc=False)
    for module, path in mounts:
        api.add_namespace(module.api, path=path)
    app = Flask("sudarshan-g5-api-tests")
    app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    limiter.init_app(app)
    app.register_blueprint(blueprint)
    return app


def stop_the_health_monitor() -> None:
    """The market data service starts its health monitor thread when it is imported; a test that
    imported it stops the monitor once done (the SIO1 tests do the same)."""
    module = sys.modules.get("services.market_data_service")
    if module is not None:
        module.get_market_data_service().health_monitor.stop()
