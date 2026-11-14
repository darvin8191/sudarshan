"""The automation database: strategies, their state and one line per alert (M12.1).

The tables are in schema.py. The engine comes from OpenAlgo's ``database.engine_factory``
(NullPool, and inside OpenAlgo the cooperative SQLite lock wait that keeps the eventlet hub
running); every use is ``with self._session() as session``. The store creates what is missing
when first used, for a dev checkout; deployments get it from
upgrade/migrate_sudarshan_automation.py.

Two writes here are claims, and both are one statement against a unique index:

- ``claim_alert`` inserts the alert's row before any order is sent; a second delivery of the same
  alert loses the insert and is a repeat;
- ``set_state`` makes a strategy Active only if no other strategy that is not Stopped owns the
  same contract and product in the same mode.

A link's token is never kept: only its SHA-256 and its last four characters.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from database.engine_factory import create_db_engine
from utils import real_threading
from utils.lazy import LazyInit
from utils.logging import get_logger

from . import contract
from . import model as m
from .schema import AlertRow, StateRow, StrategyRow, create_missing, default_path

logger = get_logger(__name__)

PAYLOAD_KEEP = 1024
SENTENCE_KEEP = 600


class Conflict(Exception):
    """The strategy was changed after the page read it (its version moved on)."""


class Missing(Exception):
    """No such strategy."""


class Taken(Exception):
    """Another strategy that is not Stopped owns the contract. ``other`` is its name."""

    def __init__(self, other: str):
        super().__init__(other)
        self.other = other


def now_ms() -> int:
    return int(time.time() * 1000)


def new_token() -> str:
    """A link's token: 32 random bytes, URL-safe, behind its prefix. Shown once."""
    return contract.TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _strategy(row: StrategyRow) -> m.Strategy:
    return m.from_row(m.Strategy, row)


def _state(row: StateRow | None, strategy_id: int) -> m.State:
    return m.State(strategy_id) if row is None else m.from_row(m.State, row)


