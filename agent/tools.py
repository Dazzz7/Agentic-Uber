"""
Agent tool implementations with confirmation gates.

The confirmation gate works like a two-phase commit:
  1. Agent calls request_booking_confirmation()   → user sees details, returns token
  2. Agent calls book_ride(confirmation_token=…)  → token consumed, booking executes

Tokens are single-use UUIDs stored in memory. A booking or cancellation
attempted without a valid token is rejected and logged as failed.

The gate is enforced in code, not by instruction to the LLM — the LLM
can't book even if it tries, because the adapter call is unreachable
without a consumed token.
"""

import json
import uuid
from typing import Callable, Optional

from adapters.base import AddressError, PlatformAdapter
from agent.action_log import ActionLog


_TOOL_DEFINITIONS = [
    {
        "name": "search_rides",
        "description": (
            "Search all available ride options between a pickup and dropoff location. "
            "Returns ride types, price ranges, ETAs, and surge info. "
            "Always call this first before booking."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pickup":  {"type": "string", "description": "Pickup address or landmark"},
                "dropoff": {"type": "string", "description": "Destination address or landmark"},
            },
            "required": ["pickup", "dropoff"],
        },
    },
    {
        "name": "get_price_estimate",
        "description": "Get a detailed price estimate for a specific ride type.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pickup":    {"type": "string"},
                "dropoff":   {"type": "string"},
                "ride_type": {"type": "string", "description": "e.g. UberX, UberXL, Uber Comfort, Uber Black, UberPool"},
            },
            "required": ["pickup", "dropoff", "ride_type"],
        },
    },
    {
        "name": "compare_platforms",
        "description": (
            "Compare ride prices across all available platforms for the same route. "
            "Call this when the user wants to compare platforms before deciding. "
            "Returns options from all platforms side by side. "
            "Call this before select_platform if user hasn't chosen a platform yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pickup":  {"type": "string", "description": "Pickup address or landmark"},
                "dropoff": {"type": "string", "description": "Destination address or landmark"},
            },
            "required": ["pickup", "dropoff"],
        },
    },
    {
        "name": "select_platform",
        "description": (
            "Select the ride platform to use (uber or lyft). "
            "MUST be called before search_rides, book_ride, or any platform-specific action. "
            "Call this as soon as the user mentions a platform, or after compare_platforms "
            "when the user has chosen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["uber", "lyft"],
                    "description": "The platform the user wants to use"
                }
            },
            "required": ["platform"],
        },
    },
    {
        "name": "request_booking_confirmation",
        "description": (
            "REQUIRED before booking: present the booking details to the user and obtain confirmation. "
            "Returns a confirmation_token if the user approves. "
            "You MUST call this before book_ride — a booking without a token will be rejected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pickup":          {"type": "string"},
                "dropoff":         {"type": "string"},
                "ride_type":       {"type": "string"},
                "estimated_price": {"type": "string", "description": "e.g. '$24–$31'"},
                "eta_minutes":     {"type": "integer", "description": "Estimated pickup wait in minutes"},
                "surge_note":      {"type": "string", "description": "Surge warning if applicable, otherwise empty string"},
            },
            "required": ["pickup", "dropoff", "ride_type", "estimated_price", "eta_minutes"],
        },
    },
    {
        "name": "book_ride",
        "description": (
            "Book the ride. REQUIRES a valid confirmation_token from request_booking_confirmation. "
            "Will be rejected if no token is provided or the token has already been used."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pickup":             {"type": "string"},
                "dropoff":            {"type": "string"},
                "ride_type":          {"type": "string"},
                "confirmation_token": {"type": "string", "description": "Token returned by request_booking_confirmation"},
            },
            "required": ["pickup", "dropoff", "ride_type", "confirmation_token"],
        },
    },
    {
        "name": "track_ride",
        "description": ("Get the CURRENT real-time status of a booked ride. "
            "MUST be called every single time the user asks for any update, "
            "status, tracking, or location — even if you just called it. "
            "NEVER answer from memory. Each call returns a new state: "
            "arriving → 2 min away → driver arrived → ride started → completed. "
            "The ride progresses ONLY by calling this tool."),
        "input_schema": {
            "type": "object",
            "properties": {
                "ride_id": {"type": "string", "description": "The ride_id returned by book_ride"},
            },
            "required": ["ride_id"],
        },
    },
    {
        "name": "request_cancel_confirmation",
        "description": (
            "REQUIRED before cancelling: present cancellation details to the user. "
            "Returns a confirmation_token if approved. Note that a cancellation fee may apply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ride_id": {"type": "string"},
                "reason":  {"type": "string", "description": "Reason for cancellation"},
            },
            "required": ["ride_id"],
        },
    },
    {
        "name": "cancel_ride",
        "description": (
            "Cancel a booked ride. REQUIRES a valid confirmation_token from request_cancel_confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ride_id":            {"type": "string"},
                "confirmation_token": {"type": "string"},
            },
            "required": ["ride_id", "confirmation_token"],
        },
    },
]


