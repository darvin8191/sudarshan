"""``python -m sudarshan.ops.watch``: one run of the watch (the timer's command).

``--check`` reads and judges and prints one line per check, one for the name the certificate is
asked for by, and one for the outside heartbeat (whether a link is set, never the link); it writes no file, records nothing and sends nothing, so
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
    readings = readers.gather(
        time.time(), settings.app_dir, settings.env_file, None, state.get("posture")
    )
    for result in checks.run_checks(readings, state.get("memory") or {}):
        facts = " ".join(f"{name}={value}" for name, value in sorted(result.facts.items()))
        sys.stdout.write(f"{result.name:<20} {result.level:<8} {facts}\n")
    site = readers.site_name(settings.env_file)
    sys.stdout.write(f"{'certificate site':<20} {'set' if site else 'unknown':<8} {_site(site)}\n")
    found = heartbeat.links(os.environ)  # whether a link is set; it is never printed or asked for
    beat = found if isinstance(found, str) else "set"
    words = heartbeat.WORDS.get(beat, "An outside heartbeat is set up; each run sends it.")
    sys.stdout.write(f"{'heartbeat':<20} {beat:<8} {words}\n")
    return 0


def _site(site: str | None) -> str:
    """Which name the certificate check asks nginx for, so the owner can see it is his app's."""
    if site:
        return f"The certificate is asked for by the app's own address: {site}"
    return (
        "The app's own address is not known, so no certificate is checked (another site's is "
        f"never judged in its place). Add {readers.CERT_HOST_ENV}=<the app's address> to "
        "/etc/sudarshan/watch.env."
    )


if __name__ == "__main__":
    sys.exit(main())
