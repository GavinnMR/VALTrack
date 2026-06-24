"""Terminal entry point for the VALTrack data harvest.

Two passes, selected with --pass:

  cheap (default): the list-level pass. Franchise teams, rankings, rosters, and
  match histories. Fast, and what an ongoing incremental refresh runs.

  details: the expensive per-match pass. One API call per stored match to pull
  per-map scores, player stats, rounds, and vetos. This is the bulk of the
  harvest time. It only fetches matches that do not already have detail, so it
  is safe to re-run and resume after a stop or failure.

  details --redetail: re-fetch matches that were detailed before the per-map
  economy and series-performance tables existed, so those rich tables fill in.
  Pair it with --since to keep the backfill to a recent window (a scout does not
  need years-old economy), for example the last twelve to eighteen months.

Run the cheap pass first so there are matches to pull detail for.

Usage:
    python harvest.py --pass cheap --scope full          one-time full load (slow)
    python harvest.py --pass cheap --scope incremental   only new matches
    python harvest.py --pass details                     fill detail for matches missing it
    python harvest.py --pass details --limit 50          process only the newest 50
    python harvest.py --pass details --redetail --since 2025-01-01   backfill economy and performance for recent matches

Make sure vlrggapi is running at http://127.0.0.1:3001 before harvesting.
"""
import argparse
import time

from valtrack.ingest import run_detail_ingest, run_ingest


def _run_cheap(scope):
    print(f"starting cheap {scope} harvest")
    start = time.monotonic()
    summary = run_ingest(scope=scope)
    elapsed = time.monotonic() - start

    print("")
    print(f"done in {elapsed:.0f}s")
    print(f"  teams loaded: {summary['teams']}")
    print(f"  matches written: {summary['matches']}")
    if summary["unresolved"]:
        print(f"  unresolved names: {', '.join(summary['unresolved'])}")
    if summary["errors"]:
        print(f"  errors: {', '.join(summary['errors'])}")


def _run_details(scope, limit, redetail=False, since=None):
    label = "re-detail" if redetail else "detail"
    extra = "".join([
        f" (limit {limit})" if limit else "",
        f" since {since}" if since else "",
    ])
    print(f"starting {label} {scope} harvest{extra}")
    start = time.monotonic()
    summary = run_detail_ingest(
        scope=scope, limit=limit, redetail=redetail, since=since)
    elapsed = time.monotonic() - start

    print("")
    print(f"done in {elapsed:.0f}s")
    print(f"  matches detailed: {summary['matches']}")
    print(f"  maps stored: {summary['maps']}")
    if summary["errors"]:
        shown = ", ".join(str(m) for m in summary["errors"][:20])
        more = "" if len(summary["errors"]) <= 20 else f" (+{len(summary['errors']) - 20} more)"
        print(f"  errors on {len(summary['errors'])} matches: {shown}{more}")


def main():
    parser = argparse.ArgumentParser(description="Harvest VLR data into SQLite.")
    parser.add_argument(
        "--pass",
        dest="which",
        choices=["cheap", "details"],
        default="cheap",
        help="cheap is the list-level pass; details is the per-match pass",
    )
    parser.add_argument(
        "--scope",
        choices=["full", "incremental"],
        default="incremental",
        help="full loads all history; incremental stops at known matches",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="details only: cap how many matches to process in this run",
    )
    parser.add_argument(
        "--redetail",
        action="store_true",
        help="details only: also re-fetch matches detailed before the economy "
             "and performance tables existed, to backfill those rich tables",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="details only: limit to matches on or after this date (YYYY-MM-DD)",
    )
    args = parser.parse_args()

    if args.which == "cheap":
        _run_cheap(args.scope)
    else:
        _run_details(args.scope, args.limit, args.redetail, args.since)


if __name__ == "__main__":
    main()
