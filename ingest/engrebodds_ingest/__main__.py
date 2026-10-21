"""Entry point: python -m engrebodds_ingest <job>"""

import argparse
import sys

from engrebodds_ingest.config import load_settings
from engrebodds_ingest.jobs import JOBS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="engrebodds_ingest")
    parser.add_argument("job", nargs="?", help="job to run")
    parser.add_argument("--list", action="store_true", help="list available jobs")
    args = parser.parse_args(argv)

    if args.list or not args.job:
        print("Available jobs:", ", ".join(sorted(JOBS)) or "(none yet)")
        return 0
    if args.job not in JOBS:
        print(f"Unknown job: {args.job}", file=sys.stderr)
        return 2

    JOBS[args.job](load_settings())
    return 0


if __name__ == "__main__":
    sys.exit(main())
