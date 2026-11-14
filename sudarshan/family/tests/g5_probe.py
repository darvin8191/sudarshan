"""What every route OpenAlgo registers answers to a family viewer: the G5 walk (P6 7.1).

test_g5_routes.py runs this module in an interpreter of its own, in an empty temporary folder:

    python -m sudarshan.family.tests.g5_probe <report.json>

It builds OpenAlgo's real app from app.py's own ``create_app``, walks every rule of its url_map
with every method the rule declares (OPTIONS and HEAD aside) as each caller below, then connects to
every Socket.IO namespace as the same callers and emits an order event. It writes what it saw and
judges nothing: the test reads the report against route_classes.py.

The callers, each request a fresh one (no cookie carried from an answer to the next request):

- ``anonymous``: no cookie;
- ``viewer``: only the family cookie ``__Host-sudarshan_family``, with a random value (what a
  family phone sends OpenAlgo, which never gives it a session: P6 0.1, 2.2);
- ``forged``: on ``/sudarshan/*`` rules only, a session signed with the app's key, logged in as a
  user who is not the owner (cross-design Y15: upstream's own check reads only ``logged_in`` and
  ``login_time``, so only an owner check refuses this one);
- ``local``: no cookie and none of nginx's headers: a process on the server itself, such as the
  family service, which takes input from people who are not the owner (P6 2.1).

The first three come through nginx, so they carry the headers nginx adds. A CSRF token is no
credential, since any visitor gets one from ``/auth/csrf-token``: so every write carries a valid
one with the session cookie it belongs to, and what answers is the route's own check, not
Flask-WTF's.

Why an interpreter of its own: app.py cannot be imported in a test (it reads and may rewrite the
operator's .env, starts the schedulers and binds the WebSocket proxy's ports), and building the
app imports nearly every module of the repository, which starts threads (APScheduler, the market
data health monitor) that would outlive the test in the shared CI interpreter. Here they end with
the walk, and no other test's settings or stand-ins can reach it.

What runs:
- app.py's imports and ``create_app`` (and its session teardown), from its own source, and nothing
  else of its module body: every blueprint and route it registers, in its order, with its hooks,
  error handlers, CSRF exemptions and middleware. The React shell is registered even without a
  built frontend, and the remote MCP routes, off by default, are switched on, so both are walked.
  A rule that a React shell route hides (the same path and method, registered first) is walked
  again on a second app built without the shell, where it answers itself.
- Stubbed: the threads ``create_app`` starts (broker keep-warm, order-update adapters, the health
  collector, the analytics warm-up). The rate limiter is off: a limit is not an authorisation, and
  a 429 would hide what the route answers. Events published on the event bus are recorded, with
  whether each was raised while refusing a request for its API key, and not delivered. Writes
  handed to the order-log executor run; once the walk is done they are let finish, and the rows of
  the owner's order log and analyzer log are counted.
- Every database is a file in the temporary folder (the test sets the URLs; this refuses to run
  otherwise), with the tables setup_environment creates, from its own list, and an owner account.
- Nothing reaches the network or starts a process: every connect, name lookup and subprocess is
  refused and recorded with the request that tried it.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
import types
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
APP_PY = REPO / "app.py"
HOST = "g5.invalid"
BASE_URL = f"https://{HOST}"
FAMILY_COOKIE = "__Host-sudarshan_family"
OWNER = "g5-owner"
NOT_THE_OWNER = "not-the-owner"
#: What nginx adds to every request it forwards (P6 2.3); the address is a documentation one.
FORWARDED = {"X-Forwarded-For": "198.51.100.7", "X-Real-IP": "198.51.100.7",
             "X-Forwarded-Proto": "https"}  # fmt: skip
#: How a browser's fetch from the app's own page looks (Referer: Flask-WTF checks it on HTTPS).
BROWSER = {"Accept": "application/json", "Referer": f"{BASE_URL}/", "Origin": BASE_URL}
WRITES = ("POST", "PUT", "PATCH", "DELETE")
CALLERS = ("anonymous", "viewer", "forged", "local")
REQUEST_TIMEOUT_S = 10
#: Each request is cut off after REQUEST_TIMEOUT_S where the platform has an interval timer.
ALARM = hasattr(signal, "setitimer")
BODY_LIMIT = 1 << 20
SNIPPET = 300
#: A sample for each URL converter, so every rule can be built into a path.
SAMPLES = {"int": "1", "float": "1.5", "path": "g5/probe", "string": "g5probe",
           "default": "g5probe", "uuid": "00000000-0000-4000-8000-000000000005"}  # fmt: skip
#: Samples by argument name, where the generic one would not reach the view (VALID_BROKERS).
NAMED_SAMPLES = {"broker": "upstox"}
ARGUMENT = re.compile(
    r"<(?:(?P<converter>[A-Za-z_]\w*)(?:\((?P<args>[^)]*)\))?:)?(?P<name>[A-Za-z_]\w*)>"
)
SUCCESS = re.compile(r"""["']status["']\s*:\s*["']success["']""")
DB_URLS = ("DATABASE_URL", "SANDBOX_DATABASE_URL", "LOGS_DATABASE_URL", "LATENCY_DATABASE_URL",
           "HEALTH_DATABASE_URL")  # fmt: skip
