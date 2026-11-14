"""G5: every route OpenAlgo registers refuses a family viewer (P6 7.1; cross-design Y7, Y15).

A family phone never gets an OpenAlgo session (P6 0.1, 2.2). It sends OpenAlgo only its own
cookie, ``__Host-sudarshan_family``, which OpenAlgo does not read, so every route has to answer it
as it answers a stranger. The rules file asks for an automated test that every write namespace,
admin route and bot command refuses a viewer session; this is that test for OpenAlgo's routes and
its Socket.IO (the family bot's commands are T2's, P6 8).

g5_probe.py walks the real app in an interpreter of its own (its docstring says why, and what runs
and what does not); this module reads its report against route_classes.py:

- every rule the app registers is classified, and every line of the table names a rule it has;
- each rule answers every caller as its class allows (route_classes.py says what each class
  allows), and nothing reaches the network, starts a process, raises or hands out a session;
- no caller writes a row to the owner's order log or analyzer log, and an order event a caller
  causes is one raised while refusing a request for its key, which the bus hands to no subscriber;
- only protected, webhook, login-flow and internal rules take a write;
- Socket.IO, which the url_map does not hold, refuses the anonymous caller and the viewer on every
  namespace, and an order event reaches the owner's socket and neither of theirs (Y7);
- nothing in the family service's own modules imports the code that trades.

Runs in the backend CI shards with the other ``sudarshan/**/tests`` files (ci_shards.py); it needs
no eventlet and no gunicorn, only the repository's own dependencies.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sudarshan.family.tests import route_classes as classes

HERE = Path(__file__).resolve().parent
FAMILY = HERE.parent
REPO = HERE.parents[2]
PROBE = "sudarshan.family.tests.g5_probe"
PROBE_TIMEOUT_S = 280
WRITES = ("POST", "PUT", "PATCH", "DELETE")
LOGIN_PAGES = {"auth.login", "react.react_login"}
NEVER = {"connect", "lookup", "process", "raised"}
DB_URLS = ("DATABASE_URL", "SANDBOX_DATABASE_URL", "LOGS_DATABASE_URL", "LATENCY_DATABASE_URL",
           "HEALTH_DATABASE_URL")  # fmt: skip
#: What the family service may never import: the books, OpenAlgo's services and broker code, and
#: its REST API (P6 7.1). The family package's tests are not the service: the walk builds OpenAlgo.
FORBIDDEN = ("sudarshan.books", "services", "broker", "restx_api")

pytestmark = pytest.mark.timeout(PROBE_TIMEOUT_S + 20)


class NotClean(Exception):
    """A route in FINDINGS still refuses the way it is listed: an expected failure."""


class G5Finding(UserWarning):
    """Shown in the warnings summary for each finding still open."""


def probe_environment(here: Path) -> dict[str, str]:
    """A fresh environment for the walk: nothing from this process's settings or another test's
    stand-ins, every database and folder in ``here`` (the probe refuses to run otherwise), and the
    settings the owner's server runs with where they change what a route answers."""
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SYSTEMROOT")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    env.update({name: f"sqlite:///{here}/{name.lower()}.db" for name in DB_URLS})
    env.update({
        "PYTHONPATH": str(REPO),
        "API_KEY_PEPPER": "0" * 64,  # fake, test only
        "APP_KEY": "g5-test-only-app-key-" + "7" * 32,  # fake, test only
        "HISTORIFY_DATABASE_PATH": f"{here}/historify.duckdb",
        "LOG_DIR": f"{here}/log",
        "MCP_OAUTH_KEYS_DIR": f"{here}/keys",
        "NUMBA_CACHE_DIR": f"{here}/numba",
        "LOG_LEVEL": "WARNING",
        # HTTPS, as on the server: the session cookie is __Secure-, and CSRF checks the referrer.
        "HOST_SERVER": "https://g5.invalid",
        "CSRF_ENABLED": "TRUE",
        "FLASK_DEBUG": "False",
        # The address in the requests is never trusted, so no answer can come from an IP ban.
        "TRUST_PROXY_HEADERS": "FALSE",
        # Off by default, switched on here so its routes are classified and walked too.
        "MCP_HTTP_ENABLED": "True",
        "MCP_PUBLIC_URL": "https://g5.invalid",
        "VALID_BROKERS": "upstox",
        "REDIRECT_URL": "https://g5.invalid/upstox/callback",
        "BROKER_API_KEY": "g5-fake-key",  # fake, test only
        "BROKER_API_SECRET": "g5-fake-secret",  # fake, test only
        # The owner's Telegram bot is set up, so Telegram's webhook checks its secret header.
        "TELEGRAM_WEBHOOK_SECRET": "g5-fake-webhook-secret",  # fake, test only
        # Twelve hours away, so no session the walk makes crosses the daily expiry mid-walk.
        "SESSION_EXPIRY_TIME": (ist + timedelta(hours=12)).strftime("%H:%M"),
        "DISABLE_SESSION_EXPIRY": "FALSE",
    })  # fmt: skip
    return env


