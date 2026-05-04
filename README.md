# Ride Booking Agent

An AI agent that books rides end-to-end: discover options, compare prices, book, track, and cancel. Built with Claude's tool-use API and a swappable platform adapter pattern. Supports Uber and Lyft.

## Quick Start:

```bash
git clone <repo>
cd ride-agent

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create a .env file with your Anthropic API key
echo "ANTHROPIC_API_KEY = your_key_here" > .env

# Run all 5 scenarios (saves transcripts to transcripts/)
python scenarios/run_all.py

# Or run the interactive agent tell it which platform you want in plain English
python main.py                    # default is uber
python main.py --surge 2.5            # simulate surge pricing
python main.py --model claude-opus-4-5       # switch model
```
# Helpful prompts:
'''
1. Book a ride from japan town to mountain view
2. Book a lyft from sfo to cupertino
3. Compare prices across uber and lyft to ride from santa clara to milpitas
4. Cancel ride
5. Provide approximate landmark location. Example locations to use: sfo, san francisco airport, oakland, fremont, san jose airport, san mateo, etc. Check uber_mock for more locations details.

## Architecture

```
adapters/
  base.py          Abstract PlatformAdapter interface — the contract
  uber_mock.py     Uber implementation (mock, realistic pricing + surge)
  lyft_mock.py     Lyft implementation — shows exactly what changes

agent/
  action_log.py    Append-only JSONL log: requested→verified→executed→result
  tools.py         Tool implementations + CONFIRMATION GATE logic
  ride_agent.py    Anthropic Claude tool-use loop

scenarios/
  runner.py        Scripted scenario harness with injectable confirmations
  run_all.py       5 scenarios: happy path, comparison, bad address, surge, cancel

main.py            Interactive REPL
```

## Confirmation Gate

The agent **cannot** book or cancel without a human-approved token:

```
Agent calls request_booking_confirmation(...)
  → User sees details, types "yes"
  → System returns a single-use UUID token

Agent calls book_ride(..., confirmation_token=<token>)
  → Token is consumed atomically
  → Adapter books the ride
  → Action logged with verified=True
```

A `book_ride` call without a valid token is rejected in code — not by
instruction to the LLM. The LLM literally cannot reach the adapter.

## Swapping Platforms

See `PLATFORM_SWAP.md` for a full analysis. TL;DR: implement the 7 methods
in `PlatformAdapter`, pass your adapter to `RideAgent`. Nothing else changes.

'''python
from adapters.uber_mock import UberMockAdapter
from adapters.lyft_mock import LyftMockAdapter

agent = RideAgent(
    adapters={
        "uber": UberMockAdapter(),
        "lyft": LyftMockAdapter(),
    },
    ...
)
'''

## Action Log

Every action is written to a JSONL file (default: `logs/actions.jsonl`):

'''json
{
  "action_id": "a3f1b2c4",
  "action_type": "book_ride",
  "timestamp_start": "2026-04-25T14:30:00Z",
  "timestamp_end": "2026-04-25T14:30:01Z",
  "status": "success",
  "verified_by_user": true,
  "requested": {"pickup": "Times Square", "dropoff": "JFK", "ride_type": "UberX"},
  "executed": {"pickup": "Times Square", "dropoff": "JFK", "ride_type": "UberX"},
  "result": {"success": true, "ride_id": "UBR-A1B2C3D4", ...}
}
'''

## Scenarios

| # | Scenario | What it tests |
|---|----------|---------------|
| 01 | Happy path | Full flow: search → confirm → book → track |
| 02 | Price comparison | Uber vs Lyft side-by-side, price delta, user picks |
| 03 | Bad address | Geocoding error → recovery → rebook |
| 04 | Surge pricing | 2.5x surge across both platforms, agent warns, eta based user confirms |
| 05 | Cancellation | Book (tried on both platforms) → cancel (both gates fire) |

Transcripts are saved to `transcripts/` as JSON + Markdown.
