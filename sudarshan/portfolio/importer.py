"""Importing a file of entries into a tab, and exporting a tab (M7.22, design 4.11 steps 1-4).

* **Preview.** The file is parsed in memory (imports/generic.py or imports/zerodha.py), checked
  against the tab as it is now, and stored as an ``import_batch`` with status ``preview``: the
  normalised rows (the entry each row means, its words, its duplicate key) and never the file. A
  preview older than 24 h is dropped the next time anyone previews or commits.
* **Commit.** Names the tab version the preview saw. In one IMMEDIATE transaction the version is
  checked, the new rows are checked against the ledger once more, every row is written with its
  ``batch_id`` and ``import_key`` (so the same trade cannot enter twice, whatever happens), the tab
  moves one version and the batch becomes ``committed``.
* **Undo** (``discard`` of a committed batch). Removes the batch's rows if the tab stays valid;
  otherwise it says which entry stands in the way. A batch still at ``preview`` is simply dropped.
* **Export.** The posted entries of a tab in the generic format (imports/generic.export).

Everything is one store transaction on the store's writer thread, so a check and the write it
guards see the same rows. No clock: ``now`` arrives by name, in epoch milliseconds.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from sudarshan.portfolio.book import _entry_fields, _names, _writable_tab
from sudarshan.portfolio.core import words
from sudarshan.portfolio.core.days import day_of_instant
from sudarshan.portfolio.core.kinds import HOLDING_KINDS, IMPORT_FORMATS
from sudarshan.portfolio.core.ledger import replay
from sudarshan.portfolio.core.model import BadEntry, Entry, Refusal
from sudarshan.portfolio.imports import generic, zerodha
from sudarshan.portfolio.imports.csvutil import BadFile
from sudarshan.portfolio.imports.preview import Context, Preview
from sudarshan.portfolio.store import (
    Invalid,
    NotFound,
    PortfolioStore,
    Refused,
    Scope,
    Tx,
    VersionConflict,
)

PREVIEW_TTL_MS = 24 * 3600 * 1000
FORMATS = {"generic": generic.preview, "zerodha": zerodha.preview}
WORDS = {
    "gone": "This import is no longer available. Choose the file again.",
    "changed": "This portfolio changed after the file was checked. Choose the file again.",
    "format": "Choose the kind of file: a SUDARSHAN CSV or a Zerodha tradebook.",
    "account": "Choose the account the entries belong to from the list.",
    "name": "Give the file a name.",
    "nothing": "There is nothing new in this file to add.",
}
assert set(FORMATS) == set(IMPORT_FORMATS)


class Importer:
    def __init__(self, store: PortfolioStore, resolver: Any = None):
        self.store = store
        #: The master-contract reader (resolver.py): a symbol the book does not know is asked
        #: there for its ISIN, which may be an old one the book kept as an alias.
        self.resolver = resolver

    # ---- step 1 and 2: upload, preview --------------------------------------------------------
    def preview(
        self,
        scope: Scope,
        tab_id: int,
        fmt: str,
        filename: str,
        text: str,
        account_id: int | None,
        *,
        now: int,
    ) -> dict[str, Any]:
        if fmt not in FORMATS:
            raise Invalid(WORDS["format"])
        if not isinstance(text, str) or not isinstance(filename, str) or not filename.strip():
            raise Invalid(WORDS["name"])

        def run(tx: Tx):
            _drop_old(tx, now)
            tab = tx.get("tab", tab_id)
            if tab is None:
                raise NotFound()
            _writable_tab(tx, tab_id, tab["version"])
            result = _read(tx, tab_id, fmt, text, account_id, now, self.resolver)
            return self._store(tx, tab, fmt, filename.strip()[:200], result, now)

        return self._guard(self.store.write, scope, run)

    @staticmethod
    def _store(tx: Tx, tab: dict, fmt: str, filename: str, result: Preview, now: int) -> dict:
        rows = [
            {"row": r.row, "status": r.status, "words": r.words, "entry": r.entry, "key": r.key}
            for r in result.rows
        ]
        counts = {s: result.count(s) for s in ("new", "dup", "bad")}
        batch = tx.insert(
            "import_batch",
            {
                "tab_id": tab["id"],
                "format": fmt,
                "rows_total": len(rows),
                "rows_new": counts["new"],
                "rows_dup": counts["dup"],
                "rows_bad": counts["bad"],
                "preview": {"filename": filename, "rows": rows},
                "status": "preview",
                "tab_version": tab["version"],
            },
            now,
        )
        shown, more = result.listing()
        return {
            "batch_id": batch["id"],
            "format": fmt,
            "filename": filename,
            "rows_total": len(rows),
            "rows_new": counts["new"],
            "rows_dup": counts["dup"],
            "rows_bad": counts["bad"],
            "words": result.summary,
            "rows": shown,
            "truncated": more,
            "tab_version": tab["version"],
            "expires_at": now + PREVIEW_TTL_MS,
        }

    # ---- step 3: commit -------------------------------------------------------------------------
    def commit(self, scope: Scope, batch_id: int, version: int, *, now: int) -> dict[str, Any]:
        def run(tx: Tx):
            _drop_old(tx, now)
            batch = tx.get("import_batch", batch_id)
            if batch is None or batch["status"] != "preview":
                return None  # swept just now, or never there: the sweep must still be saved
            tab = _writable_tab(tx, batch["tab_id"], version)
            if batch["tab_version"] != version:
                raise VersionConflict("version_conflict")
            new = [r for r in batch["preview"]["rows"] if r["status"] == "new"]
            if not new:
                raise Invalid(WORDS["nothing"])
            start = tx.next_seq(tab["id"])
            made = []
            for place, found in enumerate(new):
                fields = _entry_fields(tx, found["entry"], now)
                made.append((fields, start + place, found["key"]))
            _check(tx, tab["id"], [Entry.of({**f, "seq": s}) for f, s, _ in made])
            for fields, seq, key in made:
                values = {**fields, "tab_id": tab["id"], "seq": seq, "source": "import"}
                tx.insert("activity", {**values, "batch_id": batch_id, "import_key": key}, now)
            moved = tx.bump_tab(tab["id"], version, now)
            tx.change("import_batch", batch_id, None, {"status": "committed"}, now)
            return {"batch_id": batch_id, "added": len(made), "tab_version": moved["version"]}

        done = self._guard(self.store.write, scope, run)
        if done is None:
            raise NotFound(WORDS["gone"])
        return done

    # ---- step 4: discard or undo ----------------------------------------------------------------
    def discard(
        self, scope: Scope, batch_id: int, version: int | None, *, now: int
    ) -> dict[str, Any]:
        def run(tx: Tx):
            _drop_old(tx, now)
            batch = tx.get("import_batch", batch_id)
            if batch is None or batch["status"] in ("discarded", "undone"):
                return None
            if batch["status"] == "preview":
                tx.change(
                    "import_batch", batch_id, None, {"status": "discarded", "preview": None}, now
                )
                return {
                    "batch_id": batch_id,
                    "status": "discarded",
                    "removed": 0,
                    "tab_version": None,
                }
            if version is None:
                raise Invalid(WORDS["changed"])
            tab = _writable_tab(tx, batch["tab_id"], version)
            mine = [r for r in tx.entries(tab["id"]) if r["batch_id"] == batch_id]
            _undo_check(tx, tab["id"], {r["id"] for r in mine})
            for row in mine:
                tx.remove("activity", row["id"])
            moved = tx.bump_tab(tab["id"], version, now)
            tx.change("import_batch", batch_id, None, {"status": "undone", "preview": None}, now)
            return {
                "batch_id": batch_id,
                "status": "undone",
                "removed": len(mine),
                "tab_version": moved["version"],
            }

        done = self._guard(self.store.write, scope, run)
        if done is None:
            raise NotFound(WORDS["gone"])
        return done

    @staticmethod
    def _guard(call, scope, run):
        try:
            return call(scope, run)
        except BadEntry as exc:
            raise Invalid(str(exc)) from exc

    # ---- export ---------------------------------------------------------------------------------
    def export(self, scope: Scope, tab_id: int, *, today: int) -> dict[str, str]:
        def run(tx: Tx):
            if tx.get("tab", tab_id) is None:
                raise NotFound()
            accounts = {a["id"]: a["name"] for a in tx.rows("account")}
            assets = {
                a["id"]: generic.AssetRef(
                    a["symbol"] or a["name"], a["exchange"] or "", a["isin"] or ""
                )
                for a in tx.rows("asset")
            }
            return generic.export(tx.entries(tab_id), accounts, assets, tab_id, today)

        return self.store.read(scope, run).wire()


# ---- helpers ---------------------------------------------------------------------------------
def _drop_old(tx: Tx, now: int) -> None:
    """Previews older than 24 h are dropped; committed batches keep their row (the undo needs it)."""
    for batch in tx.rows("import_batch", status="preview"):
        if batch["created_at"] < now - PREVIEW_TTL_MS:
            tx.remove("import_batch", batch["id"])


def _read(
    tx: Tx, tab_id: int, fmt: str, text: str, account_id: int | None, now: int, resolver: Any = None
) -> Preview:
    accounts = {a["id"]: a["name"] for a in tx.rows("account") if not a["system"]}
    if account_id is not None and account_id not in accounts:
        raise Invalid(WORDS["account"])
    names, decimals = _names(tx)
    holdings = {a["id"] for a in tx.rows("asset") if a["kind"] in HOLDING_KINDS}

    def book_asset(kind: str, value: str) -> int | None:
        alias = tx.find_alias(kind, value.strip().upper()) if value.strip() else None
        return alias["asset_id"] if alias and alias["asset_id"] in holdings else None

    def resolve(symbol: str, exchange: str, isin: str) -> int | None:
        found = book_asset("symbol", symbol) or book_asset("isin", isin)
        if found is not None or resolver is None or not symbol.strip():
            return found
        # Not in the book by name: the master contract may know the symbol's ISIN, and the book
        # may hold that ISIN (an old one kept as an alias) under another symbol.
        for place in (exchange.strip().upper(),) if exchange.strip() else ("NSE", "BSE"):
            try:
                listing = resolver.find(symbol, place)
            except Exception:  # an unreadable master contract only means "not found"
                return None
            if listing is not None and listing.isin:
                return book_asset("isin", listing.isin)
        return None

    rows = tx.entries(tab_id)
    ctx = Context(
        today=day_of_instant(now),
        resolve=resolve,
        accounts=accounts,
        default_account=account_id,
        existing=tuple(Entry.of(r) for r in rows if r["status"] == "posted"),
        keys={r["import_key"]: day_of_instant(r["created_at"]) for r in rows if r["import_key"]},
        names=names,
        decimals=decimals,
    )
    try:
        return FORMATS[fmt](text, ctx)
    except BadFile as bad:
        raise Invalid(str(bad)) from None


def _check(tx: Tx, tab_id: int, added: list[Entry]) -> None:
    """The tab's ledger with the new rows in, or Refused in the ledger's words."""
    entries = [Entry.of(r) for r in tx.entries(tab_id)]
    entries += [dataclasses.replace(e, id=-(n + 1)) for n, e in enumerate(added)]
    names, decimals = _names(tx)
    try:
        replay(entries, names=names, decimals=decimals)
    except Refusal as refusal:
        stuck = refusal.entry_id if refusal.entry_id and refusal.entry_id > 0 else None
        facts = refusal.facts
        text = words.import_clash(
            names.asset(facts.get("asset_id")),
            facts["kind"],
            facts["day"],
            refusal.reason,
            names.unit(facts.get("asset_id")),
        )
        raise Refused(text, entry_id=stuck, reason=refusal.reason) from refusal


def _undo_check(tx: Tx, tab_id: int, drop: set[int]) -> None:
    """The tab without the batch's rows must still replay; if not, the entry that depends on them."""
    entries = [Entry.of(r) for r in tx.entries(tab_id) if r["id"] not in drop]
    names, decimals = _names(tx)
    try:
        replay(entries, names=names, decimals=decimals)
    except Refusal as refusal:
        facts = refusal.facts
        unit = names.unit(facts.get("asset_id"))
        later = words.NOUNS[facts["kind"]]
        text = (
            f"Removing this import would leave the {later} on {words.date(facts['day'])} without "
            f"the {unit} of {names.asset(facts.get('asset_id'))} it needs. "
            f"Delete or change that {later} first."
        )
        raise Refused(text, entry_id=refusal.entry_id, reason=refusal.reason) from refusal
