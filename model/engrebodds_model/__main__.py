"""Entry point: python -m engrebodds_model <command>"""

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="engrebodds_model")
    sub = parser.add_subparsers(dest="command", required=True)
    bt = sub.add_parser("backtest", help="walk-forward backtest on football-data.co.uk (private use only)")
    bt.add_argument("--download", action="store_true", help="fetch missing CSVs first")
    bt.add_argument("--quick", action="store_true", help="5 leagues only, for a fast check")
    bt.add_argument("--workers", type=int, default=None, help="parallel processes (default: all cores)")
    bt.add_argument("--improve", action="store_true", help="run the model improvement ladder instead")
    args = parser.parse_args(argv)

    if args.command == "backtest":
        from engrebodds_model.backtest import football_data, run
        if args.download:
            failed = football_data.download()
            print("download failures:", failed or "none")
        if args.improve:
            from engrebodds_model.backtest import improve
            improve.run(workers=args.workers)
        else:
            run.run(quick=args.quick, workers=args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
