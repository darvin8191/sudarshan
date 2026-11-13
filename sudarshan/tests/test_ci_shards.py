"""The CI shards (sudarshan/conftest.py) cover every test file exactly once."""

from __future__ import annotations

from sudarshan.conftest import ROOT, shard_of

COUNT = 4  # the matrix in .github/workflows/sudarshan-ci.yml


def test_every_test_file_lands_in_exactly_one_shard():
    files = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "sudarshan").rglob("test_*.py")
        if "/tests/" in path.relative_to(ROOT).as_posix()
    )
    assert len(files) > 100
    shards = {name: shard_of(name, COUNT) for name in files}
    assert set(shards.values()) == set(range(1, COUNT + 1))  # no shard is empty
    assert all(1 <= shard <= COUNT for shard in shards.values())


def test_the_workflow_runs_the_same_number_of_shards():
    workflow = (ROOT / ".github" / "workflows" / "sudarshan-ci.yml").read_text(encoding="utf-8")
    assert f"shard: [{', '.join(str(i) for i in range(1, COUNT + 1))}]" in workflow
    assert f"SUDARSHAN_TEST_SHARD: ${{{{ matrix.shard }}}}/{COUNT}" in workflow
