# Ride Booking Agent — Architecture, Design Decisions & Assumptions

## Overview

This document walks through every decision made in building the ride-booking agent:
what the design assumes, why each layer exists, where the tradeoffs land, and what
would need to change in a real production deployment.

---

## 1. The Core Problem Decomposition

The prompt asked for four distinct things that map to four distinct layers:

| Requirement | What it became |
|-------------|----------------|
| "Discover options, compare prices, book, track, cancel" | Seven agent tools |
| "Platform adapter (swappable)" | `adapters/base.py` — abstract class + two implementations |
| "Confirmation gates" | Two-phase commit with single-use tokens inside `agent/tools.py` |
| "Action log — requested, verified, executed, result" | `agent/action_log.py` — append-only JSONL |

The key insight is that these four concerns are **genuinely independent**. The agent
loop doesn't know what platform it's on. The platform doesn't know the agent exists.
The action log doesn't know what triggered it. The gate is enforced in code, not in
the LLM prompt. This separation is not just clean code — it's what makes each piece
testable, replaceable, and auditable in isolation.

---

## 2. The Platform Adapter Layer

### 2.1 Why an abstract base class?

The natural instinct for "swappable" is an interface. Python's `abc.ABC` gives us
that: if a subclass skips implementing any of the seven abstract methods, Python
raises `TypeError` at instantiation time — not at runtime when the method is called.
This catches missed implementations early.

The alternative would be duck typing (no base class, just convention). That works
fine until someone adds a new platform, forgets one method, and the agent silently
fails only on the code path that calls that method. The ABC makes the contract
explicit and mechanically enforced.

### 2.2 The seven methods and why exactly seven

```
validate_address    — separate from search so the agent can give early feedback
search_rides        — returns ALL options; agent picks based on user preference
get_price_estimate  — targeted query for one ride type after the user has decided
book_ride           — the state-changing action (only called after confirmation)
track_ride          — read-only polling; can be called many times
cancel_ride         — second state-changing action (also gated)
get_platform_name   — pure metadata; used in prompts, logs, and confirmation messages
```

`validate_address` exists as a separate method because bad addresses are a common
failure mode and the agent should detect them *before* calling `search_rides`, which
would fail with a less useful error. In practice, the mock adapter's `search_rides`
calls `_geocode` internally anyway, so they share the same lookup logic — but the
agent tool for address validation can give the user a clean "that address doesn't
look right, can you try again?" before doing any ride search.

`get_price_estimate` is technically redundant with `search_rides` (the agent could
filter search results). It exists because real platform APIs have separate endpoints
for these — Uber's `/estimates/price` vs. a specific product price quote — and the
agent may need to refresh a specific price without re-fetching all options. It also
gives the agent a way to answer "what would UberX cost?" without triggering the full
search flow.

### 2.3 Domain objects: dataclasses over dicts

`RideOption`, `BookedRide`, and `RideTrackingInfo` are `@dataclass` objects, not
raw dicts. This matters for two reasons:

1. **The adapter-to-tools boundary is typed.** The tools layer knows exactly what
   fields exist on a `BookedRide`. If a new platform returns data in a slightly
   different shape, the adapter is responsible for mapping it to the dataclass — the
   tools layer never has to handle missing keys.

2. **`price_display` as a computed property.** The `RideOption.price_display`
   property formats the price string and automatically appends the surge multiplier
   if active. The adapter doesn't decide the display format; the domain object does.
   This means all adapters produce the same display output automatically.

### 2.4 AddressError and PlatformError as distinct exception types

Two exception classes are defined in `base.py`:

- `AddressError` — the user gave a bad or unresolvable address. The agent should
  catch this and ask the user for clarification. It is a *recoverable* error.

- `PlatformError` — something went wrong on the platform side (API down, auth
  expired, rate limited). The agent should report it as a system error. It is
  *not* the user's fault.

The tools layer catches these distinctly. `AddressError` becomes
`{"error": "address_error", "message": "..."}` which the agent surfaces as a
helpful prompt. Any other exception becomes `{"error": "platform_error", ...}`.
Without this distinction, the agent would give users error messages like
"rate limit exceeded" when they mistyped an address.

### 2.5 The mock adapter's geocoding strategy

