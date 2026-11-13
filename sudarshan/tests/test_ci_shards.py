"""The CI shards (sudarshan/tests/ci_shards.py) cover every test file exactly once."""

from __future__ import annotations

from sudarshan.tests.ci_shards import ROOT, all_test_files, files_for, shard_of

COUNT = 4  # the matrix in .github/workflows/sudarshan-ci.yml


def test_every_test_file_lands_in_exactly_one_shard():
    files = all_test_files()
    assert len(files) > 100
    shards = [files_for(shard, COUNT) for shard in range(1, COUNT + 1)]
    assert all(shards)  # no shard is empty
    assert sorted(name for shard in shards for name in shard) == files
    assert all(1 <= shard_of(name, COUNT) <= COUNT for name in files)


def test_the_workflow_runs_the_same_shards():
    workflow = (ROOT / ".github" / "workflows" / "sudarshan-ci.yml").read_text(encoding="utf-8")
    assert f"shard: [{', '.join(str(i) for i in range(1, COUNT + 1))}]" in workflow
    assert f"python -m sudarshan.tests.ci_shards ${{{{ matrix.shard }}}} {COUNT}" in workflow


def test_there_is_no_conftest_at_the_package_root():
    """sudarshan/ has no __init__.py: a conftest.py there puts the folder on the import path."""
    assert not (ROOT / "sudarshan" / "conftest.py").exists()
