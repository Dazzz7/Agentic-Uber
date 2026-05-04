"""
Run all 5 scenarios and save transcripts to transcripts/.

Usage:
    python scenarios/run_all.py          # from project root
    python run_all.py                    # from scenarios/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.uber_mock import UberMockAdapter
from adapters.lyft_mock import LyftMockAdapter
from scenarios.runner import ScenarioRunner

def _make_adapters(seed: int, surge: float = 1.0) -> dict:
    """Build both platform adapters with the same seed and surge."""
    return {
        "uber": UberMockAdapter(surge_multiplier=surge, seed=seed),
        "lyft": LyftMockAdapter(surge_multiplier=surge, seed=seed),
    }

# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1 — Happy path
# A user books a standard UberX, then tracks it.
# ─────────────────────────────────────────────────────────────────────────────

def scenario_01_happy_path():
    _header("01 — Happy Path: book UberX from sfo airport to downtown san jose")

    r = ScenarioRunner("01_happy_path", adapters=_make_adapters(seed=1))
    r.queue_confirm("yes")   # booking confirmation

    r.turn("I need a ride from sfo airport to downtown san jose.")
    r.turn("Book me the UberX please.")
    r.turn("Great, can you track my ride?")
    r.turn("What is my ride progress?")
    r.turn("Give me an update")
    r.turn("Tell my ride status?")
    r.turn("Are we there yet?")

    r.save()


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 2 — Price comparison
# User wants to compare all options before deciding.
# ─────────────────────────────────────────────────────────────────────────────

def scenario_02_price_comparison():
    _header("02 — Price Comparison: Uber vs Lyft, all options menlo park → santa clara")

    r = ScenarioRunner("02_price_comparison", adapters=_make_adapters(seed=2))
    r.queue_confirm("yes")   # booking confirmation after comparison

    r.turn("Show me all ride options from menlo park to santa clara across both Uber and Lyft.")
    r.turn("What's the price difference between the cheapest and most expensive overall?")
    r.turn("The price difference is worth it for Lyft — book Lyft Shared for me.")

    r.save()


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 3 — Bad address
# First request uses a nonsensical address → agent handles the error
# gracefully and helps the user provide a valid one.
# ─────────────────────────────────────────────────────────────────────────────

def scenario_03_bad_address():
    _header("03 — Bad Address: geocoding failure + recovery")

    r = ScenarioRunner("03_bad_address", adapters=_make_adapters(seed=3))
    # No confirms queued for the bad-address turn (it fails before the gate)
    r.queue_confirm("yes")   # confirm after the user corrects the address

    r.turn("Book me a ride from 'asdfjkl;' to 'nowhere special please'.")
    r.turn("Sorry about that. How about from redwood city to oakland?")
    r.turn("Perfect, book the cheapest option.")

    r.save()


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 4 — Surge pricing edge case
# 2.5x surge is active. Agent warns the user. User books anyway.
# Also tests that the agent explicitly surfaces the surge before confirming.
# ─────────────────────────────────────────────────────────────────────────────

def scenario_04_surge():
    _header("04 — Surge Pricing: 2.5x surge, user books anyway")

    r = ScenarioRunner("04_surge", adapters=_make_adapters(surge=2.5, seed=4))
    r.queue_confirm("yes")   # user confirms despite surge

    r.turn("I need a ride from mountain view to san jose airport right now, it's urgent. Show all options across lyft and uber.")
    r.turn("That surge is steep — roughly what would the normal price be without it?")
    r.turn("Fine, book me the fastest pickup option. I need to catch my flight.")

    r.save()


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 5 — Cancellation
# User books a ride then decides to cancel. Both actions go through gates.
# ─────────────────────────────────────────────────────────────────────────────

def scenario_05_cancellation():
    _header("05 — Cancellation: book then cancel with gate enforcement")

    r = ScenarioRunner("05_cancellation", adapters=_make_adapters(seed=5))
    r.queue_confirm("yes")   # booking confirmation
    r.queue_confirm("yes")   # cancellation confirmation
    r.queue_confirm("yes")   # booking confirmation
    r.queue_confirm("yes")   # cancellation confirmation

    r.turn("Book me a UberX from palo alto to oakland airport.")
    r.turn("Actually, my plans changed — please cancel the ride.")
    r.turn("Book me a Lyft lux from palo alto to sfo airport.")
    r.turn("Great, can you track my ride?")
    r.turn("Give me an update")
    r.turn("Track my ride")
    r.turn("Tell my ride status?")
    r.turn("Please cancel my ride.")


    r.save()


# ─────────────────────────────────────────────────────────────────────────────

def _header(title: str) -> None:
    bar = "═" * 66
    print(f"\n{bar}")
    print(f"  {title}")
    print(bar)


def main():
    print("\nRunning all 5 scenarios...")
    scenario_01_happy_path()
    scenario_02_price_comparison()
    scenario_03_bad_address()
    scenario_04_surge()
    scenario_05_cancellation()
    print("\n\nAll scenarios complete. Transcripts saved to transcripts/")
    print("  JSON : transcripts/<name>.json")
    print("  MD   : transcripts/<name>.md")
    print("  Log  : transcripts/<name>_actions.jsonl")


if __name__ == "__main__":
    main()
