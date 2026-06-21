"""Terminal entry point for the VALTrack data harvest.

This is the cheap, list-level pass: franchise teams, rankings, rosters, and
match histories. It is safe to re-run and stop or fail partway through, since
each team commits on its own.

Usage:
    python harvest.py --scope full         one-time full load (slow)
    python harvest.py --scope incremental  only new matches since last run

The full per-match detail pass is a separate, later step. Make sure vlrggapi is
running at http://127.0.0.1:3001 before harvesting.
"""
import argparse
import time

from valtrack.ingest import run_ingest


def main():
    parser = argparse.ArgumentParser(description="Harvest VLR data into SQLite.")
    parser.add_argument(
        "--scope",
        choices=["full", "incremental"],
        default="incremental",
        help="full loads all history; incremental stops at known matches",
    )
    args = parser.parse_args()

    print(f"starting {args.scope} harvest")
    start = time.monotonic()
    summary = run_ingest(scope=args.scope)
    elapsed = time.monotonic() - start

    print("")
    print(f"done in {elapsed:.0f}s")
    print(f"  teams loaded: {summary['teams']}")
    print(f"  matches written: {summary['matches']}")
    if summary["unresolved"]:
        print(f"  unresolved names: {', '.join(summary['unresolved'])}")
    if summary["errors"]:
        print(f"  errors: {', '.join(summary['errors'])}")


if __name__ == "__main__":
    main()
