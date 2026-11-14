"""Generator of losses.json (T6): the two business buckets after set-off, and what is carried.

Imports nothing from sudarshan. ``ns`` is the year's net from derivatives (non-speculative) and
``sp`` the net from intraday shares (speculative), in rupees; the file holds paise. S3, S6 and
S13 rest on an order the sources do not settle and are marked "verify with CA".
Run: python3 make_losses.py
"""

import json
from pathlib import Path


def carry(loss_fy, bucket, rupees, source="owner"):
    return {"taxpayer": 1, "loss_fy": loss_fy, "bucket": bucket, "paise": rupees * 100,
            "source": source}  # fmt: skip


def row(loss_fy, bucket, rupees, last, source="owner"):
    return {"loss_fy": loss_fy, "bucket": bucket, "paise": rupees * 100, "source": source,
            "last_fy": last}  # fmt: skip


def case(name, note, ns, sp, taxable_ns, taxable_sp, carries=(), carried=(), lapsed=(), other=0,
         own=(), fy=2026, says=()):  # fmt: skip
    given = {"fy": fy, "non_speculative_net": ns * 100, "speculative_net": sp * 100,
             "carries": list(carries)}  # fmt: skip
    out = {"taxable": {"non_speculative_paise": taxable_ns * 100,
                       "speculative_paise": taxable_sp * 100},
           "available_other_heads_paise": other * 100, "carried": list(carried),
           "lapsed": list(lapsed), "own_figures": list(own), "says": list(says)}  # fmt: skip
    return {"id": name, "note": note, "in": given, "out": out}


assert -180_000 + 25_000 == -155_000 and 2026 + 8 == 2034 and 2024 + 4 == 2028  # S1
assert 40_000 - 25_000 == 15_000 and 2021 + 4 == 2025  # S3, S4
assert 100_000 - 70_000 == 30_000 and 45_000 - 20_000 == 25_000 and 30_000 - 25_000 == 5_000
assert 50_000 - 30_000 == 20_000 and 40_000 - 20_000 == 20_000  # S13

