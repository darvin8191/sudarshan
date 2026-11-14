"""M18.11, M19.10: the financial-year archive, kept 8 years (P8 6.6, 10.8).

The first complete archive made from 4 April on is the financial year's archive that ended on
31 March: by then the year's Tax pack and March's charges exist (review M3; an archive of
31 March's evening held neither). ``retention.plan`` keeps the newest eight of them whatever the daily and monthly counts
say, and the size cap never takes one: it says ``over_cap`` instead. Its offsite copy goes up
under a name of its own, so the bucket's rules for the nightly names never expire it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from backup_fakes import Server, ist

from sudarshan.ops import backup, offsite, retention
from sudarshan.ops.retention import Backup, fy_label, parse, plan, yearlies, yearly_label

VECTORS = json.loads(
    (Path(__file__).with_name("retention_yearly_vectors.json")).read_text(encoding="utf-8")
)
MB = 1 << 20


def _archive(stamp: str, size: int = MB) -> Backup:
    found = parse(f"sudarshan-backup-{stamp}.tar.gz", size)
    assert found is not None, stamp
    return found


@pytest.mark.parametrize(("when", "label", "why"), VECTORS["labels"], ids=lambda v: str(v)[:40])
def test_the_financial_year_an_archive_follows(when, label, why):
    assert fy_label(datetime.strptime(when, "%Y-%m-%d %H:%M:%S")) == label, why


@pytest.mark.parametrize("vector", VECTORS["yearlies"], ids=lambda v: v["name"])
def test_which_archive_is_each_years(vector):
    found = yearlies([_archive(stamp) for stamp in reversed(vector["archives"])])
    assert {label: b.name for label, b in found.items()} == {
        label: f"sudarshan-backup-{stamp}.tar.gz" for label, stamp in vector["yearly"].items()
    }


def _years(first_year: int, count: int) -> list[Backup]:
    """For each year: the archive of 4 April 16:30 and the two nights after it."""
    found = []
    for year in range(first_year, first_year + count):
        start = datetime(year, 4, 4, 16, 30)
        found += [_archive((start + timedelta(days=n)).strftime("%Y%m%d-%H%M%S")) for n in range(3)]
    return found


def test_a_years_archive_outlives_the_daily_and_monthly_counts():
    archives = _years(2027, 3) + [_archive("20300110-163000")]
    kept = plan(archives, daily=1, monthly=1, yearly=8).keep
    assert [b.name[17:25] for b in kept] == ["20300110", "20290404", "20280404", "20270404"]
    without = plan(archives, daily=1, monthly=1).keep
    assert [b.name[17:25] for b in without] == ["20300110"], "yearly=0 keeps the old rules"


def test_eight_years_are_kept_and_the_ninth_goes():
    archives = _years(2027, 10)
    kept = {b.name[17:25] for b in plan(archives, daily=1, monthly=0, yearly=8).keep}
    assert {f"{year}0404" for year in range(2029, 2037)} <= kept
    assert "20270404" not in kept and "20280404" not in kept


def test_the_cap_never_takes_a_years_archive_and_says_so_instead():
    yearly = [_archive(f"{year}0404-163000", 3 * MB) for year in (2027, 2028, 2029)]
    nights = [_archive(f"202911{day:02d}-163000", MB) for day in range(1, 11)]
    settled = plan(yearly + nights, daily=30, monthly=0, yearly=8, cap_bytes=12 * MB)
    kept = [b.name for b in settled.keep]
    assert all(b.name in kept for b in yearly)
    assert len(kept) == 3 + retention.PROTECT_NEWEST and settled.over_cap is False
    tight = plan(yearly + nights, daily=30, monthly=0, yearly=8, cap_bytes=10 * MB)
    assert all(b.name in [k.name for k in tight.keep] for b in yearly)
    assert tight.over_cap is True, "the caller is told; nothing more is removed"


def test_yearly_label_names_only_the_years_own_archive():
    archives = [_archive("20270331-163000"), _archive("20270404-163000"),
                _archive("20270405-163000")]  # fmt: skip
    assert yearly_label(archives, archives[0].name) is None  # before the year's papers exist
    assert yearly_label(archives, archives[1].name) == "2026-27"
    assert yearly_label(archives, archives[2].name) is None
    assert yearly_label(archives, "sudarshan-backup-20990101-000000.tar.gz") is None


def test_the_offsite_name_of_a_years_archive_is_its_own():
    nightly = "sudarshan-backup-20270404-163102.tar.gz"
    assert (
        offsite.object_name(nightly, "v0.13.1", "2026-27")
        == "sudarshan-fy-2026-27-20270404-163102-v0.13.1.tar.gz.cms"
    )
    assert offsite.object_name(nightly, "v0.13.1").startswith(offsite.NIGHTLY_PREFIX)
    assert not offsite.object_name(nightly, None, "2026-27").startswith(offsite.NIGHTLY_PREFIX)
    partial = "sudarshan-backup-20270404-163102-partial.tar.gz"
    assert offsite.object_name(partial, "v0.13.1", "2026-27").startswith(offsite.NIGHTLY_PREFIX)
    for odd in ("2026", "../x", "2026-2027", ""):
        assert offsite.object_name(nightly, "v0.13.1", odd).startswith(offsite.NIGHTLY_PREFIX)


def test_the_job_keeps_the_years_archive_and_sends_it_under_its_own_name(tmp_path, monkeypatch):
    server = Server(tmp_path)
    server.populate()
    settings = server.settings(keep_daily=1, keep_monthly=0, min_gap_s=0)
    assert settings.keep_yearly == 8
    sent = []

    def fake_send(**given):
        sent.append((given["archive"].name, given["fy"]))
        return offsite.Outcome("ok")

    monkeypatch.setattr(offsite, "send", fake_send)
    for night in (ist(2027, 3, 31), ist(2027, 4, 3), ist(2027, 4, 4), ist(2027, 4, 5),
                  ist(2027, 4, 6)):  # fmt: skip
        assert backup.run(settings, clock=lambda n=night: n).outcome == backup.OK
    # 31 March's and 3 April's are ordinary nights: the year's is the first from 4 April.
    assert [fy for _, fy in sent] == [None, None, "2026-27", None, None]
    left = sorted(b.name[17:25] for b in backup.list_archives(settings.archive_dir))
    assert left == ["20270404", "20270406"]


def test_the_years_archive_is_made_after_the_journals_own_papers():
    """Review M3: the year's archive was 31 March 16:30, about 15 hours before the journal
    builds the year's Tax pack (1 April 07:00) and before March's charges are asked for (from
    the 3rd). The rule is held to the journal's own timetable, so neither can move alone."""
    from sudarshan.journal import schedule
    from sudarshan.journal.jobs import charges

    month, day, hour = retention.FY_FROM
    whole = datetime(2027, month, day, hour)
    pack_month, pack_day, pack_minute = schedule.FY_AT
    pack = datetime(2027, pack_month, pack_day) + timedelta(minutes=pack_minute)
    march_asked = datetime(2027, 4, charges.MONTH_FROM_DAY, 22, 0)  # the 22:00 catch-up
    assert whole > pack and whole > march_asked
    assert fy_label(pack - timedelta(minutes=1)) is None  # an archive made before the pack
    assert fy_label(pack + timedelta(hours=1)) is None  # the pack alone is not the year
    assert fy_label(march_asked + timedelta(minutes=30)) is None
    assert fy_label(whole) == "2026-27"
    # The window is the builder's 92 days, counted from there.
    assert retention.FY_WINDOW_DAYS == 92
    assert fy_label(whole + timedelta(days=91, hours=23)) == "2026-27"
    assert fy_label(whole + timedelta(days=92)) is None