class RideAgentTools:
    """
    Implements all agent tools and enforces the confirmation gate.

    Parameters
    ----------
    adapter : PlatformAdapter
        The ride platform (Uber, Lyft, …).
    action_log : ActionLog
        Every action is logged before and after execution.
    confirm_callback : Callable | None
        Called with (prompt_text, details_dict) → str response.
        Defaults to stdin prompt. Override for automated scenarios.
    """

    def __init__(
        self,
        adapter: PlatformAdapter,
        all_adapters: dict, # {"uber": UberMockAdapter, "lyft": LyftMockAdapter}
        action_log: ActionLog,
        confirm_callback: Optional[Callable] = None,
    ):
        self.adapter = adapter  # currently active adapter
        self.all_adapters = all_adapters   # all platforms for comparison
        self.log = action_log
        self._confirm = confirm_callback or self._stdin_confirm
        self._tokens: dict[str, dict] = {}  # live confirmation tokens

    # ── Tool: search_rides ──────────────────────────────────────────────────

    def search_rides(self, pickup: str, dropoff: str) -> dict:
        entry = self.log.start("search_rides", {"pickup": pickup, "dropoff": dropoff})
        try:
            options = self.adapter.search_rides(pickup, dropoff)
            result = {
                "platform": self.adapter.get_platform_name(),
                "pickup": pickup,
                "dropoff": dropoff,
                "ride_count": len(options),
                "options": [
                    {
                        "option_id": o.option_id,
                        "ride_type": o.ride_type,
                        "price_range": o.price_display,
                        "price_min": o.estimated_price_min,
                        "price_max": o.estimated_price_max,
                        "surge_multiplier": o.surge_multiplier,
                        "surge_active": o.surge_multiplier > 1.0,
                        "eta_pickup_minutes": o.eta_pickup_minutes,
                        "capacity": o.capacity,
                    }
                    for o in options
                ],
            }
            self.log.complete(entry, result)
            return result
        except AddressError as exc:
            err = {"error": "address_error", "message": str(exc)}
            self.log.fail(entry, err)
            return err
        except Exception as exc:
            err = {"error": "platform_error", "message": str(exc)}
            self.log.fail(entry, err)
            return err

    # ── Tool: get_price_estimate ────────────────────────────────────────────

    def get_price_estimate(self, pickup: str, dropoff: str, ride_type: str) -> dict:
        entry = self.log.start("get_price_estimate", {"pickup": pickup, "dropoff": dropoff, "ride_type": ride_type})
        try:
            opt = self.adapter.get_price_estimate(pickup, dropoff, ride_type)
            result = {
                "ride_type": opt.ride_type,
                "price_range": opt.price_display,
                "price_min": opt.estimated_price_min,
                "price_max": opt.estimated_price_max,
                "surge_multiplier": opt.surge_multiplier,
                "eta_pickup_minutes": opt.eta_pickup_minutes,
            }
            self.log.complete(entry, result)
            return result
        except Exception as exc:
            err = {"error": str(exc)}
            self.log.fail(entry, err)
            return err
        
    # ── Tool: compare_platforms ────────────────────────────────────────────
        
    def compare_platforms(self, pickup: str, dropoff: str) -> dict:
        entry = self.log.start("compare_platforms", {"pickup": pickup, "dropoff": dropoff})

        comparison = {}
        errors = {}

        for platform_key, adapter in self.all_adapters.items():
            try:
                options = adapter.search_rides(pickup, dropoff)
                comparison[platform_key] = {
                    "platform": adapter.get_platform_name(),
                    "options": [
                        {
                            "ride_type":    o.ride_type,
                            "price_range":  o.price_display,
                            "price_min":    o.estimated_price_min,
                            "price_max":    o.estimated_price_max,
                            "surge_active": o.surge_multiplier > 1.0,
                            "eta_minutes":  o.eta_pickup_minutes,
                            "capacity":     o.capacity,
                        }
                        for o in options
                    ],
                }
            except AddressError as exc:
                errors[platform_key] = {"error": "address_error", "message": str(exc)}
            except Exception as exc:
                errors[platform_key] = {"error": "platform_error", "message": str(exc)}

        result = {
            "pickup":     pickup,
            "dropoff":    dropoff,
            "comparison": comparison,
            "errors":     errors,
        }
        self.log.complete(entry, result)
        return result
    

    # ── Tool: request_booking_confirmation (GATE) ───────────────────────────

    def request_booking_confirmation(
        self,
        pickup: str,
        dropoff: str,
        ride_type: str,
        estimated_price: str,
        eta_minutes: int,
        surge_note: str = "",
    ) -> dict:
        
        pickup_fmt  = self._format_address(pickup)
        dropoff_fmt = self._format_address(dropoff)
        details = {
            "platform": self.adapter.get_platform_name(),
            "pickup": pickup_fmt,
            "dropoff": dropoff_fmt,
            "ride_type": ride_type,
            "estimated_price": estimated_price,
            "eta_pickup_minutes": eta_minutes,
            "surge_note": surge_note,
        }
        entry = self.log.start("request_booking_confirmation", details)

        surge_line = f"\n  ⚠  Surge: {surge_note}" if surge_note else ""
        prompt = (
            f"BOOKING CONFIRMATION REQUIRED\n"
            f"  Platform : {self.adapter.get_platform_name()}\n"
            f"  Ride type: {ride_type}\n"
            f"  From     : {pickup_fmt}\n"
            f"  To       : {dropoff_fmt}\n"
            f"  Price    : {estimated_price}\n"
            f"  ETA      : {eta_minutes} min{surge_line}"
        )

        response = self._confirm(prompt, details)
        approved = response.strip().lower() in ("yes", "y")

        if approved:
            token = uuid.uuid4().hex
            self._tokens[token] = details
            result = {
                "approved": True,
                "confirmation_token": token,
                "message": "User confirmed. Pass confirmation_token to book_ride.",
            }
            self.log.complete(entry, result, verified=True)
        else:
            result = {"approved": False, "message": "User declined the booking."}
            self.log.complete(entry, result, verified=False)

        return result
    
    def _format_address(self, address: str) -> str:
        """Return formatted address from adapter, fall back to original if invalid."""
        result = self.adapter.validate_address(address)
        return result.get("formatted", address) if result.get("valid") else address

    # ── Tool: book_ride ─────────────────────────────────────────────────────

    def book_ride(self, pickup: str, dropoff: str, ride_type: str, confirmation_token: str) -> dict:
        entry = self.log.start(
            "book_ride",
            {"pickup": pickup, "dropoff": dropoff, "ride_type": ride_type, "token_provided": bool(confirmation_token)},
        )

        # Gate: consume token
        confirmed = self._tokens.pop(confirmation_token, None)
        if confirmed is None:
            err = {
                "error": "invalid_confirmation_token",
                "message": (
                    "Booking rejected: no valid confirmation token. "
                    "Call request_booking_confirmation first and pass the returned token."
                ),
            }
            self.log.fail(entry, err)
            return err

        try:
            booked = self.adapter.book_ride(pickup, dropoff, ride_type)
            result = {
                "success": True,
                "ride_id": booked.ride_id,
                "driver": booked.driver_name,
                "driver_rating": booked.driver_rating,
                "vehicle": f"{booked.vehicle_color} {booked.vehicle_make} {booked.vehicle_model}",
                "license_plate": booked.license_plate,
                "eta_minutes": booked.eta_minutes,
                "status": booked.status,
                "message": (
                    f"Booked! {booked.driver_name} ({booked.driver_rating}★) is on the way "
                    f"in a {booked.vehicle_color} {booked.vehicle_make} {booked.vehicle_model} "
                    f"({booked.license_plate}). Pickup in ~{booked.eta_minutes} min."
                ),
            }
            entry.executed = {"pickup": pickup, "dropoff": dropoff, "ride_type": ride_type}
            self.log.complete(entry, result, verified=True)
            return result
        except Exception as exc:
            err = {"error": str(exc)}
            self.log.fail(entry, err)
            return err

    # ── Tool: track_ride ────────────────────────────────────────────────────

    def track_ride(self, ride_id: str) -> dict:
        entry = self.log.start("track_ride", {"ride_id": ride_id})
        try:
            info = self.adapter.track_ride(ride_id)
            result = {
                "ride_id": ride_id,
                "status": info.status,
                "eta_minutes": info.eta_minutes,
                "message": info.message,
                "distance_remaining_miles": info.distance_remaining_miles,
                "driver_location": (
                    {"lat": info.driver_lat, "lng": info.driver_lng}
                    if info.driver_lat is not None else None
                ),
            }
            self.log.complete(entry, result)
            return result
        except Exception as exc:
            err = {"error": str(exc)}
            self.log.fail(entry, err)
            return err

    # ── Tool: request_cancel_confirmation (GATE) ────────────────────────────

    def request_cancel_confirmation(self, ride_id: str, reason: str = "") -> dict:
        details = {"ride_id": ride_id, "reason": reason or "not specified"}
        entry = self.log.start("request_cancel_confirmation", details)

        prompt = (
            f"CANCELLATION CONFIRMATION REQUIRED\n"
            f"  Ride ID: {ride_id}\n"
            f"  Reason : {reason or 'not specified'}\n"
            f"  Note   : A cancellation fee may apply if the driver is already on the way."
        )

        response = self._confirm(prompt, details)
        approved = response.strip().lower() in ("yes", "y")

        if approved:
            token = uuid.uuid4().hex
            self._tokens[token] = details
            result = {"approved": True, "confirmation_token": token}
            self.log.complete(entry, result, verified=True)
        else:
            result = {"approved": False, "message": "User declined cancellation."}
            self.log.complete(entry, result, verified=False)

        return result

    # ── Tool: cancel_ride ───────────────────────────────────────────────────

    def cancel_ride(self, ride_id: str, confirmation_token: str) -> dict:
        entry = self.log.start("cancel_ride", {"ride_id": ride_id, "token_provided": bool(confirmation_token)})

        confirmed = self._tokens.pop(confirmation_token, None)
        if confirmed is None:
            err = {
                "error": "invalid_confirmation_token",
                "message": "Cancellation rejected: call request_cancel_confirmation first.",
            }
            self.log.fail(entry, err)
            return err

        try:
            result = self.adapter.cancel_ride(ride_id)
            entry.executed = {"ride_id": ride_id}
            self.log.complete(entry, result, verified=True)
            return result
        except Exception as exc:
            err = {"error": str(exc)}
            self.log.fail(entry, err)
            return err

    # ── Tool registry ───────────────────────────────────────────────────────

    def get_tool_definitions(self) -> list:
        return _TOOL_DEFINITIONS

    def dispatch(self, name: str, inputs: dict) -> dict:
        table = {
            "search_rides":                  self.search_rides,
            "get_price_estimate":            self.get_price_estimate,
            "compare_platforms":             self.compare_platforms,
            "request_booking_confirmation":  self.request_booking_confirmation,
            "book_ride":                     self.book_ride,
            "track_ride":                    self.track_ride,
            "request_cancel_confirmation":   self.request_cancel_confirmation,
            "cancel_ride":                   self.cancel_ride,
        }
        fn = table.get(name)
        if fn is None:
            return {"error": f"Unknown tool: {name}"}
        return fn(**inputs)

    # ── Default confirm callback ────────────────────────────────────────────

    @staticmethod
    def _stdin_confirm(prompt: str, _details: dict) -> str:
        bar = "=" * 62
        print(f"\n{bar}")
        print(prompt)
        print(bar)
        return input("Type 'yes' to confirm, anything else to cancel: ")