DIRS = ("HISTORIFY_DATABASE_PATH", "LOG_DIR", "MCP_OAUTH_KEYS_DIR")


class ProbeTimeout(BaseException):
    """A request that did not answer in time. A BaseException, so Flask does not turn it into a
    500 answer on the way out."""


class Ledger:
    """Every connection, name lookup, process and event-bus publication tried, and every exception
    a view raised, with the request it happened in."""

    def __init__(self) -> None:
        self.during = "start-up"
        self.entries: list[dict] = []

    def note(self, kind: str, what: str, **more) -> None:
        self.entries.append({"kind": kind, "what": what[:SNIPPET], "during": self.during, **more})

    def since(self, mark: int) -> list[dict]:
        return self.entries[mark:]


LEDGER = Ledger()


def refuse_unless_isolated(here: Path) -> None:
    """Every database and folder this run writes must be inside its own temporary folder."""
    for name in DB_URLS:
        if not os.environ.get(name, "").startswith(f"sqlite:///{here}/"):
            raise SystemExit(f"{name} does not point into {here}: refusing to run")
    for name in DIRS:
        if not os.environ.get(name, "").startswith(f"{here}/"):
            raise SystemExit(f"{name} does not point into {here}: refusing to run")


def guard_the_machine() -> None:
    """No connection, name lookup or process from here on: each is recorded and refused."""

    def no_connect(self, address, *args, **kwargs):
        LEDGER.note("connect", repr(address))
        raise OSError("the G5 walk makes no connection")

    def no_lookup(host, *args, **kwargs):
        LEDGER.note("lookup", repr(host))
        raise socket.gaierror(socket.EAI_NONAME, "the G5 walk makes no name lookup")

    def no_process(self, args, *more, **kwargs):
        LEDGER.note("process", repr(args))
        raise OSError("the G5 walk starts no process")

    socket.socket.connect = no_connect
    socket.socket.connect_ex = no_connect
    socket.getaddrinfo = no_lookup
    subprocess.Popen.__init__ = no_process


def load_app_module() -> types.ModuleType:
    """app.py's imports, ``create_app`` and its session teardown, from its own source."""
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"), filename=str(APP_PY))
    kept = []
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            kept.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in (
            "create_app",
            "shutdown_database_sessions",
        ):
            node.decorator_list = []  # the teardown is registered on each app below
            kept.append(node)
        elif isinstance(node, ast.Assign) and _targets(node) == ["logger"]:
            kept.append(node)
    module = types.ModuleType("g5_openalgo_app")
    module.__file__ = str(APP_PY)
    sys.modules[module.__name__] = module  # Flask finds the app's root folder through it
    exec(compile(ast.Module(body=kept, type_ignores=[]), str(APP_PY), "exec"), module.__dict__)
    for name in ("create_app", "shutdown_database_sessions"):
        if not callable(getattr(module, name, None)):
            raise SystemExit(f"app.py no longer defines {name}: the walk cannot build the app")
    return module


def _targets(node: ast.Assign) -> list:
    return [getattr(target, "id", None) for target in node.targets]


