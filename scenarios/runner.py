"""
ScenarioRunner — harness for running scripted agent scenarios.

Inject pre-programmed confirmation responses via queue_confirm() so
scenarios run end-to-end without human input. Every exchange is
captured and saved as a JSON transcript + human-readable Markdown.
"""

import json
import sys
import textwrap
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Make project root importable when running from scenarios/
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.base import PlatformAdapter
from adapters.uber_mock import UberMockAdapter
from adapters.lyft_mock import LyftMockAdapter
from agent.action_log import ActionLog
from agent.ride_agent import RideAgent


TRANSCRIPT_DIR = Path(__file__).parent.parent / "transcripts"


class ScenarioRunner:
    """
    Runs a named scenario against a fresh agent instance.

    Parameters
    ----------
    name : str
        Scenario slug, e.g. "01_happy_path". Used for file names.
    adapter : PlatformAdapter
        Platform adapter to use (default: UberMockAdapter).
    seed : int
        Random seed for reproducible mock data.
    """

    def __init__(
        self,
        name: str,
        adapters: Optional[dict] = None, 
        seed: int = 42,
    ):
        self.name = name
        TRANSCRIPT_DIR.mkdir(exist_ok=True)

        self._confirm_queue: deque[str] = deque()
        self._exchanges: list[dict] = []
        self._started = datetime.now(timezone.utc).isoformat()

        log_path = TRANSCRIPT_DIR / f"{name}_actions.jsonl"
        self.action_log = ActionLog(log_file=log_path)

        _adapters = adapters or {
        "uber": UberMockAdapter(seed=seed),
        "lyft": LyftMockAdapter(seed=seed),
        }

        self.agent = RideAgent(
            adapters=_adapters,
            action_log=self.action_log,
            confirm_callback=self._auto_confirm,
        )
        self._platform = "Uber + Lyft"

    # ── Confirmation injection ──────────────────────────────────────────────

    def queue_confirm(self, response: str) -> "ScenarioRunner":
        """Pre-program the next user confirmation response. Chainable."""
        self._confirm_queue.append(response)
        return self

    def _auto_confirm(self, prompt: str, details: dict) -> str:
        response = self._confirm_queue.popleft() if self._confirm_queue else "yes"

        self._record({
            "type": "confirmation_gate",
            "prompt": prompt,
            "details": details,
            "auto_response": response,
        })

        # Print so the scenario output shows the gate firing
        short_prompt = prompt.split("\n")[0]
        print(f"\n  [GATE] {short_prompt}")
        print(f"  [GATE] Auto-response: '{response}'")
        return response

    # ── Conversation ────────────────────────────────────────────────────────

    def turn(self, user_message: str) -> str:
        """Send one user message, print and record the exchange."""
        print(f"\n  User : {user_message}")
        response = self.agent.chat(user_message)
        print(f"\n  Agent: {_wrap(response)}")

        self._record({"type": "user", "content": user_message})
        self._record({"type": "agent", "content": response})
        return response

    # ── Persistence ─────────────────────────────────────────────────────────

    def save(self) -> Path:
        """Write JSON transcript + Markdown summary."""
        data = {
            "scenario": self.name,
            "platform": self._platform,
            "started": self._started,
            "finished": datetime.now(timezone.utc).isoformat(),
            "action_log": self.action_log.as_list(),
            "exchanges": self._exchanges,
        }

        json_path = TRANSCRIPT_DIR / f"{self.name}.json"
        md_path = TRANSCRIPT_DIR / f"{self.name}.md"

        json_path.write_text(json.dumps(data, indent=2, default=str))
        md_path.write_text(self._render_markdown(data))

        print(f"\n  [Saved] {json_path.name}  |  {md_path.name}")
        print(self.action_log.summary())
        return json_path

    # ── Private ─────────────────────────────────────────────────────────────

    def _record(self, item: dict) -> None:
        self._exchanges.append(item)

    def _render_markdown(self, data: dict) -> str:
        title = data["scenario"].replace("_", " ").title()
        lines = [
            f"# Transcript: {title}",
            f"**Platform:** {data['platform']}  |  **Started:** {data['started']}",
            "",
            "## Conversation",
            "",
        ]
        for ex in data["exchanges"]:
            if ex["type"] == "user":
                lines.append(f"**User:** {ex['content']}")
                lines.append("")
            elif ex["type"] == "agent":
                lines.append(f"**Agent:** {ex['content']}")
                lines.append("")
            elif ex["type"] == "confirmation_gate":
                lines.append(f"> **[Confirmation Gate]** {ex['prompt'].splitlines()[0]}")
                lines.append(f"> Auto-response: `{ex['auto_response']}`")
                lines.append("")

        lines += [
            "## Action Log",
            "",
            "| # | Action | Status | User Verified |",
            "|---|--------|--------|---------------|",
        ]
        for i, entry in enumerate(data["action_log"], 1):
            verified = (
                "yes" if entry.get("verified_by_user") is True
                else "no" if entry.get("verified_by_user") is False
                else "—"
            )
            lines.append(f"| {i} | `{entry.get('action_type', '?')}` | {entry.get('status', '?')} | {verified} |")

        return "\n".join(lines) + "\n"


def _wrap(text: str, width: int = 72) -> str:
    """Wrap long agent responses for readable terminal output."""
    lines = text.splitlines()
    wrapped = []
    for line in lines:
        if len(line) <= width:
            wrapped.append(line)
        else:
            wrapped.extend(textwrap.wrap(line, width=width, subsequent_indent="         "))
    return "\n         ".join(wrapped)
