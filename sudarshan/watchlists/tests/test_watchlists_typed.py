"""The built-in lists made from typed files (M8.8, W13): the format, where files come from, the
catalogue offering only lists whose file is there, a changed file read again, and the exception's
limits for the new code. Fixtures of the bridge tests are reused."""

# ruff: noqa: F811
from __future__ import annotations

import gc
import json
import os
import threading
from datetime import datetime
from pathlib import Path

import pytest
from presets_master import index_of, keys, snapshot_of
from test_watchlists_presets_bridge import API, box, client, day, view  # noqa: F401  (fixtures)

from sudarshan.screener import universe as screener_universe
from sudarshan.watchlists import contract, master, presets
from sudarshan.watchlists.presets import typed_lists as typed
from sudarshan.watchlists.refs import preset as reader
from sudarshan.watchlists.words import presets as words

VECTORS = json.loads((Path(__file__).parent / "vectors" / "typed.json").read_text("utf-8"))
SPEC = {s.id: s for s in typed.SPECS}


@pytest.fixture(autouse=True)
def folders(monkeypatch, tmp_path):
    """An empty server folder of its own, and the typed reader's memory of files cleared."""
    server = tmp_path / "universes"
    server.mkdir()
    monkeypatch.setenv(typed.ENV, str(server))
    typed._loaded.clear()
    typed._logged.clear()
    yield server
    typed._loaded.clear()
    typed._logged.clear()


def put(server: Path, name: str, text: str, when: int | None = None) -> Path:
    path = server / name
    path.write_text(text, encoding="utf-8")
    if when is not None:
        os.utime(path, ns=(when, when))
    return path


def nse(*symbols):
    return [(s, "NSE", "EQ", None, None) for s in symbols]


# ------------------------------------------------------------------------------------- the format


@pytest.mark.parametrize("case", VECTORS["parse"], ids=lambda c: c["name"][:50])
def test_the_typed_format(case):
    got = typed.parse(case["text"])
    assert list(got.symbols) == case["symbols"]
    assert got.as_of == case["as_of"]
    assert [list(p) for p in got.problems] == case["problems"]


def test_a_file_with_one_missing_name_leaves_it_out_and_says_so_in_words(folders):
    put(folders, "NIFTYIT.txt", "# as of 2026-09-30\nAAA\nGHOST\nBBB\n")
    entry = presets.entry("niftyit")
    built = entry.members(entry.prepare(index_of(nse("AAA", "BBB", "CCC"))), 0, {})
    assert keys(built.rows) == ["NSE:AAA", "NSE:BBB"] and built.missing == ("NSE:GHOST",)
    out = presets.lines(entry, built, "2026-09-30")
    assert out == [
        "Built-in list, locked. NIFTY IT as of 30 Sep 2026: 2 symbols.",
        "1 name in this list is not in today's contract list: GHOST. "
        "The list is checked at each index review.",
    ]  # fmt: skip


def test_two_missing_names_use_the_plural(folders):
    put(folders, "NIFTYIT.txt", "# as of 2026-09-30\nABC\nAAA\nXYZ\n")
    entry = presets.entry("niftyit")
    built = entry.members(entry.prepare(index_of(nse("AAA"))), 0, {})
    assert presets.lines(entry, built, "2026-09-30")[1] == (
        "2 names in this list are not in today's contract list: ABC, XYZ. "
        "The list is checked at each index review."
    )


def test_a_file_with_no_header_says_the_date_is_not_recorded(folders):
    put(folders, "NIFTYIT.txt", "AAA\n")
    entry = presets.entry("niftyit")
    prepared = entry.prepare(index_of(nse("AAA")))
    assert prepared.as_of is None
    built = entry.members(prepared, 0, {})
    assert presets.lines(entry, built, prepared.as_of) == [
        "Built-in list, locked. NIFTY IT (date not recorded): 1 symbol."
    ]


def test_a_bad_line_is_named_by_its_number_and_the_rest_of_the_list_stands(folders):
    put(folders, "NIFTYIT.txt", "# as of 2026-09-30\nAAA\nab cd\nBBB\nAAA\n")
    entry = presets.entry("niftyit")
    prepared = entry.prepare(index_of(nse("AAA", "BBB")))
    built = entry.members(prepared, 0, {})
    assert keys(built.rows) == ["NSE:AAA", "NSE:BBB"]
    assert presets.lines(entry, built, prepared.as_of)[-1] == (
        '2 lines in this list\'s file were left out: line 3 ("ab cd" is not a symbol), '
        "line 5 (AAA is already on line 2). Symbols are capital letters and digits."
    )