def _status(settings) -> dict:
    return json.loads(settings.status_file.read_text(encoding="utf-8"))


def test_a_years_archive_whose_upload_failed_is_sent_again_under_the_years_name(
    tmp_path, monkeypatch
):
    """Review L4: the upload of the year's archive failed on its night, and the next night sent
    only that night's archive, so the copy kept 8 years was never offsite and nothing said so."""
    server = Server(tmp_path)
    server.populate()
    settings = server.settings(keep_daily=1, keep_monthly=0, min_gap_s=0)
    sent, answers = [], []

    def fake_send(**given):
        sent.append((given["archive"].name[17:25], given["fy"]))
        return answers.pop(0) if answers else offsite.Outcome("ok")

    monkeypatch.setattr(offsite, "send", fake_send)
    told = []
    monkeypatch.setattr(backup, "record", lambda *a, **k: told.append(k) or True)

    # 4 April: the year's archive is made; its upload fails.
    answers[:] = [offsite.Outcome("failed", "network")]
    first = backup.run(settings, clock=lambda: ist(2027, 4, 4))
    assert first.offsite == "failed" and sent == [("20270404", "2026-27")]
    owed = _status(settings)["fy_owed"]
    assert owed == {"fy": "2026-27", "archive": first.archive}
    assert [(t["why"], t["fy"], t["fy_tonight"]) for t in told] == [("offsite", "2026-27", "yes")]

    # 5 April: the link is still down. Tonight's own upload fails; the year's stays owed and is
    # not tried into a dead link.
    answers[:] = [offsite.Outcome("failed", "network")]
    backup.run(settings, clock=lambda: ist(2027, 4, 5))
    assert sent[1:] == [("20270405", None)] and _status(settings)["fy_owed"] == owed

    # 6 April: tonight's goes, then the year's is tried again under the year's name, and fails.
    answers[:] = [offsite.Outcome("ok"), offsite.Outcome("failed", "busy")]
    told.clear()
    third = backup.run(settings, clock=lambda: ist(2027, 4, 6))
    assert sent[2:] == [("20270406", None), ("20270404", "2026-27")]
    assert third.offsite == "ok" and _status(settings)["fy_owed"] == owed
    assert [(t["why"], t["reason"], t["fy"]) for t in told] == [("offsite", "busy", "2026-27")]
    assert "fy_tonight" not in told[0]

    # 7 April: both go. Nothing is owed any more, and it is not sent a third time after that.
    told.clear()
    backup.run(settings, clock=lambda: ist(2027, 4, 7))
    assert sent[4:] == [("20270407", None), ("20270404", "2026-27")]
    assert _status(settings)["fy_owed"] is None and told == []
    backup.run(settings, clock=lambda: ist(2027, 4, 8))
    assert sent[6:] == [("20270408", None)]
    # The year's archive itself was never removed while it waited.
    assert "20270404" in {b.name[17:25] for b in backup.list_archives(settings.archive_dir)}


