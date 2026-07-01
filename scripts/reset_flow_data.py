"""Clean-slate reset for wallet-flow validation.

Archives the current SQLite DB (timestamped copy) then clears flow_scans and
tracked_markets so calibration restarts from zero. Use when changing the scan
universe (e.g. the crypto-only category filter) so the old, polluted data
doesn't mix with the new run.

Usage:
    python scripts/reset_flow_data.py            # prompts for confirmation
    python scripts/reset_flow_data.py --yes      # non-interactive
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypto_news_parser.storage import archive_and_reset_flow_data, db_path  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Archive + clear wallet-flow scan/tracking data.")
    p.add_argument("--yes", action="store_true", help="Skip confirmation")
    args = p.parse_args()

    src = db_path()
    print(f"Database: {src}")
    if not args.yes:
        reply = input("Archive a copy and CLEAR flow_scans + tracked_markets? [y/N] ")
        if reply.strip().lower() not in {"y", "yes"}:
            print("Aborted.")
            return 1

    archive = archive_and_reset_flow_data()
    print(f"Archived to: {archive}")
    print("Cleared flow_scans + tracked_markets. Calibration counter is now zero.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