def stub_start_up(module: types.ModuleType) -> None:
    """The threads ``create_app`` starts (none of them answers a request), and the event bus."""
    import portfolio
    import services.order_update_service as order_updates
    import utils.health_monitor as health
    from utils.event_bus import bus

    module.start_broker_keepalive = lambda: None
    portfolio.warm_analytics = lambda: None
    order_updates.start_order_update_adapters_on_boot = lambda db_ready=None: None
    health.start_health_collector = lambda *args, **kwargs: None
    bus.publish = lambda event: LEDGER.note(
        "event", type(event).__name__, key_refused=bool(getattr(event, "key_refused", False))
    )


def build_app(module: types.ModuleType, *, react: bool):
    from flask import got_request_exception

    from limiter import limiter

    module.is_react_frontend_available = lambda: react
    app = module.create_app()
    app.teardown_appcontext(module.shutdown_database_sessions)
    limiter.enabled = False

    def raised(sender, exception, **extra):
        LEDGER.note("raised", f"{type(exception).__name__}: {exception}")

    got_request_exception.connect(raised, app, weak=False)
    return app


def create_tables(module: types.ModuleType, app) -> None:
    """The tables setup_environment creates, from its own list in app.py."""
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"), filename=str(APP_PY))
    nested = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
              and node.name == "_init_databases_and_schedulers"]  # fmt: skip
    if len(nested) != 1:
        raise SystemExit("app.py's setup_environment changed: no _init_databases_and_schedulers")
    imports = [node for node in ast.walk(nested[0]) if isinstance(node, ast.ImportFrom)
               and (node.module or "").startswith("database.")]  # fmt: skip
    listing = [node for node in ast.walk(nested[0]) if isinstance(node, ast.Assign)
               and _targets(node) == ["db_init_functions"]]  # fmt: skip
    if len(listing) != 1:
        raise SystemExit("app.py's setup_environment changed: no db_init_functions list")
    namespace = dict(module.__dict__)
    exec(compile(ast.Module(body=imports, type_ignores=[]), str(APP_PY), "exec"), namespace)
    functions = eval(compile(ast.Expression(listing[0].value), str(APP_PY), "eval"), namespace)
    with app.app_context():
        for _name, function in functions:
            function()
        if callable(namespace.get("init_strategy_book_db")):  # set up beside the list
            namespace["init_strategy_book_db"]()


def add_owner() -> None:
    from database.user_db import add_user, db_session

    add_user(OWNER, "owner@example.invalid", secrets.token_urlsafe(16) + "-Aa1!", is_admin=True)
    db_session.remove()


def ist_now() -> str:
    import pytz

    return datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()


def session_cookie(app, values: dict | None) -> tuple[str, str]:
    """A signed session cookie holding ``values`` and a CSRF token, and the token, as a browser
    gets them: the session from a login (forged here with the app's key), the token from
    ``/auth/csrf-token``."""
    client = app.test_client()
    if values:
        with client.session_transaction(base_url=BASE_URL) as session:
            session.update(values)
    answer = client.get("/auth/csrf-token", base_url=BASE_URL, headers=BROWSER)
    token = answer.get_json()["csrf_token"]
    name = app.config["SESSION_COOKIE_NAME"]
    return f"{name}={client.get_cookie(name, domain=HOST).value}", token


def callers(app) -> dict[str, dict]:
    """Each caller's cookies for a read, and for a write with the CSRF token beside them."""
    family = f"{FAMILY_COOKIE}={secrets.token_urlsafe(32)}"
    anonymous, anonymous_token = session_cookie(app, None)
    viewer, viewer_token = session_cookie(app, None)
    forged_values = {"logged_in": True, "login_time": ist_now(), "user": NOT_THE_OWNER}
    forged, forged_token = session_cookie(app, forged_values)
    local, local_token = session_cookie(app, None)
    return {
        "anonymous": {"read": None, "write": anonymous, "token": anonymous_token,
                      "forwarded": True},
        "viewer": {"read": family, "write": f"{family}; {viewer}", "token": viewer_token,
                   "forwarded": True},
        "forged": {"read": forged, "write": forged, "token": forged_token, "forwarded": True},
        "local": {"read": None, "write": local, "token": local_token, "forwarded": False},
    }  # fmt: skip