class AutomationStore:
    """The automation database at ``path`` (``schema.default_path()`` when omitted)."""

    def __init__(self, path: Path | str | None = None):
        self._path = Path(path) if path else None
        self._engine = None
        self._factory = None
        self._lock = real_threading.Lock()  # reference work only, never across a database call

    @property
    def path(self) -> Path:
        return self._path or default_path()

    def _session(self):
        factory = self._factory
        if factory is None:
            engine, built = self._prepare()  # database work, outside any lock
            with self._lock:
                if self._factory is None:
                    self._engine, self._factory, engine = engine, built, None
                factory = self._factory
            if engine is not None:  # another caller prepared it first
                engine.dispose()
        return factory()

    def _prepare(self):
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        # hide_parameters: a failed statement's error text must not carry a token's hash or an
        # alert's text into log/errors.jsonl.
        engine = create_db_engine(f"sqlite:///{path}", hide_parameters=True)
        try:
            create_missing(engine)
        except BaseException:
            engine.dispose()
            raise
        return engine, sessionmaker(bind=engine, expire_on_commit=False)

    def dispose(self) -> None:
        """Close the engine (tests; the app keeps one for its life)."""
        with self._lock:
            engine, self._engine, self._factory = self._engine, None, None
        if engine is not None:
            engine.dispose()

    # -- strategies ---------------------------------------------------------------------------

    def create(self, columns: dict[str, Any]) -> tuple[m.Strategy, str]:
        """A new strategy, Sandbox and Stopped, with its link's token (returned once)."""
        token, at = new_token(), now_ms()
        row = StrategyRow(
            **columns,
            token_sha256=token_hash(token),
            token_tail=token[-4:],
            state=m.STOPPED,
            mode=m.SANDBOX,
            created_ms=at,
            updated_ms=at,
            version=1,
        )
        with self._session() as session:
            session.add(row)
            try:
                session.flush()
                session.add(StateRow(strategy_id=row.id))
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise contract.Refused(
                    "Another strategy already has this name. Choose a different one.", "name"
                ) from exc
            return _strategy(row), token

    def count(self) -> int:
        with self._session() as session:
            return session.scalar(select(func.count()).select_from(StrategyRow)) or 0

    def strategies(self) -> list[m.Strategy]:
        with self._session() as session:
            rows = session.scalars(select(StrategyRow).order_by(StrategyRow.id)).all()
            return [_strategy(row) for row in rows]

    def strategy(self, strategy_id: int) -> m.Strategy | None:
        with self._session() as session:
            row = session.get(StrategyRow, strategy_id)
            return None if row is None else _strategy(row)

    def by_token(self, token: str) -> m.Strategy | None:
        """The strategy a link's token belongs to. The lookup is by hash; the token is not kept."""
        with self._session() as session:
            row = session.scalar(
                select(StrategyRow).where(StrategyRow.token_sha256 == token_hash(token))
            )
            return None if row is None else _strategy(row)

    def state(self, strategy_id: int) -> m.State:
        with self._session() as session:
            return _state(session.get(StateRow, strategy_id), strategy_id)

    def both(self, strategy_id: int) -> tuple[m.Strategy, m.State] | None:
        """The strategy and its state in one read."""
        with self._session() as session:
            row = session.get(StrategyRow, strategy_id)
            if row is None:
                return None
            return _strategy(row), _state(session.get(StateRow, strategy_id), strategy_id)

    def _write(self, strategy_id: int, version: int | None, values: dict[str, Any]) -> m.Strategy:
        """One versioned update. ``version`` None writes whatever version is there (the intake's
        own changes, made under the strategy's lock)."""
        with self._session() as session:
            where = [StrategyRow.id == strategy_id]
            if version is not None:
                where.append(StrategyRow.version == version)
            try:
                done = session.execute(
                    update(StrategyRow)
                    .where(*where)
                    .values(**values, updated_ms=now_ms(), version=StrategyRow.version + 1)
                )
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise self._why_refused(strategy_id, values, exc) from exc
            row = session.get(StrategyRow, strategy_id)
            if row is None:
                raise Missing(strategy_id)
            if done.rowcount != 1:
                raise Conflict(strategy_id)
            return _strategy(row)

    def _why_refused(self, strategy_id: int, values: dict[str, Any], exc: IntegrityError):
        text = str(getattr(exc, "orig", exc))
        if "auto_strategy.name" in text:
            return contract.Refused(
                "Another strategy already has this name. Choose a different one.", "name"
            )
        return Taken(self._owner_of_contract(strategy_id, values))

    def _owner_of_contract(self, strategy_id: int, values: dict[str, Any]) -> str:
        with self._session() as session:
            mine = session.get(StrategyRow, strategy_id)
            if mine is None:
                return ""
            want = {
                key: values.get(key, getattr(mine, key))
                for key in ("mode", "contract_key", "product")
            }
            other = session.scalar(
                select(StrategyRow.name).where(
                    StrategyRow.id != strategy_id,
                    StrategyRow.state != m.STOPPED,
                    StrategyRow.mode == want["mode"],
                    StrategyRow.contract_key == want["contract_key"],
                    StrategyRow.product == want["product"],
                )
            )
            return other or ""

    def update(self, strategy_id: int, version: int, columns: dict[str, Any]) -> m.Strategy:
        return self._write(strategy_id, version, columns)

    def set_state(
        self, strategy_id: int, version: int | None, state: str, why: str | None = None
    ) -> m.Strategy:
        """Active, exits only or Stopped. Raises ``Taken`` when another strategy owns the
        contract."""
        return self._write(strategy_id, version, {"state": state, "stopped_why": why})

    def set_mode(self, strategy_id: int, version: int, mode: str) -> m.Strategy:
        return self._write(strategy_id, version, {"mode": mode})

    def replace_token(self, strategy_id: int, version: int) -> tuple[m.Strategy, str]:
        """A new link. From the commit on, the old one answers as a link nobody made."""
        token = new_token()
        strategy = self._write(
            strategy_id, version, {"token_sha256": token_hash(token), "token_tail": token[-4:]}
        )
        return strategy, token

    # -- alerts -------------------------------------------------------------------------------

    def claim_alert(
        self,
        strategy_id: int,
        decide: Callable[[m.Strategy, m.State], dict[str, Any] | None],
        day: str,
        token: str | None = None,
    ) -> tuple[m.Strategy, m.State, int | None, dict[str, Any] | None] | None:
        """Read the strategy and its state, let ``decide`` judge them, and write the alert's row,
        all in one transaction. Call it holding the strategy's lock.

        ``decide`` returns the row's columns (with ``claim`` and ``verdict``), or ``None`` to
        write nothing. ``counts`` in them (taken out) adds one to the day's alerts;
        ``state_values`` and ``strategy_values`` (taken out) are written with the row. Returns
        the strategy and state as read, the new row's id and its columns; the id is ``None`` when
        the claim was already there (a repeat: nothing was written). ``None`` when the strategy is
        gone, or when ``token`` is given and is no longer its link's.
        """
        with self._session() as session:
            row = session.get(StrategyRow, strategy_id)
            if row is None or (token is not None and row.token_sha256 != token_hash(token)):
                return None
            state_row = session.get(StateRow, strategy_id)
            if state_row is None:
                state_row = StateRow(strategy_id=strategy_id)
                session.add(state_row)
                session.flush()
            strategy, state = _strategy(row), _state(state_row, strategy_id)
            columns = decide(strategy, state)
            if columns is None:
                session.rollback()
                return strategy, state, None, None
            columns = dict(columns)
            counts = columns.pop("counts", False)
            state_values = columns.pop("state_values", None) or {}
            strategy_values = columns.pop("strategy_values", None) or {}
            alert = AlertRow(strategy_id=strategy_id, **_kept(columns))
            session.add(alert)
            if counts:
                state_values = {
                    **state_values,
                    "day": day,
                    "alerts_today": state.alerts_on(day) + 1,
                }
            for key, value in state_values.items():
                setattr(state_row, key, value)
            if state_values:
                state_row.version = (state_row.version or 0) + 1
            for key, value in strategy_values.items():
                setattr(row, key, value)
            if strategy_values:
                row.version, row.updated_ms = (row.version or 0) + 1, now_ms()
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return strategy, state, None, columns
            return strategy, state, alert.id, columns

    def finish_alert(
        self,
        alert_id: int,
        *,
        verdict: str,
        sentence: str,
        order_id: str | None = None,
        answer_ms: int | None = None,
        state_values: dict[str, Any] | None = None,
        **columns: Any,
    ) -> None:
        """The order's answer on the alert's row, and the state moved on when it should be."""
        with self._session() as session:
            alert = session.get(AlertRow, alert_id)
            if alert is None:
                return
            for key, value in _kept(
                {**columns, "verdict": verdict, "sentence": sentence, "order_id": order_id}
            ).items():
                setattr(alert, key, value)
            alert.answer_ms = answer_ms
            if state_values:
                state_row = session.get(StateRow, alert.strategy_id)
                if state_row is not None:
                    for key, value in state_values.items():
                        setattr(state_row, key, value)
                    state_row.version = (state_row.version or 0) + 1
            session.commit()

    def note_alert(self, strategy_id: int, **columns: Any) -> int | None:
        """A line that is no claim: a repeat, or a message that could not be read."""
        if not columns.get("claim"):
            columns["claim"] = f"note|{now_ms()}|{secrets.token_hex(4)}"
        with self._session() as session:
            alert = AlertRow(strategy_id=strategy_id, **_kept(columns))
            session.add(alert)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return None
            return alert.id

    def write_state(self, strategy_id: int, values: dict[str, Any]) -> None:
        with self._session() as session:
            state_row = session.get(StateRow, strategy_id)
            if state_row is None:
                return
            for key, value in values.items():
                setattr(state_row, key, value)
            state_row.version = (state_row.version or 0) + 1
            session.commit()

    def alerts(
        self, strategy_id: int | None = None, before: int | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Alert lines, newest first, without the stored message text or the sender's address."""
        query = select(AlertRow).order_by(AlertRow.id.desc()).limit(max(1, min(limit, 500)))
        if strategy_id is not None:
            query = query.where(AlertRow.strategy_id == strategy_id)
        if before is not None:
            query = query.where(AlertRow.id < before)
        with self._session() as session:
            rows = session.scalars(query).all()
            return [{key: getattr(row, key) for key in contract.ALERT} for row in rows]

    def last_alert(self, strategy_id: int) -> dict[str, Any] | None:
        found = self.alerts(strategy_id, limit=1)
        return found[0] if found else None

    def alert(self, alert_id: int) -> dict[str, Any] | None:
        with self._session() as session:
            row = session.get(AlertRow, alert_id)
            if row is None:
                return None
            return {column.name: getattr(row, column.name) for column in AlertRow.__table__.columns}

    def sandbox_run(self, strategy_id: int) -> dict[str, int]:
        """How a strategy did in the sandbox, for the Go Live sheet: alerts handled, the days they
        came on, how many sent an order and how many were refused."""
        with self._session() as session:
            rows = session.execute(
                select(AlertRow.verdict, AlertRow.received_ms).where(
                    AlertRow.strategy_id == strategy_id, AlertRow.mode == m.SANDBOX
                )
            ).all()
        handled = [
            (verdict, at) for verdict, at in rows if verdict not in ("repeat", "bad_message")
        ]
        india = 19_800_000  # India's time, +5:30, in ms
        return {
            "alerts": len(handled),
            "sessions": len({(at + india) // 86_400_000 for _, at in handled}),
            "sent": sum(1 for verdict, _ in handled if verdict in ("sent", "no_action")),
            "refused": sum(1 for verdict, _ in handled if verdict not in m.MOVED),
        }


def _kept(columns: dict[str, Any]) -> dict[str, Any]:
    """Columns cut to what the table keeps."""
    out = dict(columns)
    if out.get("payload") is not None:
        out["payload"] = str(out["payload"])[:PAYLOAD_KEEP]
    if out.get("sentence") is not None:
        out["sentence"] = str(out["sentence"])[:SENTENCE_KEEP]
    if out.get("claim") is not None:
        out["claim"] = str(out["claim"])[:160]
    return out


#: The process's one store. Its factory only constructs: nothing is opened until it is used.
_shared: LazyInit[AutomationStore] = LazyInit(AutomationStore, "automation-store")


def shared() -> AutomationStore:
    return _shared.get()
