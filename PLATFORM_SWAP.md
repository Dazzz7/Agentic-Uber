# How to Swap Uber for a Different Marketplace

## The One-File Contract

Every platform adapter must implement seven methods defined in `adapters/base.py`:

```
validate_address(address)          → {valid, formatted, lat, lng}
search_rides(pickup, dropoff)      → List[RideOption]
get_price_estimate(pickup, dropoff, ride_type) → RideOption
book_ride(pickup, dropoff, ride_type) → BookedRide
track_ride(ride_id)                → RideTrackingInfo
cancel_ride(ride_id)               → {success, fee, message}
get_platform_name()                → str
```

The agent, tools layer, confirmation gates, and action log never import anything from `adapters/uber_mock.py`. They only know about these seven methods. **Swapping platforms = writing one new file.**

---

## What Changes for Each Platform

### Lyft

| Concern | Uber | Lyft |
|---------|------|------|
| Auth | `Authorization: Token <server_token>` | OAuth2 client_credentials |
| Base URL | `https://api.uber.com/v1.2/` | `https://api.lyft.com/v1/` |
| Price estimate | `GET /estimates/price` | `GET /cost` |
| Booking | `POST /requests` | `POST /rides` |
| Ride types | `UberPool`, `UberX`, `Uber Comfort`, `UberXL`, `Uber Black` | `Lyft Shared`, `Lyft Standard`, `Lyft Comfort`, `Lyft XL`,`Lyft Lux` |
| Tracking | Two endpoints: `/requests/{id}` + `/requests/{id}/map` | Single `GET /rides/{id}` (driver embedded) |
| Surge | `surge_confirmation_id` round-trip | `cost_token` echoed back on booking |
| Cancellation | `DELETE /requests/{id}` | `POST /rides/{id}/cancel` |

# ─────────────────────────────────────────────────────────
**What stays the same:** 
the confirmation gate, the action log schema, `adapters/base.py`, and all core logic inside `agent/`. 

Note: Adding a third platform like Bolt or Via requires zero further changes to any of these files (passing an `adapters` dict instead of a single adapter).

---

### Zocdoc (Medical Transport — radically different domain)

Zocdoc is a useful extreme case because it proves the abstraction generalizes beyond commodity ride-sharing.

| Adapter method | Uber meaning | Zocdoc meaning |
|----------------|--------------|----------------|
| `search_rides` | Search car options by price | Search available medical transport providers by insurance / appointment time |
| `get_price_estimate` | Price range for ride type | Insurance copay + out-of-pocket estimate |
| `book_ride` | Dispatch a driver | Schedule a transport slot tied to an appointment |
| `track_ride` | Driver GPS position | Transport status: confirmed / en route / arrived |
| `cancel_ride` | Cancel trip | Cancel transport reservation (may have clinic notification side-effect) |
| `validate_address` | Geocode street address | Validate clinic address + check it's in the transport network |

The tool definitions in `agent/tools.py` would need descriptions updated for medical context (e.g., "ride_type" → "transport_type"), but the **gate logic, logging, and agent loop require zero changes**.

---

## What Breaks First

When you swap from Uber to a new platform, issues surface in this order:

### 1. Ride type name mismatch (breaks immediately)
The agent carries ride type strings across tool calls. If Lyft returns `"lyft_xl"` but the user or agent says `"UberXL"`, booking fails.
**Fix:** Each adapter's `search_rides()` must return canonical names for *that* platform. The agent learns them from search results, never hardcodes. Can add a block of locked/ unlocked state while using the current platform.

### 2. Surge confirmation flow (breaks on surge)
Uber's surge requires the client to echo a `surge_confirmation_id`.
Lyft requires echoing a `cost_token`.
The current adapter interface doesn't expose this round-trip — a `confirm_surge(token)` method would need to be added to the base class and a corresponding agent tool created.
**Scope:** 1 new method + 1 new tool + 1 new gate.

### 3. Tracking data schema (breaks on track)
Uber separates driver location into a `/map` endpoint.
Lyft embeds it in the ride object. The `RideTrackingInfo` dataclass handles both (lat/lng are Optional), but an adapter that doesn't
populate `eta_minutes` will cause the agent to give wrong info.
**Fix:** Adapter implementer must map platform fields to dataclass fields precisely — this is the most error-prone step.

### 4. Cancellation fee disclosure (breaks UX, not code)
Uber's cancellation fee is returned in the DELETE response body.
Lyft's fee is a separate API call. If an adapter silently returns `cancellation_fee: 0` without actually checking, users get surprised.
**Fix:** Adapter contract documentation should require honest fee reporting; integration tests should simulate the fee-applies path.

### 5. Geocoding / address validation (domain-specific)
Uber and Lyft both accept free-text addresses routed through Google Maps.
Zocdoc requires clinic IDs, not street addresses.
The `validate_address` method signature handles both, but callers expecting lat/lng may need to treat the `formatted` field as an opaque identifier for non-geo domains.

---

## Summary

The adapter boundary is narrow and well-defined. The agent, tools, gate, and log are completely platform-agnostic. The two most likely sources of real integration pain are: (1) ride type name translation and (2) the platform-specific surge confirmation handshake, neither of which requires changes above the adapter layer.
