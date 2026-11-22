#!/usr/bin/env bash
# The date sentinel, on a Linux box with libfaketime installed (sudo apt-get install -y faketime).
#
#   scripts/date_sentinel.sh <days> [part]
#
# <days>  how far ahead the clock is shifted: 0 (a control run), 1, 7, 30 or 100 are the ones the
#         nightly workflow uses.
# [part]  1 to 4  one shard of the sudarshan backend tests (sudarshan/tests/ci_shards.py)
#         upstream  test/test_strategy_module_*.py
#         all  every one of those, one after another (the default)
#
# Environment: PYTHON (default .venv/bin/python, else python3), FAKETIME_LIB (default: searched for),
# DATE_SENTINEL_OUT (default ./date-sentinel-out, where the logs, the junit files and
# failing-<days>d-<part>.txt, one failing test id per line, are written).
#
# Only the pytest process (and what it starts) runs on the shifted clock; this script, and the
# date it reports, stay on the real one. Exit status: 0 when nothing failed, 1 when a test failed,
# 2 for a bad call or a libfaketime that does not shift the clock.
# What a failure means, and the tests skipped by name: sudarshan/README.md, "The date sentinel".
set -u

usage() {
  echo "usage: scripts/date_sentinel.sh <days> [1|2|3|4|upstream|all]" >&2
  exit 2
}

days=${1:-}
part=${2:-all}
case "$days" in '' | *[!0-9]*) usage ;; esac
case "$part" in 1 | 2 | 3 | 4 | upstream | all) ;; *) usage ;; esac

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root" || exit 2
py=${PYTHON:-}
if [ -z "$py" ]; then
  if [ -x .venv/bin/python ]; then py=.venv/bin/python; else py=python3; fi
fi
out=${DATE_SENTINEL_OUT:-$root/date-sentinel-out}
mkdir -p "$out"

lib=${FAKETIME_LIB:-}
if [ -z "$lib" ]; then
  # The multithread build first (the tests start threads). On arm64 both builds segfaulted the
  # test run, even with the clock not moved: use an x86_64 machine.
  for candidate in /usr/lib/*/faketime/libfaketimeMT.so.1 /usr/lib/faketime/libfaketimeMT.so.1 \
    /usr/local/lib/faketime/libfaketimeMT.so.1 /usr/lib/*/faketime/libfaketime.so.1 \
    /usr/lib/faketime/libfaketime.so.1 /usr/local/lib/faketime/libfaketime.so.1; do
    if [ -e "$candidate" ]; then
      lib=$candidate
      break
    fi
  done
fi
if [ -z "$lib" ] || [ ! -e "$lib" ]; then
  echo "libfaketimeMT.so.1 (or libfaketime.so.1) not found: install faketime or set FAKETIME_LIB" >&2
  exit 2
fi

# The monotonic clock is shifted with the wall clock (the default): a constant shift cannot change
# a difference, and leaving it alone made sleeps fail (EINVAL) and 35 ops tests fail on day 1.
shifted() {
  LD_PRELOAD="$lib${LD_PRELOAD:+:$LD_PRELOAD}" FAKETIME="+${days}d" "$@"
}

real_now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
expected=$(date -u -d "+${days} days" +%F)
seen=$(shifted date -u +%F)
echo "date sentinel: real clock $real_now, libfaketime $lib"
echo "date sentinel: the shifted clock reads $seen (expected $expected for +${days}d)"
if [ "$seen" != "$expected" ] && [ "$days" -ne 0 ]; then
  echo "libfaketime did not shift the clock; nothing was run" >&2
  exit 2
fi

status=0

run_files() { # run_files <label> <file>...
  label=$1
  shift
  failed="$out/failing-${days}d-$label.txt"
  rm -f "$failed"
  echo "date sentinel: ${days}d, $label: $# test files"
  shifted env SUDARSHAN_DATE_SENTINEL=1 SUDARSHAN_DATE_SENTINEL_FAILED="$failed" \
    nice -n 10 "$py" -m pytest -o addopts="" --timeout=300 -p no:cacheprovider \
    -p sudarshan.tests.date_sentinel_plugin -q -rfEs \
    --junitxml="$out/junit-${days}d-$label.xml" "$@" 2>&1 | tee "$out/pytest-${days}d-$label.log"
  code=${PIPESTATUS[0]}
  # 5 is "no tests collected": an empty shard is not a failure.
  if [ "$code" -ne 0 ] && [ "$code" -ne 5 ]; then
    status=1
    if [ ! -s "$failed" ]; then
      echo "(pytest exited $code with no failing test id: see the log)" >"$failed"
    fi
  fi
  [ -e "$failed" ] || : >"$failed"
}

run_shard() { # run_shard <n>
  # shellcheck disable=SC2046
  set -- $("$py" -m sudarshan.tests.ci_shards "$1" 4)
  [ "$#" -gt 0 ] && run_files "$part_label" "$@"
}

case "$part" in
  1 | 2 | 3 | 4)
    part_label=$part
    run_shard "$part"
    ;;
  upstream)
    # shellcheck disable=SC2046
    run_files upstream $(ls test/test_strategy_module_*.py)
    ;;
  all)
    for n in 1 2 3 4; do
      part_label=$n
      run_shard "$n"
    done
    # shellcheck disable=SC2046
    run_files upstream $(ls test/test_strategy_module_*.py)
    ;;
esac

echo "date sentinel: real clock at the end $(date -u +%Y-%m-%dT%H:%M:%SZ); failing ids in $out"
cat "$out"/failing-"${days}"d-*.txt 2>/dev/null
exit "$status"