@pytest.fixture(scope="module")
def report(tmp_path_factory) -> dict:
    """The walk's report: run once for the module, in a folder of its own."""
    here = tmp_path_factory.mktemp("g5-walk").resolve()
    out, log = here / "report.json", here / "walk.log"
    with log.open("w", encoding="utf-8") as sink:
        try:
            done = subprocess.run(
                [sys.executable, "-m", PROBE, str(out)],
                cwd=here,
                env=probe_environment(here),
                stdout=sink,
                stderr=subprocess.STDOUT,
                timeout=PROBE_TIMEOUT_S,
                check=False,
            )
            code = done.returncode
        except subprocess.TimeoutExpired:
            code = f"none: stopped after {PROBE_TIMEOUT_S} s"
    if code != 0 or not out.exists():
        tail = log.read_text(encoding="utf-8", errors="replace")[-6000:]
        pytest.fail(f"the G5 walk did not finish (exit {code}); its log ends:\n{tail}")
    return json.loads(out.read_text(encoding="utf-8"))


class Walk:
    """The report, indexed for the per-rule checks."""

    def __init__(self, report: dict) -> None:
        self.methods = {(r["rule"], r["endpoint"]): r["methods"] for r in report["rules"]}
        self.records: dict[tuple, list[dict]] = defaultdict(list)
        for record in report["requests"]:
            self.records[(record["rule"], record["endpoint"], record["method"])].append(record)
        index = [r for r in report["requests"] if r["rule"] == "/" and r["caller"] == "anonymous"]
        self.shell = index[0]["sha256"] if index and index[0]["status"] == 200 else None
        self.shell_views = {endpoint for (rule, endpoint), cls in classes.ROUTES.items()
                            if cls == classes.PUBLIC_SHELL}  # fmt: skip

    def answers(self, rule: str, endpoint: str, method: str) -> list[dict]:
        """The records of requests this rule's own view answered: on the app, or, where a React
        shell route takes the same path and method, on the app built without the shell."""
        records = self.records.get((rule, endpoint, method), [])
        own = [r for r in records if r["app"] == "app" and r["reached"] == endpoint]
        return own or [r for r in records if r["reached"] == endpoint]


@pytest.fixture(scope="module")
def walk(report) -> Walk:
    return Walk(report)


def callers_for(rule: str) -> tuple[str, ...]:
    if rule.startswith("/sudarshan/") and rule not in classes.NOT_OWNER_ONLY:
        return ("anonymous", "viewer", "local", "forged")
    return ("anonymous", "viewer", "local")


