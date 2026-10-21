"""Entry point: python -m engrebodds_ingest <job> [options]"""

import argparse
import sys

from engrebodds_ingest.config import load_settings
from engrebodds_ingest.jobs import JOBS

# Options a job may take; only the ones given are passed on.
OPTIONS = {
    "days_back": "days before today (sync_fixtures)",
    "days_ahead": "days after today (sync_fixtures, sync_odds)",
    "max_pages": "cap on pages per date (sync_odds; useful on the free plan)",
    "limit": "cap on fixtures or leagues per run (sync_stats, sync_standings)",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="engrebodds_ingest")
    parser.add_argument("job", nargs="?", help="job to run")
    parser.add_argument("--list", action="store_true", help="list available jobs")
    for name, help_text in OPTIONS.items():
        parser.add_argument("--" + name.replace("_", "-"), dest=name, type=int, help=help_text)
    args = parser.parse_args(argv)

    if args.list or not args.job:
        print("Available jobs:", ", ".join(sorted(JOBS)) or "(none yet)")
        return 0
    if args.job not in JOBS:
        print(f"Unknown job: {args.job}", file=sys.stderr)
        return 2

    options = {name: getattr(args, name) for name in OPTIONS if getattr(args, name) is not None}
    JOBS[args.job](load_settings(), **options)
    return 0


if __name__ == "__main__":
    sys.exit(main())