The mock uses a lookup table of California landmarks. The strategy for unknown addresses:

- If the string contains any of a hardcoded list of "obviously bad" signals
  (`asdfjkl`, `nowhere`, `fake`, `qwerty`, etc.) → `AddressError`
- If the string is long enough and contains digits → accept as a plausible address
  and generate random-but-plausible coordinates
- Otherwise → `AddressError`

**Assumption:** This is a deliberate simplification. A real adapter would call
Google Maps Geocoding API or Uber's Places API. The key design assumption is that
the *contract* (raise `AddressError` for bad addresses, return lat/lng for good ones)
is what matters — the implementation is a mock.

### 2.6 Surge pricing as an adapter-level concept

Surge is baked into the adapter constructor (`surge_multiplier` parameter) rather
than being something the agent reasons about independently. The adapter sets the
multiplier on every `RideOption` it returns. The agent tools layer surfaces
`surge_active: true` to the agent when `surge_multiplier > 1.0`. The agent's system
prompt instructs it to warn the user when surge is active.

**Why this design:** Surge is platform-specific. Uber uses `surge_confirmation_id`.
Lyft uses a `cost_token`. The adapter handles the mechanics; the tools layer only
cares about the boolean "is surge happening" and the multiplier value for display.
The agent only cares about warning the user. Three layers, three concerns, none
bleeds into the others.

---

## 3. The Confirmation Gate

### 3.1 Why in code, not in the prompt

The most important architectural decision: the confirmation gate is enforced
**in Python code**, not by telling the LLM "please always ask for confirmation."

Prompt-based gates are unreliable. A sufficiently long conversation, a distracted
user, or a jailbreak attempt can cause the model to skip a "please confirm first"
instruction. If the agent calls `book_ride` directly, the booking still happens.

Code-based gates cannot be circumvented by the LLM. The `book_ride` function in
`tools.py` checks for a valid token on line 1. If there's no token, it returns an
error and logs the failed attempt — no network call to the adapter is made.

### 3.2 Two-phase commit with single-use tokens

The flow mirrors how payment systems handle authorization:

```
Phase 1 — Authorize:
  agent calls request_booking_confirmation(pickup, dropoff, ride_type, price, eta)
  → system shows details to human
  → human types "yes"
  → system generates UUID token, stores in memory dict
  → returns {"approved": true, "confirmation_token": "<uuid>"}

Phase 2 — Execute:
  agent calls book_ride(pickup, dropoff, ride_type, confirmation_token="<uuid>")
  → code calls self._tokens.pop(token, None)
  → if None: reject, log failed, return error (adapter never called)
  → if found: call adapter.book_ride(), log success with verified=True
```

The token is **single-use** (`pop` removes it). If the agent tries to book twice
with the same token, the second call is rejected. This prevents double-booking bugs
if the agent's reasoning loop misfires.

**Assumption:** Tokens are stored in-process memory. In a production system you'd
store them in Redis with a TTL (e.g., 5 minutes). For this implementation, in-process
is fine because each agent session is a single process and tokens don't need to
survive restarts.

### 3.3 Cancellation uses the same gate

The same pattern applies to `cancel_ride`. This is deliberate — cancellations are
irreversible and may incur fees. The agent cannot cancel a ride without the user
first seeing the cancellation details and typing "yes."

### 3.4 The confirm_callback injection pattern

The `RideAgentTools` class takes a `confirm_callback` in its constructor:

```python
def __init__(self, adapter, action_log, confirm_callback=None):
    self._confirm = confirm_callback or self._stdin_confirm
```

The default (`_stdin_confirm`) prints the gate prompt and blocks on `input()`.
For automated scenarios, a different callable is injected that reads from a
pre-programmed queue. This pattern means:

- Interactive mode: real human confirmation via terminal
- Test/scenario mode: scripted responses, fully automated
- Future: GUI callback, REST webhook, mobile push notification approval

The gate logic itself doesn't change — only the callback that collects the human
response.

---

## 4. The Action Log

### 4.1 Schema design: requested → verified → executed → result

Every `ActionEntry` has four distinct fields:

```
requested  — what the agent asked for (raw tool inputs, before validation)
verified   — whether a human confirmed (True/False/None if no gate applies)
executed   — what actually ran (may differ from requested after validation)
result     — what happened (success dict or error dict)
```