def verdict(cls: str, record: dict, walk: Walk) -> str | None:
    """Why ``record`` is not an answer ``cls`` allows; None when it is."""
    if record["status"] is None:
        return record.get("error") or "no answer"
    for entry in record["ledger"]:
        if entry["kind"] in NEVER:
            return f"{entry['kind']}: {entry['what']}"
        if entry["kind"] == "event" and not entry["what"].endswith("FailedEvent"):
            return f"published {entry['what']}"
    handed = sorted(set(record.get("session") or ()) & {"logged_in", "user"})
    if handed and record["caller"] != "forged":
        return f"handed out a session carrying {handed}"
    status = record["status"]
    if cls in (classes.PROTECTED, classes.WEBHOOK, classes.INTERNAL) and record["success"]:
        return f'answered {status} with "status": "success"'
    if cls == classes.PROTECTED:
        clean = (
            status in (401, 403)
            or (status == 302 and record["location_endpoint"] in LOGIN_PAGES)
            or (status == 400 and record["method"] in WRITES and "CSRF" in record["body"])
        )
    elif cls == classes.WEBHOOK:
        clean = status in (400, 401, 403, 404)
    elif cls == classes.INTERNAL:
        clean = status in (401, 403, 404)
    elif cls == classes.PUBLIC_SHELL:
        clean = (status in (200, 404) and record["sha256"] == walk.shell) or (
            status == 302 and record["location_endpoint"] in walk.shell_views
        )
    elif cls == classes.PUBLIC_STATIC:
        clean = status in (200, 304, 404)
    else:
        clean = status < 500
    return None if clean else f"answered {status}"


def listed(finding: classes.Finding, record: dict) -> bool:
    """Whether ``record`` refuses the way its FINDINGS line says."""
    raised = [entry["what"] for entry in record["ledger"] if entry["kind"] == "raised"]
    seen = " ".join([record.get("body") or "", record.get("location") or "", *raised])
    return record["status"] == finding.status and finding.says in seen


def describe(record: dict, why: str) -> str:
    body = (record.get("body") or "")[:160]
    return f"  {record['method']} {record['path']} as {record['caller']}: {why}; {body!r}"


def entries():
    for (rule, endpoint), cls in sorted(classes.ROUTES.items()):
        open_findings = {key[2]: finding for key, finding in classes.FINDINGS.items()
                         if key[:2] == (rule, endpoint)}  # fmt: skip
        marks = []
        if open_findings:
            why = "; ".join(f"{method}: {f.why}" for method, f in sorted(open_findings.items()))
            fixed = "if this passes, the route is fixed: remove its FINDINGS line"
            marks.append(pytest.mark.xfail(
                raises=NotClean, strict=True, reason=f"G5 finding ({fixed}): {why}",
            ))  # fmt: skip
        yield pytest.param(rule, endpoint, cls, id=f"{cls} {rule} {endpoint}", marks=marks)


def test_every_rule_the_app_registers_is_classified(report):
    """Mutation target 23: a rule the table does not know fails, with the line to add."""
    assert report["rules"], "the walk found no rule at all"
    unknown = [r for r in report["rules"] if (r["rule"], r["endpoint"]) not in classes.ROUTES]
    lines = [f"classify this route: {r['rule']} ({r['endpoint']}, "
             f"{', '.join(r['methods']) or 'OPTIONS'})" for r in unknown]  # fmt: skip
    lines.append("Add a line for each to sudarshan/family/tests/route_classes.py, protected unless "
                 "it must be public, and say why if it is (a React page whose view only calls "
                 "serve_react_app() is public_shell):")  # fmt: skip
    lines += [f"  {classes.line_for(r['rule'], r['endpoint'])}" for r in unknown]
    assert not unknown, "\n".join(lines)


