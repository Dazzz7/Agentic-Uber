"""
Interactive ride-booking agent.

Usage:
    python main.py                  
    python main.py --surge 2.5
    python main.py --help
"""

import argparse
import sys
from pathlib import Path

from adapters.uber_mock import UberMockAdapter
from adapters.lyft_mock import LyftMockAdapter
from agent.action_log import ActionLog
from agent.ride_agent import RideAgent

BANNER = """
╔══════════════════════════════════════════════════════════╗
║       Ride Booking Agent  —  powered by Claude-Sonnet    ║
║   Type your request in plain English. Type 'quit' exit.  ║
╚══════════════════════════════════════════════════════════╝
"""


def build_adapter(platform: str, surge: float):
    if platform == "lyft":
        return LyftMockAdapter(surge_multiplier=surge)
    return UberMockAdapter(surge_multiplier=surge)


def main():
    parser = argparse.ArgumentParser(description="AI-powered ride booking agent")
    #parser.add_argument("--platform", choices=["uber", "lyft"], default="uber")
    parser.add_argument("--surge", type=float, default=1.0,
                        help="Surge multiplier (e.g. 2.5 to simulate surge pricing)")
    parser.add_argument("--model", default="claude-sonnet-4-5")
    parser.add_argument("--log", default="logs/session.jsonl",
                        help="Path for the action log JSONL file")
    args = parser.parse_args()

    adapters = {
        "uber": UberMockAdapter(surge_multiplier=args.surge),
        "lyft": LyftMockAdapter(surge_multiplier=args.surge),
    }
    log = ActionLog(log_file=Path(args.log))
    agent = RideAgent(adapters=adapters, action_log=log, model=args.model)

    print(BANNER)
    print(f"  Platforms: Uber, Lyft (say which you want)")
    print(f"  Model    : {args.model}")
    print(f"  Log file : {args.log}")
    if args.surge > 1.0:
        print(f"  ⚠  Surge mode: {args.surge}x")
    print()

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("\nGoodbye.")
            break

        response = agent.chat(user_input)
        print(f"\nAgent: {response}\n")

    print()
    print(log.summary())


if __name__ == "__main__":
    main()