def test_the_names_of_many_bad_lines_are_cut_after_five():
    line = words.problems_line([(n, "symbol", "x y", 0) for n in range(1, 9)])
    assert line.startswith("8 lines in this list's file were left out: line 1 ")
    assert "line 5 (" in line and "line 6 (" not in line and ", and 3 more." in line


def test_the_typed_locked_line_is_the_one_of_the_design():
    assert words.typed_locked_line("NIFTY 50", 50, "2026-09-30") == (
        "Built-in list, locked. NIFTY 50 as of 30 Sep 2026: 50 symbols."
    )


# ----------------------------------------------------------------------------- where files come from


def test_the_repository_files_read_the_same_symbols_with_both_readers():
    """Every file of the screener's folder, read by the screener's reader and by this one."""
    files = sorted(screener_universe.LISTS_DIR.glob("*.txt"))
    assert {f.stem for f in files} >= {"NIFTY50", "NIFTYBANK"}
    for path in files:
        theirs = screener_universe.read_list(path.stem)
        mine = typed.parse(path.read_text(encoding="utf-8"))
        assert list(mine.symbols) == theirs, path.name
        assert mine.problems == (), f"{path.name} passes the format check"
        assert mine.as_of == "2026-09-30", f"{path.name} carries its date"


def test_with_no_server_folder_the_two_repository_lists_are_offered_from_the_repository():
    assert [e.id for e in presets.CATALOGUE if e.kind == "typed"] == ["nifty50", "niftybank"]
    got = typed.load(SPEC["nifty50"])
    assert got is not None and Path(got.stamp[0]).parent == typed.REPO_DIR
    assert list(got.typed.symbols) == screener_universe.read_list("NIFTY50")


def test_the_server_copy_of_a_name_wins_over_the_repository(folders):
    put(folders, "NIFTY50.txt", "# as of 2026-03-31\nAAA\nBBB\n")
    got = typed.load(SPEC["nifty50"])
    assert Path(got.stamp[0]).parent == folders
    assert got.typed.symbols == ("AAA", "BBB") and got.typed.as_of == "2026-03-31"
    assert len(typed.load(SPEC["niftybank"]).typed.symbols) == 12, (
        "the other still comes from the repo"
    )


def test_the_server_folder_can_be_named_by_the_environment_and_has_a_default(monkeypatch):
    assert typed.server_dir() != Path(typed.SERVER_DIR)  # the fixture named another
    monkeypatch.delenv(typed.ENV)
    assert typed.server_dir() == Path("/etc/sudarshan/universes")


def test_a_list_the_repository_does_not_ship_is_read_only_from_the_server(
    folders, monkeypatch, tmp_path
):
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    put(fake_repo, "NIFTY100.txt", "# as of 2026-09-30\nAAA\n")
    monkeypatch.setattr(typed, "REPO_DIR", fake_repo)
    assert typed.load(SPEC["nifty100"]) is None and presets.entry("nifty100") is None
    put(folders, "NIFTY100.txt", "# as of 2026-09-30\nBBB\n")
    assert typed.load(SPEC["nifty100"]).typed.symbols == ("BBB",)


def test_every_file_name_is_distinct_and_the_ids_are_refs():
    assert len(typed.SPECS) == 15
    assert len({s.file for s in typed.SPECS}) == 15 and len({s.id for s in typed.SPECS}) == 15
    assert [s.id for s in typed.SPECS if s.repo] == ["nifty50", "niftybank"]
    assert all(s.file == s.file.upper().replace(".TXT", ".txt") for s in typed.SPECS)
    assert all(e.ref == f"preset:{e.id}" and e.kind == "typed" for e in typed.ENTRIES)


# ------------------------------------------------------------------------------ the catalogue


def test_a_list_whose_file_is_absent_is_not_in_the_catalogue(folders):
    ids = [e.id for e in presets.CATALOGUE]
    assert "nifty100" not in ids and "niftyit" not in ids and presets.entry("nifty100") is None
    put(folders, "NIFTYIT.txt", "# as of 2026-09-30\nAAA\n")
    assert "niftyit" in [e.id for e in presets.CATALOGUE] and presets.entry("niftyit")
    (folders / "NIFTYIT.txt").unlink()
    assert presets.entry("niftyit") is None, "a file that went is not offered at the next call"


def test_the_catalogue_order_is_typed_lists_first_then_the_master_ones(folders):
    for s in typed.SPECS:
        put(folders, s.file, "AAA\n")
    got = [e.id for e in presets.CATALOGUE]
    assert got == [s.id for s in typed.SPECS] + ["indices", "fno_stocks", "index_futures"]


def test_an_absent_list_is_not_offered_by_lists_and_view_says_so(client, box, folders):
    day(box)
    got = client.get(f"{API}/lists", headers=client.headers).get_json()["data"]["presets"]
    assert "preset:nifty100" not in [p["ref"] for p in got]
    r = view(client, "nifty100")
    assert r.status_code == 404 and r.get_json()["message"] == words.GONE