@pytest.mark.parametrize(("rule", "endpoint", "cls"), list(entries()))
def test_the_route_answers_every_caller_as_its_class_allows(walk, rule, endpoint, cls):
    line = classes.line_for(rule, endpoint, cls)
    assert (rule, endpoint) in walk.methods, (
        f"this line names no rule the app registers; remove it:\n  {line}"
    )
    problems, seen = [], []
    for method in walk.methods[(rule, endpoint)]:
        records = {r["caller"]: r for r in walk.answers(rule, endpoint, method)}
        for caller in callers_for(rule):
            record = records.get(caller)
            if record is None:
                problems.append(f"  {method} as {caller}: no request reached this rule's view")
                continue
            why = verdict(cls, record, walk)
            if why is None:
                continue
            finding = classes.FINDINGS.get((rule, endpoint, method))
            if finding is not None and listed(finding, record):
                seen.append(describe(record, why))
            else:
                problems.append(describe(record, why))
    assert not problems, "\n".join([f"{rule} ({endpoint}) is {cls}, but:", *problems])
    if seen:
        warnings.warn(
            G5Finding(f"{rule} ({endpoint}) refuses, not cleanly:\n" + "\n".join(seen)),
            stacklevel=1,
        )
        raise NotClean("\n".join(seen))


def test_the_findings_and_exemptions_name_rules_the_table_has(walk):
    stale = [(rule, endpoint, method) for rule, endpoint, method in classes.FINDINGS
             if method not in walk.methods.get((rule, endpoint), ())
             or (rule, endpoint) not in classes.ROUTES]  # fmt: skip
    assert not stale, f"FINDINGS lines naming no rule and method the app has: {stale}"
    rules = {rule for rule, _ in classes.ROUTES}
    loose = [rule for rule in classes.NOT_OWNER_ONLY
             if rule not in rules or not rule.startswith("/sudarshan/")]  # fmt: skip
    assert not loose, (
        f"NOT_OWNER_ONLY names rules that are not /sudarshan/* rules of the app: {loose}"
    )


def test_only_protected_webhook_login_and_internal_rules_take_a_write(report):
    wrong = []
    for r in report["rules"]:
        writes = [method for method in r["methods"] if method in WRITES]
        cls = classes.ROUTES.get((r["rule"], r["endpoint"]))
        if writes and cls is not None and cls not in classes.WRITABLE:
            wrong.append(f"  {r['rule']} ({r['endpoint']}) takes {', '.join(writes)} but is {cls}")
    assert not wrong, "public rules that take a write:\n" + "\n".join(wrong)


def test_nothing_reached_the_network_started_a_process_or_succeeded(report):
    """The whole walk, start-up and Socket.IO included: refused and recorded by the probe."""
    bad = [entry for entry in report["ledger"] if entry["kind"] in NEVER - {"raised"}
           or (entry["kind"] == "event" and not entry["what"].endswith("FailedEvent"))]  # fmt: skip
    assert not bad, "\n".join(f"  {e['kind']} {e['what']} during {e['during']}" for e in bad)


def test_no_caller_writes_a_row_to_the_owner_s_logs(report):
    """Observation 4: OpenAlgo checked a request's fields before its key and logged a request its
    fields refused, so a keyless body to /api/v1/orderstatus, /openposition, /margin or /analyzer
    added a row to the owner's order log (15 in the walk of 14 Nov 2026)."""
    assert report["log_rows"] == {"order_logs": 0, "analyzer_logs": 0}


def test_an_order_event_a_caller_causes_is_refused_for_its_key(report):
    """Observation 4: the order events a keyless body caused (cancel, modify, close, basket,
    split, smart and GTT orders) reached the order log and, in analyze mode, the owner's screen.
    Raised while refusing a request for its key, an event reaches no subscriber (utils/event_bus.py);
    an event a caller who is not the owner causes that is not so marked would reach them."""
    heard = [f"  {r['method']} {r['path']} as {r['caller']}: {e['what']}"
             for r in report["requests"] for e in r["ledger"]
             if e["kind"] == "event" and not e.get("key_refused")]  # fmt: skip
    assert not heard, "order events a subscriber would hear:\n" + "\n".join(heard)


