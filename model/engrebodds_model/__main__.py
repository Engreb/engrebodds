"""Entry point: python -m engrebodds_model <command>

Live (API-Football data in Supabase):  predict | shadow_report | tune_live [--dry-run] | activate_settings <id>
Research (football-data, private use):  backtest [--download] [--quick] [--improve] [--workers N]
"""

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="engrebodds_model")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("predict", help="daily predictions for the next 48 hours (no API requests)")
    sh = sub.add_parser("shadow_report", help="score frozen predictions of finished matches")
    sh.add_argument("--days", type=int, default=7, help="length of the 'week' section")
    tl = sub.add_parser("tune_live", help="tune settings on API-Football data; saved inactive")
    tl.add_argument("--dry-run", action="store_true", help="print the result instead of saving it")
    act = sub.add_parser("activate_settings", help="make a reviewed model_settings row the active one")
    act.add_argument("settings_id", type=int)
    bt = sub.add_parser("backtest", help="walk-forward backtest on football-data.co.uk (private use only)")
    bt.add_argument("--download", action="store_true", help="fetch missing CSVs first")
    bt.add_argument("--quick", action="store_true", help="5 leagues only, for a fast check")
    bt.add_argument("--workers", type=int, default=None, help="parallel processes (default: all cores)")
    bt.add_argument("--improve", action="store_true", help="run the model improvement ladder instead")
    args = parser.parse_args(argv)

    if args.command in ("predict", "shadow_report"):
        from engrebodds_ingest.config import load_settings
        from engrebodds_model.live import predict, shadow
        (predict.run if args.command == "predict" else shadow.run)(load_settings(), **(
            {"days": args.days} if args.command == "shadow_report" else {}))
    elif args.command == "tune_live":
        from engrebodds_model import tune_live
        tune_live.main(["--dry-run"] if args.dry_run else [])
    elif args.command == "activate_settings":
        import psycopg
        from engrebodds_ingest.config import load_settings
        from engrebodds_model.live import settings as live_settings
        with psycopg.connect(load_settings().database_url) as conn:
            version = live_settings.activate(conn, args.settings_id)
            conn.commit()
        print(f"Activated model_settings {args.settings_id} ({version}); predictions use it from the next run.")
    elif args.command == "backtest":
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
