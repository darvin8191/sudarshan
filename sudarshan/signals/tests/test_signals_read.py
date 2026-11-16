"""T1: reading one document (contract.read_document), pure.

The golden vectors (``vectors/documents.json``, every document invented) hold every key at its
limit and one past it, unknown keys, an unknown module, an unknown version and a state with a
lower-case letter. A seeded fuzz holds the property of design 10.6: any byte string up to 64 KiB
never raises, and nothing reaches the caller as a snapshot unless it validates."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from sudarshan.signals import contract as c

VECTORS = json.loads((Path(__file__).parent / "vectors" / "documents.json").read_text("utf-8"))
REASONS = {
    "size", "utf8", "json", "object", "v", "run", "seq", "at", "next_by", "name", "stopping",
    "signals", "module", "instrument", "state", "label", "bias", "horizon", "quality", "score",
    "confidence", "as_of", "since", "valid_until", "levels", "inputs", "value", "why", "alerts",
    "id", "level", "title", "text", "lang", "duplicate",
}  # fmt: skip


def _raw(case: dict) -> bytes:
    if "raw_hex" in case:
        return bytes.fromhex(case["raw_hex"].replace(" ", ""))
    if "raw" in case:
        return case["raw"].encode("utf-8")
    return json.dumps(case["doc"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[k["name"] for k in VECTORS["cases"]])
def test_vector(case):
    got = c.read_document(
        _raw(case), now_ms=case["now_ms"], written_ms=case.get("written_ms"), name=case.get("file")
    )
    expect = case["expect"]
    if expect == "accepted":
        assert isinstance(got, c.Snapshot), got
    elif expect == "newer_format":
        assert isinstance(got, c.NewerFormat) and got.v > 1
    else:
        assert isinstance(got, c.Refusal), got
        assert got.reason == expect.split(":", 1)[1], got
        return
    check = case.get("check", {})
    if not isinstance(got, c.Snapshot):
        return
    if "readings" in check:
        assert len(got.readings) == check["readings"]
    if "alerts" in check:
        assert len(got.alerts) == check["alerts"]
    if "skipped" in check:
        assert got.skipped == check["skipped"]
    if "clock" in check:
        assert got.clock == check["clock"]
    if "stopping" in check:
        assert got.stopping is check["stopping"]
    if "labels" in check:
        assert [r.label for r in got.readings] == check["labels"]
    if "modules" in check:
        assert [r.module for r in got.readings] == check["modules"]
    if "why_en" in check:
        assert got.readings[0].why["en"] == check["why_en"]


def test_every_refusal_names_a_rule_and_never_the_documents_text():
    for case in VECTORS["cases"]:
        got = c.read_document(_raw(case), now_ms=case["now_ms"], name=case.get("file"))
        if isinstance(got, c.Refusal):
            assert got.reason in REASONS, got
            assert got.where == "" or got.where.split("[")[0] in ("signals", "alerts"), got


def test_the_base_snapshot_in_full():
    case = VECTORS["cases"][0]
    snap = c.read_document(_raw(case), now_ms=case["now_ms"])
    assert isinstance(snap, c.Snapshot)
    at = VECTORS["base_at"]
    assert (snap.run, snap.seq, snap.at, snap.next_by, snap.stopping) == (
        "2026-11-16T03:10:00Z",
        123,
        at,
        at + 300_000,
        False,
    )
    regime, gap = snap.readings
    assert regime.key == ("regime", "NIFTY") and gap.key == ("gap_bias", "BANKNIFTY")
    assert regime.as_record() == {
        "module": "regime", "instrument": "NIFTY", "state": "DISTRIBUTION",
        "label": "FII distribution", "bias": "bear", "score": -0.42, "confidence": 0.71,
        "since_ms": at - 10_712_250, "as_of_ms": at - 12_250, "valid_until_ms": None,
        "horizon": "swing", "quality": "ok",
        "levels": {"trigger": 25900.0, "invalid": 26120.0, "target": None},
        "inputs": [{"name": "FII index futures, net", "value": "-1.2 lakh contracts"}],
        "why": {"en": "Invented explanation for a test.", "hi": "Test ke liye banaya gaya.",
                "gu": None},
    }  # fmt: skip
    assert gap.levels is None and gap.inputs == () and gap.why == {"en": None, "hi": None,
                                                                   "gu": None}  # fmt: skip
    (alert,) = snap.alerts
    assert (alert.id, alert.level, alert.lang, alert.instrument) == (
        "a-20261116-0007",
        "watch",
        "en",
        "NIFTY",
    )
    assert snap.clock_ok and snap.published_ms == at
    assert snap.fresh_until_ms == at + 300_000 + c.FRESH_GRACE_MS
    assert snap.size == len(_raw(case))


def test_a_differing_clock_moves_the_promise_to_the_servers_clock():
    """README section 4: the document is shown at the time it was written and stays fresh for
    its own promised interval from then; its ``at`` decides nothing."""
    case = VECTORS["cases"][0]
    at = VECTORS["base_at"]
    written = at - 600_000  # TRINETRA's clock is ten minutes ahead
    snap = c.read_document(_raw(case), now_ms=written + 500, written_ms=written)
    assert isinstance(snap, c.Snapshot) and snap.clock == "ahead"
    assert snap.clock_diff_ms == 600_000
    assert snap.published_ms == written
    assert snap.fresh_until_ms == written + 300_000 + c.FRESH_GRACE_MS


def test_cleaning():
    assert c.clean_line(" a\tb\nc\x00d\x7fe\x9f  f ") == "a b cde  f"
    assert c.clean_line("‮abc⁦d⁩﻿") == "abcd"
    # the joiners of Indian scripts stay (a conjunct is spelled with them)
    assert c.clean_line("ક્‍ષ") == "ક્‍ષ"
    assert c.clean_text("One.\r\nTwo.\rThree. Four.\x0b") == "One.\nTwo.\nThree.\nFour."


@pytest.mark.parametrize("seed", range(6))
def test_any_bytes_never_raise_and_only_a_valid_document_is_a_snapshot(seed):
    rng = random.Random(seed)
    base = _raw(VECTORS["cases"][0])
    for _ in range(300):
        pick = rng.random()
        if pick < 0.3:
            raw = rng.randbytes(rng.randint(0, 64 * 1024))
        elif pick < 0.8:  # a valid document with bytes flipped, cut or repeated
            raw = bytearray(base)
            for _ in range(rng.randint(1, 8)):
                at = rng.randrange(len(raw))
                raw[at] = rng.randrange(256)
            if rng.random() < 0.3:
                raw = raw[: rng.randrange(len(raw))]
            if rng.random() < 0.2:
                raw = raw * rng.randint(2, 40)
            raw = bytes(raw)
        else:  # a valid document with a value swapped for another type
            doc = json.loads(base)
            key = rng.choice(["v", "run", "seq", "at", "next_by", "signals", "alerts"])
            doc[key] = rng.choice([None, True, 1.5, -3, "x", [], {}, [None], {"a": []}])
            raw = json.dumps(doc).encode()
        got = c.read_document(raw, now_ms=VECTORS["base_at"])
        assert isinstance(got, c.Snapshot | c.Refusal | c.NewerFormat)
        if isinstance(got, c.Snapshot):
            # it validates: read again, it is the same, and every rule holds on what came out
            assert c.read_document(raw, now_ms=VECTORS["base_at"]) == got
            assert len(raw) <= c.DOC_LIMITS["bytes"] and len(got.readings) <= 16
            assert all(r.instrument in c.INSTRUMENTS for r in got.readings)
            assert all(len(r.label) <= 40 and r.label for r in got.readings)


def test_a_snapshot_is_never_built_from_a_document_larger_than_16_kib():
    case = VECTORS["cases"][0]
    raw = _raw(case)
    padded = raw[:-1] + b',"pad":"' + b"p" * (c.DOC_LIMITS["bytes"] - len(raw) - 9) + b'"}'
    assert len(padded) == c.DOC_LIMITS["bytes"]
    assert isinstance(c.read_document(padded, now_ms=case["now_ms"]), c.Snapshot)
    over = padded[:-2] + b'p"}'
    assert c.read_document(over, now_ms=case["now_ms"]) == c.Refusal("size")