CASES = [
    case("S1", "derivatives -1,80,000, intraday +25,000, intraday loss of FY 2024-25 of 40,000 "
         "brought forward. This year first: the derivative loss takes the 25,000 (1,55,000 "
         "left). No intraday profit is left, so the 40,000 stays (to FY 2028-29). 1,55,000 is "
         "available against other income except salary, else carried to FY 2034-35",
         -180_000, 25_000, 0, 0, [carry(2024, "speculative", 40_000)],
         [row(2024, "speculative", 40_000, 2028),
          row(2026, "business", 155_000, 2034, "computed")], other=155_000,
         says=["This year's F&O (business) loss: Rs 25,000 set against this year's intraday profit.",
               "Available against other income this year except salary: Rs 1,55,000; what is not "
               "used is carried, against business income only.",
               "F&O (business) loss of FY 2026-27: Rs 1,55,000 carried 8 years (to FY 2034-35), "
               "against business income only."]),
    case("S2", "intraday -30,000 and derivatives +50,000: a speculative loss reduces nothing "
         "else: 50,000 taxable; 30,000 carried 4 years, to FY 2030-31", 50_000, -30_000,
         50_000, 0, carried=[row(2026, "speculative", 30_000, 2030, "computed")],
         says=["An intraday (speculative) loss cannot reduce any other income.",
               "Intraday (speculative) loss of FY 2026-27: Rs 30,000 carried 4 years (to "
               "FY 2030-31), against intraday (speculative) profit only."]),
    case("S3", "VERIFY WITH CA. Intraday +25,000, no loss this year; brought forward from "
         "FY 2024-25: intraday loss 40,000 and business loss 60,000. The intraday loss goes "
         "first: 25,000 used, 15,000 left; the business loss stays 60,000 as no business "
         "income is left", 0, 25_000, 0, 0,
         [carry(2024, "business", 60_000), carry(2024, "speculative", 40_000)],
         [row(2024, "speculative", 15_000, 2028), row(2024, "business", 60_000, 2032)],
         says=["Order used: this year's losses first, then earlier years' oldest first, an "
               "intraday (speculative) loss before a business loss. Verify with your CA."]),
    case("S4", "an intraday loss of FY 2021-22 offered in FY 2026-27: its 4 years ended with "
         "FY 2025-26, so it is listed as lapsed and the 25,000 profit is taxable in full",
         0, 25_000, 0, 25_000, [carry(2021, "speculative", 40_000)],
         lapsed=[row(2021, "speculative", 40_000, 2025)],
         says=["Intraday (speculative) loss of FY 2021-22: Rs 40,000 could be used up to "
               "FY 2025-26. It has lapsed."]),
    case("S4-last-year", "the same loss of FY 2022-23 is in its last year: 25,000 used; the "
         "15,000 left cannot go further and is listed as lapsed", 0, 25_000, 0, 0,
         [carry(2022, "speculative", 40_000)], lapsed=[row(2022, "speculative", 15_000, 2026)]),
    case("S5", "the owner entered a business loss of FY 2025-26 of 70,000 and SUDARSHAN "
         "computed 65,000 for the same year: the owner's is used (1,00,000 - 70,000 = 30,000 "
         "taxable) and the computed figure is only shown beside it", 100_000, 0, 30_000, 0,
         [carry(2025, "business", 65_000, "computed"), carry(2025, "business", 70_000)],
         own=[row(2025, "business", 65_000, 2033, "computed")],
         says=["SUDARSHAN's own figure for the F&O (business) loss of FY 2025-26 is Rs 65,000; "
               "yours is used."]),
    case("S5-computed-alone", "with no owner figure for that year the computed one is used: "
         "1,00,000 - 65,000 = 35,000", 100_000, 0, 35_000, 0,
         [carry(2025, "business", 65_000, "computed")]),
    case("S6", "VERIFY WITH CA. Derivatives +20,000, intraday +30,000, business loss of "
         "FY 2023-24 of 45,000: 20,000 against derivative profit, then 25,000 against intraday "
         "profit (business income of any kind): 5,000 intraday profit taxable",
         20_000, 30_000, 0, 5_000, [carry(2023, "business", 45_000)]),
    case("S7", "an intraday loss brought forward never reduces derivative profit: 50,000 "
         "taxable, the 40,000 of FY 2025-26 stays (to FY 2029-30)", 50_000, 0, 50_000, 0,
         [carry(2025, "speculative", 40_000)], [row(2025, "speculative", 40_000, 2029)]),
    case("S8", "a business loss of FY 2017-18 in FY 2026-27: 8 years ended with FY 2025-26: "
         "lapsed", 50_000, 0, 50_000, 0, [carry(2017, "business", 40_000)],
         lapsed=[row(2017, "business", 40_000, 2025)]),
    case("S9", "both profits and nothing brought forward: both taxable as they are", 50_000,
         25_000, 50_000, 25_000),
    case("S10", "derivatives -10,000 and intraday +25,000: 15,000 intraday profit taxable, "
         "nothing carried", -10_000, 25_000, 0, 15_000),
    case("S11", "both losses: nothing meets anything; the derivative loss 80,000 is available "
         "against other income, else carried to FY 2034-35; the intraday 20,000 to FY 2030-31",
         -80_000, -20_000, 0, 0,
         carried=[row(2026, "business", 80_000, 2034, "computed"),
                  row(2026, "speculative", 20_000, 2030, "computed")], other=80_000),
    case("S12-oldest-first", "derivatives +50,000; business losses of FY 2024-25 (40,000) and "
         "FY 2022-23 (30,000): the older goes first in full, then 20,000 of the newer; 20,000 "
         "of FY 2024-25 stays (to FY 2032-33)", 50_000, 0, 0, 0,
         [carry(2024, "business", 40_000), carry(2022, "business", 30_000)],
         [row(2024, "business", 20_000, 2032)]),
    case("S13", "VERIFY WITH CA. Derivatives -30,000, intraday +50,000, intraday loss of "
         "FY 2024-25 of 40,000: this year's loss first (intraday 20,000 left), then 20,000 of "
         "the old loss; 20,000 of it stays (to FY 2028-29). The other way round would carry "
         "20,000 of this year's instead", -30_000, 50_000, 0, 0,
         [carry(2024, "speculative", 40_000)], [row(2024, "speculative", 20_000, 2028)]),
    case("S14-capital-losses-ignored", "capital losses brought forward are not business "
         "losses: 50,000 taxable", 50_000, 0, 50_000, 0,
         [carry(2024, "stcl", 40_000), carry(2024, "ltcl", 40_000)]),
]  # fmt: skip

FILES = {"losses.json": CASES}

if __name__ == "__main__":
    for name, data in FILES.items():
        (Path(__file__).parent / name).write_text(json.dumps(data, indent=1) + "\n")