`executed` differs from `requested` in scenarios where input is transformed before
hitting the adapter. For example, the agent might pass "JFK" as the dropoff, but the
adapter geocodes it to "John F. Kennedy International Airport, Queens, NY 11430". The
`executed` field records what actually went to the adapter.

**Assumption:** In the current implementation, `executed` defaults to `requested`
for most actions since the mock doesn't transform inputs. A real implementation
would populate `executed` with the canonicalized, geocoded, platform-specific
parameters that were sent to the API.

### 4.2 JSONL format (append-only)

The log is written as newline-delimited JSON (JSONL), one record per line. Each call
to `log.complete()` or `log.fail()` immediately appends to the file.

Why JSONL instead of a database or single JSON file:
- **Append-only is crash-safe.** If the process dies mid-write, no existing records
  are corrupted. A single JSON file would be corrupt.
- **Streamable.** Tools like `jq`, `grep`, and log aggregators (Datadog, Splunk) can
  process JSONL line by line without loading the entire file.
- **Auditable.** An append-only log cannot be silently modified. Records can only be
  added. This matters for accountability ("did the agent really get confirmation?").

### 4.3 Session header record

Each new `ActionLog` writes a `{"_session_start": "...", "_pid": "..."}` header
record as its first line. This makes it easy to separate sessions when multiple runs
append to the same file, and to correlate log entries with system process IDs.

### 4.4 In-memory list + disk persistence

The log maintains both an in-memory list (for `summary()`, in-process queries) and
the JSONL file. The in-memory list allows the scenario runner to embed the action log
in the JSON transcript at the end of a session. The JSONL file survives process
restarts and is the authoritative audit record.

---

## 5. The Agent Loop

### 5.1 Why Claude's tool_use API

The agent uses Claude's native tool calling. The alternative would be a ReAct-style
prompt where the model outputs text like `ACTION: search_rides(...)` and the loop
parses it. Tool use is strictly better:

- **Typed inputs.** The tool schema enforces that `eta_minutes` is an integer, not a
  string that looks like an integer. The model can't pass the wrong type.
- **Parallel tool calls.** Claude can call multiple tools in one turn (e.g., get a
  price estimate for two ride types simultaneously). Text-based ReAct is inherently
  sequential.
- **No parsing fragility.** Text-based action extraction breaks on formatting
  variation. Tool use is structured JSON.

### 5.2 The conversation history format

The history is a list of message dicts following the Anthropic Messages API format:

```
user message → {"role": "user", "content": "I need a ride..."}
assistant turn → {"role": "assistant", "content": [TextBlock, ToolUseBlock, ...]}
tool results → {"role": "user", "content": [{"type": "tool_result", ...}]}
```