def test_lists_names_a_typed_list_with_its_count_and_its_own_date(client, box, folders):
    put(folders, "NIFTYIT.txt", "# as of 2026-03-31\nAAA\nBBB\nGHOST\n")
    box.snap, _ = snapshot_of(nse("AAA", "BBB"), datetime(2026, 11, 9, 8, 40))
    got = {p["ref"]: p for p in client.get(f"{API}/lists", headers=client.headers)
           .get_json()["data"]["presets"]}  # fmt: skip
    assert got["preset:niftyit"]["count"] == 2
    assert got["preset:niftyit"]["note"] == "As of 31 Mar 2026", "the file's date, not the master's"
    put(folders, "NIFTYFMCG.txt", "AAA\n")
    got = {p["ref"]: p for p in client.get(f"{API}/lists", headers=client.headers)
           .get_json()["data"]["presets"]}  # fmt: skip
    assert got["preset:niftyfmcg"]["note"] is None, "no header: no date, never the master's"


def test_the_view_of_a_typed_list_is_locked_with_the_files_date(client, box, folders):
    put(folders, "NIFTYIT.txt", "# as of 2026-09-30\nAAA\nGHOST\nBBB\n")
    box.snap, _ = snapshot_of(nse("AAA", "BBB"), datetime(2026, 11, 9, 8, 40))
    data = view(client, "niftyit").get_json()["data"]
    assert data["locked"] is True and data["version"] == "0" and data["as_of"] == "2026-09-30"
    assert data["lines"][0] == "Built-in list, locked. NIFTY IT as of 30 Sep 2026: 2 symbols."
    assert len(data["lines"]) == 2 and data["missing"] == ["NSE:GHOST"]
    assert [r["contract"]["key"] for r in data["rows"]] == ["NSE:AAA", "NSE:BBB"]
    assert data["sections"] == [] and data["follow"] == [] and data["total"] == 2


def test_the_repository_nifty_50_says_its_date_and_its_fifty_symbols(client, box):
    names = list(screener_universe.read_list("NIFTY50"))
    box.snap, _ = snapshot_of(nse(*names), datetime(2026, 11, 9, 8, 40))
    data = view(client, "nifty50").get_json()["data"]
    assert data["lines"] == ["Built-in list, locked. NIFTY 50 as of 30 Sep 2026: 50 symbols."]
    assert data["as_of"] == "2026-09-30" and data["missing"] == []


# ------------------------------------------------------------------------------- a changed file


def test_a_changed_file_is_read_again_without_a_restart(client, box, folders):
    path = put(folders, "NIFTYIT.txt", "# as of 2026-03-31\nAAA\nBBB\n", when=1_000_000_000_000)
    box.snap, _ = snapshot_of(nse("AAA", "BBB", "CCC"), datetime(2026, 11, 9, 8, 40))
    first = view(client, "niftyit").get_json()["data"]
    assert [r["contract"]["key"] for r in first["rows"]] == ["NSE:AAA", "NSE:BBB"]
    gen = master.shared().generation()
    put(folders, "NIFTYIT.txt", "# as of 2026-09-30\nAAA\nBBB\nCCC\n", when=2_000_000_000_000)
    second = view(client, "niftyit").get_json()["data"]
    assert [r["contract"]["key"] for r in second["rows"]] == ["NSE:AAA", "NSE:BBB", "NSE:CCC"]
    assert second["as_of"] == "2026-09-30"
    assert master.shared().generation() is gen, "the same master generation: only the file moved"
    assert path.exists()


def test_an_unchanged_file_is_prepared_once_for_a_generation(client, box, folders):
    put(folders, "NIFTYIT.txt", "AAA\n")
    box.snap, _ = snapshot_of(nse("AAA"), datetime(2026, 11, 9, 8, 40))
    view(client, "niftyit")
    gen = master.shared().generation()
    first = reader.prepared_for(presets.entry("niftyit"), gen)
    assert reader.prepared_for(presets.entry("niftyit"), gen) is first
    put(folders, "NIFTYIT.txt", "AAA\nBBB\n", when=1_234_567_890_000)
    assert reader.prepared_for(presets.entry("niftyit"), gen) is not first


def test_one_stat_checks_a_file_that_has_not_changed(folders, monkeypatch):
    put(folders, "NIFTYIT.txt", "AAA\n")
    first = typed.load(SPEC["niftyit"])
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("read an unchanged file"))
    assert typed.load(SPEC["niftyit"]) is first


# ------------------------------------------------------------------------------- unreadable


