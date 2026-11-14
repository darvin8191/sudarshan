"""Generator of year.json (T4): a taxpayer's capital-gains schedule for a year, by hand.

Imports nothing from sudarshan. A gain row is (term, day of sale, rate in basis points, gain in
rupees; a loss is negative). Rupees in the notes and in ``g``; the file holds paise.
Run: python3 make_year.py
"""

import json
from pathlib import Path

LAKH = 100_000


def g(term, day, rate, rupees, **more):
    paise = None if rupees is None else rupees * 100
    return {"term": term, "sale_day": day, "rate_bp": rate, "gain_paise": paise, **more}


def carry(loss_fy, bucket, rupees, source="owner"):
    return {"taxpayer": 1, "loss_fy": loss_fy, "bucket": bucket, "paise": rupees * 100,
            "source": source}  # fmt: skip


def carried(loss_fy, bucket, rupees, last, source="computed"):
    return {"loss_fy": loss_fy, "bucket": bucket, "paise": rupees * 100, "source": source,
            "last_fy": last}  # fmt: skip


def case(name, note, fy, gains, st, lt, tax, cess, exempt_used, carried=(), lapsed=(), **more):
    out = {"taxable": {"st_paise": st * 100, "lt_paise": lt * 100}, "tax_paise": tax * 100,
           "cess_paise": cess * 100, "exempt_used_paise": exempt_used * 100,
           "carried": list(carried), "lapsed": list(lapsed)}  # fmt: skip
    out.update({key: more.pop(key) for key in list(more) if key.startswith("out_")})
    return {"id": name, "note": note, "in": {"fy": fy, "gains": gains, **more}, "out": out}


L26, S26 = ("long", 20260812, 1250), ("short", 20260812, 2000)
assert 210_000 - 30_000 - 125_000 == 55_000 and 55_000 * 125 // 1000 == 6_875  # Y1
assert 40_000 * 20 // 100 == 8_000 and (6_875 + 8_000) * 4 // 100 == 595  # Y1
assert 200_000 - 50_000 - 125_000 == 25_000 and 25_000 * 125 // 1000 == 3_125  # Y2
assert 25_000 * 10 // 100 + 30_000 * 10 // 100 == 5_500 and 55_000 * 125 // 1000 == 6_875  # Y4
assert 300_000 - 50_000 - 30_000 - 125_000 == 95_000 and 95_000 * 125 // 1000 == 11_875  # Y5
assert 20_000 * 20 // 100 + 50_000 * 15 // 100 == 11_500  # Y9, highest rate first
assert 20_000 * 15 // 100 + 50_000 * 20 // 100 == 13_000  # Y9, chronological

Y4 = [g("long", 20240610, 1000, 80_000), g("long", 20240910, 1250, 100_000)]
Y9 = [g("short", 20240610, 1500, 50_000), g("short", 20240910, 2000, 50_000),
      g("short", 20241105, 2000, -30_000)]  # fmt: skip

