#!/usr/bin/env python3
"""
run_live — validate Alpaca credentials, then launch the trading dashboard.

    cd ~/Projects/HFT/host && python3 run_live.py

Takes the API key/secret from the environment if they are already exported, and
otherwise PROMPTS for them. The secret is read with getpass, so it is never
echoed to the terminal, never written to shell history, and never stored on
disk -- it lives only in this process's environment and is inherited by the
server it starts.

Before launching it actually calls Alpaca and prints the account, so a bad key
fails here with a clear message instead of silently leaving the dashboard on the
simulated feed.

PAPER BY DEFAULT. Pass --real to use the live-money account; you will be asked to
type LIVE to confirm.
"""
from __future__ import annotations

import os
import sys
import getpass
import argparse
import subprocess


def ask(name: str, prompt: str, secret: bool = False) -> str:
    val = os.environ.get(name)
    if val:
        print(f"  {name:22} from environment")
        return val
    val = (getpass.getpass(prompt) if secret else input(prompt)).strip()
    if not val:
        sys.exit(f"error: {name} is required")
    os.environ[name] = val
    return val


def main() -> int:
    ap = argparse.ArgumentParser(description="launch the live trading dashboard")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--real", action="store_true",
                    help="use the LIVE money account instead of paper")
    args = ap.parse_args()

    print("=== Alpaca credentials ===")
    key = ask("APCA_API_KEY_ID", "  API key ID       : ")
    ask("APCA_API_SECRET_KEY", "  API secret (hidden): ", secret=True)

    # Alpaca key prefixes: PK = paper, AK = live. Worth surfacing, because a live
    # key with --real is real money and the two are one character apart.
    looks_paper = key.upper().startswith("PK")
    print(f"  key prefix           : {key[:2].upper()} "
          f"({'paper' if looks_paper else 'LIVE ACCOUNT' if key.upper().startswith('AK') else 'unknown'})")

    if args.real:
        print("\n  *** --real: orders will be sent to the LIVE money account ***")
        if input("  type LIVE to continue: ").strip() != "LIVE":
            return 1
    elif not looks_paper:
        print("\n  NOTE: this does not look like a paper key, but --real was not")
        print("        given, so the paper endpoint will be used anyway.")

    # --- validate before launching ---------------------------------------
    print("\n=== validating ===")
    try:
        from alpaca.trading.client import TradingClient
        c = TradingClient(os.environ["APCA_API_KEY_ID"],
                          os.environ["APCA_API_SECRET_KEY"],
                          paper=not args.real)
        acct = c.get_account()
        print(f"  account   : {acct.account_number}")
        print(f"  status    : {acct.status}")
        print(f"  equity    : ${float(acct.equity):,.2f}")
        print(f"  buying pwr: ${float(acct.buying_power):,.2f}")
    except Exception as e:
        print(f"  FAILED: {e}")
        print("\n  The key/secret pair was rejected. Regenerate them in the Alpaca")
        print("  dashboard (Paper Trading -> API Keys) -- the secret is shown only")
        print("  once, at generation.")
        return 1

    # --- confirm the market data feed is reachable ------------------------
    try:
        from crypto_feed import CryptoFeed
        import time
        f = CryptoFeed(symbol="BTC/USD", source="alpaca")
        f.start()
        time.sleep(6)
        st = f.status()
        f.stop()
        if st["live"] and st["ticks"]:
            print(f"  BTC/USD   : ${st['mid']:,.2f}  ({st['ticks']} quotes in 6s)")
        else:
            print("  WARNING: no quotes received in 6s. Crypto trades 24/7, so this")
            print("           usually means the data subscription is not enabled.")
    except Exception as e:
        print(f"  feed check failed: {e}")

    # --- launch -----------------------------------------------------------
    print(f"\n=== starting dashboard on http://127.0.0.1:{args.port} ===")
    print("  On the page, in THIS order:")
    print("    1. feed   -> ALPACA LIVE")
    print("    2. broker -> " + ("LIVE (real money)" if args.real else "PAPER"))
    print("    3. BEGIN TRADING")
    print("  The broker cannot be armed until the feed is live -- that is deliberate.")
    print("  Ctrl-C to stop.\n")

    here = os.path.dirname(os.path.abspath(__file__))
    return subprocess.call([sys.executable, "monitor_server.py",
                            "--port", str(args.port)], cwd=here, env=os.environ)


if __name__ == "__main__":
    sys.exit(main())