def test_an_unreadable_folder_is_absent_and_logged_once(folders, monkeypatch, tmp_path):
    loop = tmp_path / "loop"
    loop.symlink_to(loop)  # a folder whose every path fails with an OS error, even for root
    monkeypatch.setenv(typed.ENV, str(loop))
    logged = []
    monkeypatch.setattr(typed.logger, "exception", lambda *a, **k: logged.append(a))
    for _ in range(3):
        assert typed.load(SPEC["niftyit"]) is None and presets.entry("niftyit") is None
        assert [e.id for e in presets.CATALOGUE if e.kind == "typed"] == ["nifty50", "niftybank"], (
            "the repository's two are still offered"
        )  # fmt: skip
    assert len(logged) == 1, "said once, however often it is asked"
    loop.unlink()
    loop.mkdir()  # the same folder, readable now
    put(loop, "NIFTYIT.txt", "AAA\n")
    assert typed.load(SPEC["niftyit"]) is not None
    (loop / "NIFTYIT.txt").unlink()
    loop.rmdir()
    loop.symlink_to(loop)  # and unreadable again
    typed.load(SPEC["niftyit"])
    assert len(logged) == 2, "after it was readable again, a new fault is said again"


def test_a_folder_that_is_not_there_is_the_normal_state_and_not_logged(monkeypatch, tmp_path):
    monkeypatch.setenv(typed.ENV, str(tmp_path / "never-made"))
    logged = []
    monkeypatch.setattr(typed.logger, "exception", lambda *a, **k: logged.append(a))
    assert typed.load(SPEC["niftyit"]) is None and logged == []


def test_a_file_too_large_to_be_a_typed_list_is_absent(folders, monkeypatch):
    put(folders, "NIFTYIT.txt", "AAA\n")
    monkeypatch.setattr(typed, "MAX_BYTES", 2)
    monkeypatch.setattr(typed.logger, "exception", lambda *a, **k: None)
    assert typed.load(SPEC["niftyit"]) is None


# --------------------------------------------------------------------------------- over 250


def test_a_list_over_250_carries_the_window_line_and_copy_cuts_at_250(client, box, folders):
    names = [f"S{n:03d}" for n in range(300)]
    put(folders, "NIFTY500.txt", "# as of 2026-09-30\n" + "\n".join(names) + "\n")
    box.snap, _ = snapshot_of(nse(*names), datetime(2026, 11, 9, 8, 40))
    data = view(client, "nifty500").get_json()["data"]
    assert data["total"] == 300
    assert data["lines"] == [
        "Built-in list, locked. NIFTY 500 as of 30 Sep 2026: 300 symbols.", words.WINDOW]  # fmt: skip
    r = client.post(f"{API}/copy", json={"from": {"ref": "preset:nifty500"},
                                         "to": {"name": "NIFTY 500 (my copy)"}},
                    headers=client.headers)  # fmt: skip
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["words"] == words.truncated(250, 300)
    assert words.truncated(250, 300) == (
        "Copied the first 250 of 300, in the order shown. A list holds 250.")  # fmt: skip


# ------------------------------------------------------------------- the exception's limits


def test_a_typed_list_is_one_generation_dropped_with_the_master(client, box, folders):
    put(folders, "NIFTYIT.txt", "AAA\n")
    box.snap, _ = snapshot_of(nse("AAA"), datetime(2026, 11, 9, 8, 40))
    view(client, "niftyit")
    view(client, "nifty50")
    assert len(reader._prepared) == 1 and master.generations_alive() == 1
    stamp, prepared = reader._prepared[master.shared().generation()]["niftyit"]
    assert stamp is not None and all(r is box.snap.by_symbol_exchange[("AAA", "NSE")]
                                     for r in prepared.rows)  # fmt: skip
    box.snap = None
    master.shared().generation()
    del stamp, prepared
    gc.collect()
    assert len(reader._prepared) == 0 and master.generations_alive() == 0


def test_no_thread_starts_for_a_typed_list_or_a_changed_file(client, box, folders, monkeypatch):
    put(folders, "NIFTYIT.txt", "AAA\n")
    box.snap, _ = snapshot_of(nse("AAA"), datetime(2026, 11, 9, 8, 40))
    before = {t.ident for t in threading.enumerate()}
    monkeypatch.setattr(threading.Thread, "start", lambda self: pytest.fail("a thread started"))
    view(client, "niftyit")
    put(folders, "NIFTYIT.txt", "AAA\nBBB\n", when=1_234_567_890_000)
    view(client, "niftyit")
    client.get(f"{API}/lists", headers=client.headers)
    assert {t.ident for t in threading.enumerate()} == before


def test_nothing_is_downloaded_and_the_module_reads_only_files():
    import ast

    tree = ast.parse((Path(typed.__file__)).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"httpx", "requests", "urllib", "socket", "http", "aiohttp", "flask"}
    assert contract  # the contract module is imported for the ids above