CASES = [
    case("Y1", "long-term gains 1,50,000 + 60,000 = 2,10,000, long-term loss 30,000, short-term "
         "gain 40,000: net long 1,80,000 less exemption 1,25,000 = 55,000 at 12.5% = 6,875; "
         "short 40,000 at 20% = 8,000; cess 4% of 14,875 = 595", 2026,
         [g(*L26, 150_000), g(*L26, 60_000), g(*L26, -30_000), g(*S26, 40_000)],
         40_000, 55_000, 14_875, 595, 125_000),
    case("Y2", "short-term loss 50,000 with no short-term gain goes against long-term gain "
         "2,00,000: 1,50,000 less 1,25,000 = 25,000 at 12.5% = 3,125; cess 125", 2026,
         [g(*S26, -50_000), g(*L26, 200_000)], 0, 25_000, 3_125, 125, 125_000),
    case("Y3", "short-term gain 60,000 and long-term loss 40,000: a long-term loss never reduces "
         "short-term gain: 60,000 at 20% = 12,000, cess 480; the 40,000 is carried, usable to "
         "FY 2034-35", 2026, [g(*S26, 60_000), g(*L26, -40_000)], 60_000, 0, 12_000, 480, 0,
         [carried(2026, "ltcl", 40_000, 2034)]),
    case("Y4", "FY 2024-25: long-term 80,000 before 23 Jul 2024 (10%) and 1,00,000 after "
         "(12.5%); the exemption 1,25,000 takes the 12.5% part first (1,00,000) and 25,000 of "
         "the 10% part: 55,000 at 10% = 5,500; cess 220", 2024, Y4, 0, 55_000, 5_500, 220,
         125_000, out_by_rate=[{"rate_bp": 1250, "paise": 10_000_000},
                               {"rate_bp": 1000, "paise": 2_500_000}]),
    case("Y4-chronological", "the other order: 80,000 of the earlier part gone, 45,000 of the "
         "later: 55,000 at 12.5% = 6,875; cess 275", 2024, Y4, 0, 55_000, 6_875, 275, 125_000,
         settings={"exempt_order": "chronological"},
         out_by_rate=[{"rate_bp": 1000, "paise": 8_000_000},
                      {"rate_bp": 1250, "paise": 4_500_000}]),
    case("Y5-brought-forward", "short gain 1,00,000, long gain 3,00,000; brought forward: "
         "long-term loss of FY 2021-22 50,000 (oldest, against long: 2,50,000), short-term loss "
         "of FY 2022-23 1,30,000 (1,00,000 against short, 30,000 against long: 2,20,000), and a "
         "short-term loss of FY 2017-18 whose 8 years ended with FY 2025-26: lapsed, not used. "
         "2,20,000 less 1,25,000 = 95,000 at 12.5% = 11,875; cess 475", 2026,
         [g(*S26, 100_000), g(*L26, 300_000)], 0, 95_000, 11_875, 475, 125_000,
         lapsed=[carried(2017, "stcl", 20_000, 2025, "owner")],
         carries=[carry(2022, "stcl", 130_000), carry(2021, "ltcl", 50_000),
                  carry(2017, "stcl", 20_000), carry(2023, "business", 99_000)]),
    case("Y6-loss-before-exemption", "long gain 1,50,000 and long loss 1,00,000: the loss comes "
         "first (50,000 left), then the exemption uses only 50,000: nothing taxable and nothing "
         "carried. (Exemption first would wrongly carry 75,000.)", 2026,
         [g(*L26, 150_000), g(*L26, -100_000)], 0, 0, 0, 0, 50_000),
    case("Y7-more-loss-than-gain", "short loss 5,00,000 against short gain 1,00,000 and long "
         "gain 2,00,000: both nil, exemption unused, 2,00,000 short-term loss carried to "
         "FY 2034-35", 2026, [g(*S26, -500_000), g(*S26, 100_000), g(*L26, 200_000)],
         0, 0, 0, 0, 0, [carried(2026, "stcl", 200_000, 2034)]),
    case("Y8-old-loss-partly-used", "long gain 1,00,000; a long-term loss of FY 2020-21 of "
         "3,00,000 is set off before the exemption (1,00,000 used, exemption unused); 2,00,000 "
         "stays, usable to FY 2028-29. A loss of FY 2018-19 of 10,000 is older and goes first; "
         "it is used in full in its last year", 2026, [g(*L26, 100_000)], 0, 0, 0, 0, 0,
         [carried(2020, "ltcl", 210_000, 2028, "owner")],
         carries=[carry(2020, "ltcl", 300_000), carry(2018, "ltcl", 10_000)]),
    case("Y8-last-year-unused", "a long-term loss of FY 2018-19 of 3,00,000 in its last year "
         "(FY 2026-27) against long gain 1,00,000: 2,00,000 cannot go further and is listed as "
         "lapsed, not carried", 2026, [g(*L26, 100_000)], 0, 0, 0, 0, 0,
         lapsed=[carried(2018, "ltcl", 200_000, 2026, "owner")],
         carries=[carry(2018, "ltcl", 300_000)]),
    case("Y9-split-year-loss", "FY 2024-25: short gains 50,000 at 15% and 50,000 at 20%, short "
         "loss 30,000: against the 20% part first: 20,000 at 20% + 50,000 at 15% = 4,000 + "
         "7,500 = 11,500; cess 460", 2024, Y9, 70_000, 0, 11_500, 460, 0),
    case("Y9-chronological", "the other order: 20,000 at 15% + 50,000 at 20% = 3,000 + 10,000 = "
         "13,000; cess 520", 2024, Y9, 70_000, 0, 13_000, 520, 0,
         settings={"exempt_order": "chronological"}),
    case("Y10-left-out-rows", "long gain 2,00,000 and a buyback loss 40,000 (its receipt 60,000 "
         "a dividend); a sale that needs the 2018 price, one with no purchase record and an "
         "exempt buyback are listed and in no total: 1,60,000 less 1,25,000 = 35,000 at 12.5% "
         "= 4,375; cess 175", 2026,
         [g(*L26, 200_000), g(*L26, -40_000, dividend_paise=6_000_000),
          g(*L26, None, status="needs_fmv"), g(None, 20260812, None, None, status="unknown_cost"),
          g(*L26, None, status="exempt_buyback")],
         0, 35_000, 4_375, 175, 125_000,
         out_lists={"needs_fmv": 1, "unknown_cost": 1, "exempt_buybacks": 1, "schedule": 2},
         out_buyback_dividend_paise=6_000_000),
    case("Y11-other-years-ignored", "a gain sold on 31 Mar 2026 belongs to FY 2025-26 and one "
         "on 1 Apr 2027 to FY 2027-28: FY 2026-27 has only the 30,000 short-term gain: 6,000; "
         "cess 240", 2026,
         [g("short", 20260331, 2000, 900_000), g("short", 20260401, 2000, 30_000),
          g("short", 20270401, 2000, 900_000)], 30_000, 0, 6_000, 240, 0),
    case("Y12-own-loss-first", "short gain 1,00,000; this year's short loss 1,00,000 and a "
         "short-term loss of FY 2020-21 of 1,00,000: the year's own loss is used first, so the "
         "old one stays whole, usable to FY 2028-29 (the other way round would carry this "
         "year's to FY 2034-35)", 2026, [g(*S26, 100_000), g(*S26, -100_000)], 0, 0, 0, 0, 0,
         [carried(2020, "stcl", 100_000, 2028, "owner")], carries=[carry(2020, "stcl", 100_000)]),
    case("Y13-old-long-loss-short-gain", "short gain 50,000 and a long-term loss of FY 2022-23 "
         "of 80,000 brought forward: it cannot touch short-term gain: 50,000 at 20% = 10,000, "
         "cess 400; the 80,000 stays, usable to FY 2030-31", 2026, [g(*S26, 50_000)],
         50_000, 0, 10_000, 400, 0, [carried(2022, "ltcl", 80_000, 2030, "owner")],
         carries=[carry(2022, "ltcl", 80_000)]),
    case("Y14-fy-2023-24", "FY 2023-24, before the change: the exemption was 1,00,000 and the "
         "rates 10% and 15%: long 1,50,000 less 1,00,000 = 50,000 at 10% = 5,000; short 20,000 "
         "at 15% = 3,000; cess 320", 2023,
         [g("long", 20231110, 1000, 150_000), g("short", 20231110, 1500, 20_000)],
         20_000, 50_000, 8_000, 320, 100_000),
]  # fmt: skip
assert 123_456 * 5 // 10 == 61_728 and 123_456 * 3 // 10 == 37_036 and 123_456 * 2 // 10 == 24_691
ALLOCATE = [{
    "id": "A1", "note": "1,234.56 of the year's non-STT charges over delivery sales of 50,000, "
    "30,000 and 20,000: 61,728 / 37,036.8 / 24,691.2 paise; floors add to 1,23,455 and the "
    "spare paisa goes to the .8: 61,728 / 37,037 / 24,691. A buyback and a sale of another "
    "year take nothing",
    "in": {"fy": 2026, "total_paise": 123_456,
           "sales": [[20260812, 5_000_000, None], [20260901, 3_000_000, None],
                     [20260715, 6_000_000, "buyback"], [20260331, 9_000_000, None],
                     [20270105, 2_000_000, None]]},
    "out": [61_728, 37_037, 0, 0, 24_691],
}]  # fmt: skip

FILES = {"year.json": {"schedule": CASES, "allocate": ALLOCATE}}

if __name__ == "__main__":
    for name, data in FILES.items():
        (Path(__file__).parent / name).write_text(json.dumps(data, indent=1) + "\n")