def test_no_answer_came_from_an_ip_ban(report):
    """A banned address answers 403 to everything, which would pass every check above."""
    assert report["banned_addresses"] == 0
    assert not [r for r in report["requests"] if "has been banned" in (r.get("body") or "")]


def test_the_shell_is_what_the_root_answers(walk):
    assert walk.shell is not None, "/ did not answer the anonymous caller with the shell"
    assert classes.ROUTES[("/", "react.react_index")] == classes.PUBLIC_SHELL


def test_socketio_refuses_the_anonymous_caller_and_the_viewer_who_hear_no_order(report):
    """Y7: Socket.IO is not in the url_map. Every namespace refuses both (SIO1, v0.5.1), and the
    order event the owner's socket hears (proving it went out) reaches neither."""
    socketio = report["socketio"]
    assert "/" in socketio["namespaces"], socketio["namespaces"]
    sockets = {(s["namespace"], s["caller"]): s for s in socketio["sockets"]}
    owner = sockets[("/", "owner")]
    assert owner["connected"] and "order_event" in owner["after_order"], (
        f"the owner's socket did not hear the order event, so the refusals prove nothing: {owner}"
    )
    for namespace in socketio["namespaces"]:
        assert sockets[(namespace, "owner")]["connected"], f"{namespace} refused the owner too"
        for caller in ("anonymous", "viewer"):
            seen = sockets[(namespace, caller)]
            assert not seen["connected"], f"{namespace} admitted the {caller} caller: {seen}"
            assert seen["on_connect"] == [] and seen["after_order"] == [], (
                f"the {caller} caller heard something on {namespace}: {seen}"
            )


def imported_names(source: str, package: str) -> list[str]:
    """Every module a source imports, relative imports resolved against ``package``, and every
    module named to ``importlib.import_module`` or ``__import__`` as a literal."""
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                stem = ".".join(parts[: len(parts) - node.level + 1])
                base = f"{stem}.{base}" if base else stem
            names.append(base)
            names += [f"{base}.{alias.name}" for alias in node.names]
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            function = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            if function in ("import_module", "__import__") and isinstance(node.args[0].value, str):
                names.append(node.args[0].value)
    return names


def forbidden(name: str) -> bool:
    return any(name == root or name.startswith(f"{root}.") for root in FORBIDDEN)


def test_the_import_check_finds_what_it_looks_for():
    """So the check below cannot pass for want of looking."""
    source = (
        "import services.order_router\n"
        "from broker.upstox.api import order_api\n"
        "from .. import books\n"
        "from ..books import exit_guard\n"
        "importlib.import_module('restx_api.place_order')\n"
        "import sudarshan.vault\n"
    )
    names = imported_names(source, "sudarshan.family")
    assert {name for name in names if forbidden(name)} == {
        "services.order_router", "broker.upstox.api", "broker.upstox.api.order_api",
        "sudarshan.books", "sudarshan.books.exit_guard", "restx_api.place_order",
    }  # fmt: skip
    assert "sudarshan.vault" in names and not forbidden("sudarshan.vault")


def test_no_family_module_imports_the_code_that_trades():
    """No module of the family service imports sudarshan.books, services, broker or restx_api."""
    scanned, offenders = [], []
    for path in sorted(FAMILY.rglob("*.py")):
        relative = path.relative_to(FAMILY)
        if relative.parts[0] == "tests":
            continue
        module = ".".join(("sudarshan", "family", *relative.with_suffix("").parts))
        package = (
            module.removesuffix(".__init__")
            if path.name == "__init__.py"
            else module.rpartition(".")[0]
        )
        scanned.append(module)
        names = imported_names(path.read_text(encoding="utf-8"), package)
        offenders += [f"  {relative}: {name}" for name in names if forbidden(name)]
    assert "sudarshan.family.check" in scanned, scanned
    assert not offenders, "the family service imports code that trades:\n" + "\n".join(offenders)