def sample_path(rule) -> str:
    def value(match: re.Match) -> str:
        if match["name"] in NAMED_SAMPLES:
            return NAMED_SAMPLES[match["name"]]
        converter = match["converter"] or "default"
        if converter == "any":
            return match["args"].split(",")[0].strip().strip("'\"")
        return SAMPLES.get(converter, SAMPLES["default"])

    return ARGUMENT.sub(value, rule.rule)


def endpoint_for(adapter, path: str, method: str) -> str:
    """The endpoint that answers ``method path``: another rule's, when one registered earlier
    takes the same path and method."""
    from werkzeug.exceptions import HTTPException
    from werkzeug.routing import RequestRedirect

    try:
        endpoint, _ = adapter.match(path, method=method)
        return endpoint
    except RequestRedirect as redirect:
        return f"redirect:{redirect.new_url}"
    except HTTPException as refused:
        return f"no-match:{refused.code}"


def session_in(app, answer) -> list[str] | None:
    """The keys of a session cookie the answer sets (decoded with the app's key), or None."""
    name = app.config["SESSION_COOKIE_NAME"]
    serializer = app.session_interface.get_signing_serializer(app)
    for header in answer.headers.getlist("Set-Cookie"):
        cookie, _, _ = header.partition(";")
        key, _, value = cookie.partition("=")
        if key.strip() == name:
            if not value:
                return []
            try:
                return sorted(serializer.loads(value))
            except Exception:  # noqa: BLE001 -- an unreadable cookie is recorded as such
                return ["<unreadable>"]
    return None


def _alarm(signum, frame):
    raise ProbeTimeout


def ask(app, client, adapter, caller: dict, method: str, path: str) -> dict:
    """One request as ``caller``; what came back."""
    headers = dict(BROWSER)
    if caller["forwarded"]:
        headers.update(FORWARDED)
    cookie = caller["write"] if method in WRITES else caller["read"]
    if cookie:
        headers["Cookie"] = cookie
    kwargs = {}
    if method in WRITES:
        headers["X-CSRFToken"] = caller["token"]
        kwargs["json"] = {}
    answer = None
    if ALARM:
        signal.setitimer(signal.ITIMER_REAL, REQUEST_TIMEOUT_S)
    try:
        answer = client.open(path, method=method, headers=headers, base_url=BASE_URL, **kwargs)
        body = b""
        for chunk in answer.iter_encoded():
            body += chunk
            if len(body) > BODY_LIMIT:
                break
        text = body.decode("utf-8", "replace")
        location = answer.headers.get("Location")
        return {
            "status": answer.status_code,
            "location": location,
            "location_endpoint": endpoint_for(adapter, urlsplit(location).path, "GET")
            if location else None,
            "type": answer.mimetype,
            "success": bool(SUCCESS.search(text)),
            "sha256": hashlib.sha256(body).hexdigest(),
            "body": text[:SNIPPET],
            "session": session_in(app, answer),
        }  # fmt: skip
    except ProbeTimeout:
        return {"status": None, "error": f"no answer within {REQUEST_TIMEOUT_S} s"}
    except Exception as exc:  # noqa: BLE001 -- recorded for the test to report
        return {"status": None, "error": f"{type(exc).__name__}: {exc}"[:SNIPPET]}
    finally:
        if ALARM:
            signal.setitimer(signal.ITIMER_REAL, 0)
        if answer is not None:
            answer.close()


def walk(app, who: dict[str, dict], label: str, only: set | None = None) -> list[dict]:
    """Every (rule, method) of ``app``, or only those in ``only``, as every caller it is for."""
    client = app.test_client(use_cookies=False)
    adapter = app.url_map.bind(HOST, url_scheme="https")
    records = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, r.endpoint)):
        path = sample_path(rule)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            if only is not None and (rule.rule, rule.endpoint, method) not in only:
                continue
            reached = endpoint_for(adapter, path, method)
            for name in CALLERS:
                if name == "forged" and not rule.rule.startswith("/sudarshan/"):
                    continue
                LEDGER.during = f"{method} {path} as {name} ({label})"
                mark = len(LEDGER.entries)
                answer = ask(app, client, adapter, who[name], method, path)
                records.append({"rule": rule.rule, "endpoint": rule.endpoint, "method": method,
                                "caller": name, "app": label, "path": path, "reached": reached,
                                **answer, "ledger": LEDGER.since(mark)})  # fmt: skip
    LEDGER.during = "between requests"
    return records


