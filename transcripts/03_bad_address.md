# Transcript: 03 Bad Address
**Platform:** Uber + Lyft  |  **Started:** 2026-05-04T09:25:54.708348+00:00

## Conversation

**User:** Book me a ride from 'asdfjkl;' to 'nowhere special please'.

**Agent:** I'm unable to find the pickup location 'asdfjkl;'. Could you please provide a valid pickup address or landmark? For example, you could give me a street address, a business name, or a well-known location.

**User:** Sorry about that. How about from redwood city to oakland?

**Agent:** Great! Here are your available ride options from Redwood City to Oakland:

🚗 **UberPool** - $38–$46 · 6 min away · Seats 2
🚗 **UberX** - $55–$67 · 12 min away · Seats 4
🚗 **UberXL** - $79–$97 · 5 min away · Seats 6
🚗 **Uber Comfort** - $90–$110 · 11 min away · Seats 4
🚗 **Uber Black** - $148–$180 · 8 min away · Seats 4

Which ride type would you like to book?

> **[Confirmation Gate]** BOOKING CONFIRMATION REQUIRED
> Auto-response: `yes`

**User:** Perfect, book the cheapest option.

**Agent:** 🎉 Your UberPool is booked!

**Driver:** David M. (4.88⭐)  
**Vehicle:** Black Chevrolet Suburban  
**License Plate:** 2QWE120  
**ETA:** 6 minutes

Your driver is on the way to pick you up in Redwood City. Your ride ID is UBR-37D37A01.

## Action Log

| # | Action | Status | User Verified |
|---|--------|--------|---------------|
| 1 | `search_rides` | failed | — |
| 2 | `search_rides` | success | — |
| 3 | `request_booking_confirmation` | success | yes |
| 4 | `book_ride` | success | yes |