def test_what_is_owed_is_read_only_when_it_still_holds(tmp_path):
    server = Server(tmp_path)
    settings = server.settings()
    settings.archive_dir.mkdir(parents=True)
    year = "sudarshan-backup-20270404-163000.tar.gz"
    other = "sudarshan-backup-20270405-163000.tar.gz"
    for name in (year, other):
        (settings.archive_dir / name).write_bytes(b"x")
    archives = backup.list_archives(settings.archive_dir)

    def kept(value):
        settings.status_file.write_text(json.dumps({"fy_owed": value}), encoding="utf-8")
        return backup.owed_year(settings, archives)

    assert kept({"fy": "2026-27", "archive": year}) == {"fy": "2026-27", "archive": year}
    assert kept(None) is None
    assert kept({"fy": "2026-27", "archive": other}) is None  # not that year's own archive
    assert kept({"fy": "2025-26", "archive": year}) is None
    assert kept({"fy": "../x", "archive": year}) is None
    assert kept({"fy": "2026-27", "archive": "sudarshan-backup-20270404-999999.tar.gz"}) is None
    assert kept("2026-27") is None and kept([year]) is None
    settings.status_file.write_text("{not json", encoding="utf-8")
    assert backup.owed_year(settings, archives) is None
    settings.status_file.unlink()
    assert backup.owed_year(settings, archives) is None


def test_the_count_of_years_is_a_setting():
    assert backup.Settings.from_env({}).keep_yearly == 8
    assert backup.Settings.from_env({"SUDARSHAN_BACKUP_KEEP_YEARLY": "0"}).keep_yearly == 0
    with pytest.raises(backup.ConfigError):
        backup.Settings.from_env({"SUDARSHAN_BACKUP_KEEP_YEARLY": "51"})
