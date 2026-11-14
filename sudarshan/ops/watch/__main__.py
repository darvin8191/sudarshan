"""``python -m sudarshan.ops.watch``: one run of the watch (the timer's command).

``--check`` reads and judges and prints one line per check and one for the outside heartbeat
(whether a link is set, never the link); it writes no file, records nothing and sends nothing, so
it is safe to run by hand at any time.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import checks, heartbeat, readers, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sudarshan.ops.watch")
    parser.add_argument("--check", action="store_true", help="print the checks; write nothing")
    args = parser.parse_args(argv)
    settings = run.settings_from(os.environ)
    if not args.check:
        return run.run_once(settings)
    state = run.load_state(settings.state_file)
    readings = readers.gather(time.time(), settings.app_dir, settings.env_file)
    for result in checks.run_checks(readings, state.get("memory") or {}):
        facts = " ".join(f"{name}={value}" for name, value in sorted(result.facts.items()))
        sys.stdout.write(f"{result.name:<20} {result.level:<8} {facts}\n")
    found = heartbeat.links(os.environ)  # whether a link is set; it is never printed or asked for
    beat = found if isinstance(found, str) else "set"
    words = heartbeat.WORDS.get(beat, "An outside heartbeat is set up; each run sends it.")
    sys.stdout.write(f"{'heartbeat':<20} {beat:<8} {words}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