def rules_of(app) -> list[dict]:
    rules = sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, r.endpoint))
    return [{"rule": rule.rule, "endpoint": rule.endpoint,
             "methods": sorted(rule.methods - {"HEAD", "OPTIONS"})} for rule in rules]  # fmt: skip


def socket_check(app, who: dict[str, dict]) -> dict:
    """Every Socket.IO namespace, as the anonymous caller, the viewer and (to prove the event went
    out) the owner; then an order event, as the order service's subscriber emits it (Y7)."""
    from events.order_events import OrderPlacedEvent
    from extensions import socketio
    from subscribers import socketio_subscriber

    owner, _ = session_cookie(app, {"logged_in": True, "login_time": ist_now(), "user": OWNER})
    cookies = {"anonymous": None, "viewer": who["viewer"]["read"], "owner": owner}
    namespaces = sorted(set(socketio.server.handlers) | set(socketio.server.namespace_handlers))
    sockets = []
    for namespace in namespaces:
        for name, cookie in cookies.items():
            headers = dict(FORWARDED, Cookie=cookie) if cookie else dict(FORWARDED)
            LEDGER.during = f"socket.io {namespace} as {name}"
            client = socketio.test_client(app, namespace=namespace, headers=headers)
            sockets.append((namespace, name, client, client.is_connected(namespace),
                            [message["name"] for message in client.queue]))  # fmt: skip
            client.queue.clear()
    LEDGER.during = "socket.io order event"
    socketio_subscriber.on_order_placed(OrderPlacedEvent(
        mode="live", strategy="g5", symbol="SBIN", exchange="NSE", action="BUY", quantity=1,
        pricetype="MARKET", product="MIS", orderid="G5-1"))  # fmt: skip
    LEDGER.during = "after socket.io"
    return {
        "namespaces": namespaces,
        "sockets": [{"namespace": namespace, "caller": name, "connected": connected,
                     "on_connect": heard, "after_order": [m["name"] for m in client.queue]}
                    for namespace, name, client, connected, heard in sockets],
    }  # fmt: skip


def banned_addresses() -> int:
    from database.traffic_db import IPBan, logs_session

    try:
        return IPBan.query.count()
    finally:
        logs_session.remove()


def log_rows() -> dict[str, int]:
    """The rows in the owner's order log and analyzer log once every write handed to their
    executors has run. No caller of the walk holds a key, so each row is one a refused key wrote."""
    from database import analyzer_db, apilog_db

    apilog_db.executor.shutdown(wait=True)
    analyzer_db.executor.shutdown(wait=True)
    try:
        return {"order_logs": apilog_db.OrderLog.query.count(),
                "analyzer_logs": analyzer_db.AnalyzerLog.query.count()}  # fmt: skip
    finally:
        apilog_db.db_session.remove()
        analyzer_db.db_session.remove()


def main(argv: list[str]) -> int:
    report_path = Path(argv[1])
    refuse_unless_isolated(Path.cwd().resolve())
    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False
    dotenv.main.load_dotenv = dotenv.load_dotenv
    guard_the_machine()
    if ALARM:
        signal.signal(signal.SIGALRM, _alarm)

    module = load_app_module()
    stub_start_up(module)
    app = build_app(module, react=True)
    create_tables(module, app)
    app.db_ready.set()
    add_owner()
    who = callers(app)
    started = time.monotonic()
    report = {"rules": rules_of(app), "requests": walk(app, who, "app")}
    hidden = {(r["rule"], r["endpoint"], r["method"]) for r in report["requests"]
              if r["reached"] != r["endpoint"]}  # fmt: skip
    report["socketio"] = socket_check(app, who)
    bare = build_app(module, react=False)  # the rules the React shell hides answer here
    bare.db_ready.set()
    report["requests"] += walk(bare, callers(bare), "without-react-shell", hidden)
    report["seconds"] = round(time.monotonic() - started, 1)
    report["log_rows"] = log_rows()
    report["ledger"] = LEDGER.entries
    report["banned_addresses"] = banned_addresses()
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