**Critical detail:** When the assistant returns both text and tool calls in one turn
(which Claude does when it wants to explain what it's about to do), both blocks must
be in the `content` list. The tool results must reference the `tool_use_id` from the
corresponding `ToolUseBlock`. The history stores `response.content` (the raw list of
blocks) rather than just the text, because the Anthropic API requires the full
content for multi-turn consistency.

### 5.3 The agentic loop structure

```python
while True:
    response = client.messages.create(...)

    text_blocks = [b for b in response.content if b.type == "text"]
    tool_blocks = [b for b in response.content if b.type == "tool_use"]

    history.append({"role": "assistant", "content": response.content})

    if not tool_blocks or stop_reason == "end_turn":
        return concatenated text   # ← done

    # execute tools, collect results
    history.append({"role": "user", "content": tool_results})
    # loop back to the top
```

The loop continues as long as the model returns tool calls. When it returns only
text (or nothing), the conversation turn is complete. This handles chains like:

```
search_rides → [model reasons about results] →
request_booking_confirmation → [user approves] →
book_ride → [model composes confirmation message] → done
```

which can be 3–4 API calls deep within a single user message.

### 5.4 The system prompt's role

The system prompt has two jobs:

1. **Workflow rules** — the order of operations the agent must follow (search before
   book, always call the confirmation tool, warn on surge, select platform before
   any booking action). These are behavioral guardrails that complement the
   code-level gate.

2. **Platform awareness** — the prompt instructs the agent that both Uber and Lyft
   are available and that `select_platform` must be called before any platform-specific
   action. Previously the prompt was templated with a single `{platform}` name —
   this was removed when multi-platform support was added. The agent now learns
   which platform is active from the `select_platform` tool response, not from
   the system prompt.

**What the system prompt does NOT do:** enforce the confirmation gate. It mentions
it as a rule, but the actual enforcement is in `tools.py`. The prompt is redundancy
(belt-and-suspenders), not the primary enforcement mechanism.

### 5.5 Model choice: claude-sonnet-4-5

Sonnet was chosen over Haiku for tool use quality. Sonnet reliably:
- Follows multi-step tool sequences (search → confirm → book)
- Surfaces surge warnings unprompted when the data shows `surge_active: true`
- Passes the `confirmation_token` through correctly without hallucinating values
- Recovers gracefully from `AddressError` responses by asking for clarification

Haiku is faster and cheaper but more likely to skip steps in a multi-tool workflow
under ambiguous instructions. For a booking agent where skipping the confirmation
gate would be a real problem, Sonnet's reliability is worth the cost difference.

---

## 6. The Scenario Runner

### 6.1 Injectable confirmations via a queue

The `ScenarioRunner` injects a `confirm_callback` that reads from a `deque`:

```python
def queue_confirm(self, response: str) -> "ScenarioRunner":
    self._confirm_queue.append(response)
    return self

def _auto_confirm(self, prompt, details):
    response = self._confirm_queue.popleft() if self._confirm_queue else "yes"
    ...
    return response
```

This design means scenarios are fully scripted but still exercise the real gate logic.
The gate fires, the callback is called, the response comes from the queue. If the
queue is empty, it defaults to "yes" (which is intentional — a scenario that forgets
to queue a response shouldn't silently fail in an unrelated way).

### 6.2 Transcript format: JSON + Markdown

Each scenario saves two files:

- `transcripts/<name>.json` — machine-readable; contains the full action log,
  all exchanges, confirmation gate events, platform metadata, and timestamps.
  Suitable for automated analysis, regression testing, or feeding into dashboards.

- `transcripts/<name>.md` — human-readable Markdown with a conversation view
  and an action log table. Suitable for review, sharing, and the "5 transcripts"
  deliverable.

The JSON transcript embeds the action log directly (not just a path reference) so
the transcript is self-contained — you can understand exactly what happened from
the transcript file alone.

### 6.3 Why 5 specific scenarios

| Scenario | What it stress-tests |
|----------|----------------------|
| Happy path | Full end-to-end flow on Uber: platform selection → search → confirm → book → track |
| Price comparison | `compare_platforms` across Uber and Lyft, agent reasons on results, user picks Lyft |
| Bad address | `AddressError` propagates cleanly, agent recovers on same instance |
| Surge pricing | 2.5x surge on both platforms, agent warns proactively, Agent reasons across multiple options without being told which to pick using user hint |
| Cancellation | Book on Lyft then cancel — both gates fire sequentially on same `RideAgentTools` instance |

The bad address scenario is particularly important because it tests that
`AddressError` propagates correctly through tools → agent → user, and that
the subsequent valid request works on the same agent instance (the agent
doesn't get confused by a prior error).

The cancellation scenario confirms that the token store works for sequential
gates — booking consumes one token, then cancellation issues and consumes a
second token, on the same `RideAgentTools` instance.

---

## 7. Key Assumptions

### 7.1 No real API credentials needed

The mock adapter is fully self-contained. Prices, drivers, ETAs, and tracking data
are all generated locally. The only external dependency is the Anthropic API
(for Claude). The system is designed so you can build, test, and demo every
non-LLM component without any third-party accounts.

### 7.2 Single-session, single-user

The current implementation assumes one user per agent instance. There's no session
management, user identity, or authentication. The `RideAgent` holds conversation
history in memory; the `RideAgentTools` holds tokens in memory. Two simultaneous
users would share state, which would be a bug in production.

**Production fix:** Create one `RideAgent` + `RideAgentTools` instance per user
session. Store conversation history and tokens in Redis keyed by session ID.

### 7.3 Token expiry is not implemented

Confirmation tokens never expire. In a real system, a user could confirm a booking,
the agent could crash, and 24 hours later a replay could consume the token and book
a ride. Tokens should expire (5 minutes is reasonable for ride booking).

**Production fix:** Replace the in-memory dict with `{token: (details, expires_at)}`
and check `expires_at` in `_consume_token`.

### 7.4 The action log is append-only and unencrypted

Log entries contain addresses, ride details, and driver information. In production,
this log would need:
- Encryption at rest
- Access controls (only the user and authorized operators can read their entries)
- Retention policies (GDPR deletion requirements)
- Tamper-evident storage (the append-only property is good; a hash chain would be
  better for auditability)

### 7.5 Surge is a constructor parameter, not dynamic

The mock sets surge at construction time. Real surge pricing changes every few
minutes. A real adapter's `search_rides` would fetch current surge from the API
on every call, and the agent might get different surge values between calling
`search_rides` and calling `book_ride`. The confirmation gate is important here —
the user confirms the price they saw at search time, but the actual booking might
incur a slightly different price. A production system should surface this discrepancy.

### 7.6 The Lyft adapter inherits from UberMockAdapter

For the mock, `LyftMockAdapter` subclasses `UberMockAdapter` and overrides
`get_platform_name`, `search_rides`, and `book_ride`. This is fine for demonstrating
swappability but would be wrong in production — a real Lyft adapter would not
inherit from the Uber implementation. It would implement `PlatformAdapter` directly
and call Lyft's API endpoints.

The inheritance is a shortcut to avoid duplicating the geocoding and haversine math
in the demo. In production, those utilities would live in a shared `utils/geo.py`
that both adapters import independently.

---

## 8. What a Production Deployment Would Add

### Adapter layer
- Real HTTP client (httpx or aiohttp) with retry logic and circuit breakers
- OAuth2 token refresh for platforms that require it
- Rate limiting and back-pressure handling
- Webhook receivers for push-based status updates (instead of polling `track_ride`)

### Gate layer
- Token expiry (Redis TTL)
- Per-user token namespacing
- Audit trail of who approved what and when (user ID, IP, timestamp)
- Rejection of gate re-use within a cooldown window (prevent double-booking race)

### Agent layer
- Streaming responses (for real-time display as the agent types)
- Multi-turn context management (trim history if it grows too long)
- Fallback behavior if the model exceeds context (summarize + continue)

### Action log
- Structured log shipping (Datadog, Splunk, CloudWatch)
- PII masking before shipping
- Idempotency keys on log entries (so duplicate writes don't create duplicate records)

### Observability
- Latency tracking per tool call and per platform API call
- Error rate dashboards per adapter
- Confirmation gate abandonment rate (how often do users decline?)
- Surge-related booking decline rate

---

## 9. Design Decisions I Would Change at Scale

**1. Tool definitions as a static list in `tools.py`**
The `_TOOL_DEFINITIONS` list is hardcoded. At scale, tools should be registered
dynamically so new tools can be added without editing the tools file. A decorator
pattern (`@tool("search_rides", schema={...})`) would be cleaner.

**2. `compare_platforms` fans out across all adapters**
Multi-platform comparison is implemented via a dedicated `compare_platforms` tool
in `agent/tools.py`. It queries all adapters in `self.all_adapters` simultaneously,
merges results, and computes `fastest` and `cheapest` summary fields so the LLM
doesn't have to reason about them from raw data. The `select_platform` tool then
sets the active adapter before any booking action.

What would still improve at scale: the `compare_platforms` result and the subsequent
`search_rides` call can generate inconsistent ETAs because each call rolls fresh
random values. The fix is to use `compare_platforms` results directly for booking
without a redundant `search_rides` call — enforced via system prompt rule and
the `summary.fastest` / `summary.cheapest` fields in the tool response.

**3. Confirmation token is in-memory**
Already mentioned above. Redis with TTL is the right fix.

**4. No idempotency on `book_ride`**
If the network request to the platform succeeds but the response is lost, a retry
would book a second ride. Real booking systems use idempotency keys (a UUID
generated before the request, sent as a header, and the platform deduplicates on it).

**5. Mock prices don't account for traffic or time of day**
Real prices vary by traffic, time of day, local events, and weather. The mock uses
a fixed per-mile rate. This matters for the surge scenario in particular — a 2.5x
multiplier is set globally, but real surge is zone-specific and time-bounded.
