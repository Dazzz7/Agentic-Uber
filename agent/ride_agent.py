"""
RideAgent — Claude-powered agent loop.

Setup:
    pip install anthropic python-dotenv
    Add ANTHROPIC_API_KEY=your_key to .env file
"""

import json
from typing import Callable, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

from adapters.base import PlatformAdapter
from agent.action_log import ActionLog
from agent.tools import RideAgentTools

SYSTEM_PROMPT = """\
You are a helpful ride-booking assistant. You support Uber and Lyft.
You can help users discover, compare, book, track, and cancel rides.

── PLATFORM SELECTION (do this first) ──────────────────────────────
0.  If the user mentions Uber or Lyft explicitly, call select_platform immediately.
    If the user asks to compare platforms, call compare_platforms first, then call select_platform after the user picks one.
    After compare_platforms, do NOT call search_rides again — the comparison results already contain all ride options and ETAs. Use them directly.
    If the user doesn't mention a platform, call select_platform("uber") automatically before doing anything else — Uber is the default.
    Never call search_rides or book_ride before select_platform.

── BOOKING WORKFLOW (non-negotiable) ───────────────────────────────
1.  Always call search_rides first before any booking action.
2.  When the user names a ride type, call request_booking_confirmation EXACTLY ONCE — never twice, never zero times.
3.  When request_booking_confirmation returns approved=True:
    - Call book_ride IN THE SAME TURN before writing ANY response to the user.
    - You do NOT have driver details until book_ride returns — never use placeholders like [Driver Name] or [Vehicle Details].
    - If you find yourself writing placeholders, STOP — call book_ride first.
    - Do NOT ask "would you like to proceed", "shall I book", or anything similar. The user already confirmed. Just call book_ride immediately.
4.  Pass the confirmation_token returned by request_booking_confirmation directly to book_ride.
    Pass the confirmation_token returned by request_cancel_confirmation directly to cancel_ride.
    Never fabricate or reuse a token — always use the exact token returned.
5.  The FIRST thing you say after book_ride succeeds must include ALL of: driver name, driver rating, vehicle color/make/model, license plate,
    and ETA. No exceptions. Do not defer this to a later message.

── TRACKING WORKFLOW ────────────────────────────────────────────────
6.  ALWAYS call track_ride when the user asks for any status, update, tracking, or location — even if you just called it moments ago.
    NEVER answer tracking questions from memory.
7.  Never call track_ride immediately after booking — only call it when the user explicitly asks.
8.  Interpret track_ride status values as follows:
    - arriving      → driver is on the way, give ETA
    - driver_arrived → tell user their driver is waiting outside
    - in_progress   → tell user ride has started, give eta_minutes to destination
    - completed     → tell user they have arrived, give total trip time
    - cancelled     → tell user the ride was cancelled
    - not_found     → tell user the ride ID was not recognised

── CANCELLATION WORKFLOW ────────────────────────────────────────────
9.  Always call request_cancel_confirmation before cancel_ride. Never skip or combine these steps.
10. The confirmation_token is INTERNAL — never show it to the user, never ask the user to provide it. Pass it directly to cancel_ride.

── EDGE CASES ───────────────────────────────────────────────────────
11. If surge_active is true in search results, warn the user explicitly before calling request_booking_confirmation.
12. If search_rides returns address_error, tell the user clearly and ask for a corrected address. Do not attempt to book.

── GENERAL ──────────────────────────────────────────────────────────
13. Keep responses concise and friendly.
    Never ask unnecessary follow-up questions after completing an action.
"""


class RideAgent:
    """
    Wraps the Anthropic Claude API with a tool-calling loop.
    Supports multiple platforms — platform is selected via select_platform tool.

    History is managed as a plain list of message dicts.
    System prompt is passed separately on every API call — not in history.
    Call reset() to start a fresh conversation while keeping the action log.
    """

    def __init__(
        self,
        adapters: dict,
        action_log: ActionLog,
        confirm_callback: Optional[Callable] = None,
        model: str = "claude-sonnet-4-5",
    ):
        self.model = model
        self.client = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from env
        self.adapters = adapters
        self.active_adapter = None
        self.action_log = action_log
        self._confirm = confirm_callback

        # tools_impl starts with first adapter as placeholder
        # all_adapters passed for compare_platforms
        first_adapter = next(iter(adapters.values()))
        self.tools_impl = RideAgentTools(
            adapter=first_adapter,
            all_adapters=adapters,
            action_log=action_log,
            confirm_callback=confirm_callback,
        )

        self._tools = self.tools_impl.get_tool_definitions()
        self._system_prompt = SYSTEM_PROMPT    # no {platform} formatting needed
        self.history: list[dict] = []   # no system message in history for Claude

    # ── select_platform ──────────────────────────────────────────────────────────
    def _select_platform(self, platform: str) -> dict:
        """Swap the active adapter. Called when LLM invokes select_platform tool."""
        adapter = self.adapters.get(platform.lower())
        if adapter is None:
            return {
                "error": f"Unknown platform '{platform}'.",
                "valid_platforms": list(self.adapters.keys()),
            }

        self.active_adapter = adapter
        # Rebuild tools_impl with new active adapter
        self.tools_impl = RideAgentTools(
            adapter=adapter,
            all_adapters=self.adapters,
            action_log=self.action_log,
            confirm_callback=self._confirm,
        )

        return {
            "success": True,
            "platform": adapter.get_platform_name(),
            "message": (
                f"Switched to {adapter.get_platform_name()}. "
                f"Ready to search and book rides."
            ),
        }

    # ── Public API ──────────────────────────────────────────────────────────

    def chat(self, user_message: str) -> str:
        """Send a user message; return the final assistant text response."""
        self.history.append({"role": "user", "content": user_message})

        while True:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=self._system_prompt,
                tools=self._tools,
                messages=self.history,
            )

            assistant_content = []
            text_response = ""

            for block in response.content:
                if block.type == "text":
                    text_response = block.text
                    assistant_content.append({
                        "type": "text",
                        "text": block.text,
                    })
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            self.history.append({
                "role": "assistant",
                "content": assistant_content,
            })

            if response.stop_reason == "end_turn":
                return text_response

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":

                    # select_platform handled here, not in tools_impl
                    if block.name == "select_platform":
                        result = self._select_platform(block.input["platform"])
                    elif self.active_adapter is None and block.name not in ("compare_platforms",):
                        # Platform not selected yet — reject and tell model to select first
                        result = {
                            "error": "no_platform_selected",
                            "message": "You must call select_platform before any other action. Default is 'uber'."
                        }
                    else:
                        result = self.tools_impl.dispatch(block.name, block.input)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    })

            self.history.append({
                "role": "user",
                "content": tool_results,
            })
            # Loop: send updated history back to Claude

    def reset(self) -> None:
        """Clear conversation history and reset platform selection."""
        self.history = []
        self.active_adapter = None