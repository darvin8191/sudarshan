"""A tab's exports per view, CSV and PDF (phase P7, lane X: M7.40), over export.py.

``export_csv`` with no ``view`` is the export the Import sheet has always had: the entries in the
import format, named by the tab's number. With ``view`` it writes that view of the tab
(``kinds.EXPORT_VIEWS``); ``activity`` is the same entries under the export's own file name. The
entries are as old as the tab's ledger and are never switched off; the four views of figures
wait for the switch the contract gives the phase's exports (``SWITCH_OF["export_pdf"]``), as
``export_pdf`` itself does through ``ops.switched``.

Owner only: no export is a family op or a journal op (``kinds.FAMILY_OPS``), every read here is
under the owner's scope, and the Upstox tab, which has no ledger of its own, is refused in words.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from .. import export, export_pdf, serialize
from ..config import switch_on
from ..core.days import day_of_instant
from ..core.kinds import SWITCH_OF, WORDS
from ..errors import ApiError
from ..importer import Importer
from ..store import Invalid, NotFound, StoreError
from . import op
from .common import OWNER, answer, need, parts, warmed, whole, work

log = logging.getLogger(__name__)

#: The switch the views of figures wait for: the one the contract gives the PDF.
SWITCH = SWITCH_OF["export_pdf"]
#: Asked of ``tab_view`` so that no saved default view narrows the file: the whole tab.
WHOLE_TAB = {"accounts": [], "view": None}


def _importer(ctx) -> Importer:
    return ctx.part("importer", lambda store: Importer(store, ctx.ensure_resolver()))


NO_PDF = (
    "The PDF could not be made on the server just now. Download the CSV instead; if this keeps "
    "happening, the server's report fonts or its PDF library need checking."
)


def _view(message: Mapping[str, Any], default: str | None = None) -> str:
    try:
        return export.view_of(message.get("view") or default)
    except ValueError as bad:
        raise ApiError(400, "bad_request", str(bad), "view") from None


def _tab(tx, tab_id: int) -> dict[str, Any]:
    """The tab a view is exported of; the Upstox tab is not one."""
    tab = tx.get("tab", tab_id)
    if tab is not None and tab["system"]:
        raise Invalid(export.WORDS["upstox"])
    return tab  # None: the read that follows says the tab is gone


async def _sheet(ctx, tab_id: int, view: str) -> export.Sheet:
    """The view as the phone is shown it now, read under the owner's scope."""
    await warmed(ctx)
    _, views = parts(ctx)
    store, now = need(ctx), ctx.clock()

    def run() -> export.Sheet:
        store.read(OWNER, lambda tx: _tab(tx, tab_id))
        if view in ("performance", "income"):
            report = views.performance(OWNER, tab_id, now=now)
            tab = views.tab_view(OWNER, tab_id, WHOLE_TAB, now=now)["tab"]
            make = export.performance if view == "performance" else export.income
            return make(tab, report, now)
        if view == "activity":

            def ledger(tx):
                tab = _tab(tx, tab_id)
                if tab is None:
                    raise NotFound()
                names = {a["id"]: a["name"] for a in tx.rows("account")}
                assets = {a["id"]: a["name"] for a in tx.rows("asset")}
                return serialize.tab(tab), tx.entries(tab_id), names, assets

            return export.entries(*store.read(OWNER, ledger), now)
        shown = views.tab_view(OWNER, tab_id, WHOLE_TAB, now=now)
        if view == "allocation":
            return export.allocation(shown)
        names = store.read(OWNER, lambda tx: {a["id"]: a["name"] for a in tx.rows("account")})
        return export.holdings(shown, names)

    return await work(run)


@op("export_csv")
async def export_csv(ctx, message: Mapping[str, Any]) -> Any:
    store, tab_id = need(ctx), whole(message, "id")
    importer, now = _importer(ctx), ctx.clock()
    if message.get("view") is None:
        return await answer(lambda: importer.export(OWNER, tab_id, today=day_of_instant(now)))
    view = _view(message)
    if view == "activity":

        def entries() -> dict[str, str]:
            tab = store.read(OWNER, lambda tx: _tab(tx, tab_id))
            text = importer.export(OWNER, tab_id, today=day_of_instant(now))["text"]
            return export.entries_file(tab, text, now)

        return await answer(entries)
    if not switch_on(ctx, SWITCH):
        raise ApiError(501, "not_ready", WORDS["not_ready"])
    sheet = await _sheet(ctx, tab_id, view)
    return await answer(lambda: export.csv_file(sheet))


@op("export_pdf")
async def export_pdf_op(ctx, message: Mapping[str, Any]) -> Any:
    """One view of a tab as a PDF (base64 in the JSON answer); no ``view`` means the holdings."""
    need(ctx)
    tab_id, view = whole(message, "id"), _view(message, "holdings")
    sheet = await _sheet(ctx, tab_id, view)

    def printed() -> dict[str, Any]:
        try:
            return export_pdf.pdf_file(sheet)
        except Exception:  # a missing library or font is the server's fault, said in words
            log.exception("a PDF export could not be made")
            raise StoreError(NO_PDF) from None

    return await answer(printed)
