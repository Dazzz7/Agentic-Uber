"""
Lyft platform adapter — demonstrates how the adapter is swapped.

To use Lyft instead of Uber:
    from adapters.lyft_mock import LyftMockAdapter
    adapter = LyftMockAdapter()
    agent = RideAgent(adapter=adapter, ...)

Nothing in agent/, scenarios/, or main.py changes.

--------------------------------------------------------------------
WHAT ACTUALLY DIFFERS between Uber and Lyft (real API):

  Auth:         Lyft uses OAuth2 client_credentials
                Uber uses server-token in Authorization header

  Base URL:     https://api.lyft.com/v1/          (Lyft)
                https://api.uber.com/v1.2/         (Uber)

  Ride types:   "lyft", "lyft_xl", "lyft_lux", "lyft_shared"
                "uberx", "uberxl", "uberblack", "pool"

  Price est.:   GET /cost?start_lat=...&ride_type=lyft
                GET /estimates/price?start_latitude=...

  Booking:      POST /rides   {ride_type, origin, destination}
                POST /requests {product_id, start_lat, ...}

  Tracking:     GET /rides/{id}  → status + driver embedded in same object
                GET /requests/{id} + GET /requests/{id}/map (separate)

  Cancellation: POST /rides/{id}/cancel  (no fee field, separate endpoint)
                DELETE /requests/{id}    (fee in surge_confirmation_id flow)

  Surge:        Lyft returns "cost_token" that must be echoed back
                Uber uses surge_confirmation_id

What stays the same: the PlatformAdapter interface, all agent tools,
confirmation gates, action logging, and the agent loop.
--------------------------------------------------------------------
"""

import random
import uuid
from .base import BookedRide
from typing import Optional

from .uber_mock import UberMockAdapter  # reuse mock internals for the demo

# Lyft-specific ride type table (different names, slightly different pricing)
LYFT_RIDE_TYPES = {
    "Lyft Shared": {"base": 4.00,  "per_mile": 1.00, "per_min": 0.17, "capacity": 2},
    "Lyft Standard":        {"base": 7.50,  "per_mile": 1.45, "per_min": 0.23, "capacity": 4},
    "Lyft Comfort":{"base": 13.00, "per_mile": 2.40, "per_min": 0.38, "capacity": 4},
    "Lyft XL":     {"base": 11.00, "per_mile": 2.10, "per_min": 0.33, "capacity": 6},
    "Lyft Lux":    {"base": 22.00, "per_mile": 3.80, "per_min": 0.60, "capacity": 4},
}

LYFT_DRIVERS = [
    {"name": "Carlos M.",  "rating": 4.88, "make": "Toyota",   "model": "Prius",   "color": "Silver", "plate": "7AAA123"},
    {"name": "Emma W.",    "rating": 4.92, "make": "Honda",    "model": "Civic",   "color": "White",  "plate": "5KJP382"},
    {"name": "Kevin J.",   "rating": 4.78, "make": "Kia",      "model": "Optima",  "color": "Red",    "plate": "4KAW366"},
    {"name": "Fatima O.",  "rating": 4.95, "make": "Tesla",    "model": "Model Y", "color": "Blue",   "plate": "7LYF492"},
]


class LyftMockAdapter(UberMockAdapter):
    """
    Lyft mock adapter.

    Inherits all mock geocoding + pricing math from UberMockAdapter
    but overrides identity, ride types, and driver roster — mirroring
    what would change in a real API integration.
    """

    def __init__(self, surge_multiplier: float = 1.0, seed: Optional[int] = None):
        super().__init__(surge_multiplier=surge_multiplier, seed=seed)
        # Override ride types and drivers
        self._ride_types = LYFT_RIDE_TYPES
        self._drivers = LYFT_DRIVERS

    def get_platform_name(self) -> str:
        return "Lyft"

    # Override book_ride to use Lyft driver pool and ride_types
    def book_ride(self, pickup: str, dropoff: str, ride_type: str) -> object:
        import uuid
        from .base import BookedRide

        if ride_type not in LYFT_RIDE_TYPES:
            raise ValueError(f"Unknown Lyft ride type '{ride_type}'. Valid: {list(LYFT_RIDE_TYPES)}")

        driver = random.choice(LYFT_DRIVERS)
        ride_id = f"LYF-{uuid.uuid4().hex[:8].upper()}"

        #from .base import BookedRide
        booked = BookedRide(
            ride_id=ride_id,
            driver_name=driver["name"],
            driver_rating=driver["rating"],
            vehicle_make=driver["make"],
            vehicle_model=driver["model"],
            vehicle_color=driver["color"],
            license_plate=driver["plate"],
            eta_minutes=self._cached_etas.get(ride_type, random.randint(3, 8)),
            ride_type=ride_type,
            status="arriving",
            pickup=pickup,
            dropoff=dropoff,
        )
        self._booked[ride_id] = booked
        self._tracking_step[ride_id] = 0
        return booked

    def search_rides(self, pickup: str, dropoff: str):
        """Override to use Lyft ride types."""
        import uuid
        from .base import RideOption

        p = self._geocode(pickup)
        d = self._geocode(dropoff)
        dist = self._haversine(p["lat"], p["lng"], d["lat"], d["lng"])

        options = []
        for ride_type, specs in LYFT_RIDE_TYPES.items():
            duration = dist * 3.5
            raw = specs["base"] + dist * specs["per_mile"] + duration * specs["per_min"]
            surged = raw * self._surge
            lo, hi = round(surged * 0.90, 2), round(surged * 1.10, 2)
            eta = random.randint(3, 12)
            self._cached_etas[ride_type] = eta
            options.append(RideOption(
                option_id=f"opt_{uuid.uuid4().hex[:6]}",
                ride_type=ride_type,
                estimated_price_min=lo,
                estimated_price_max=hi,
                surge_multiplier=self._surge,
                eta_pickup_minutes=eta,
                capacity=specs["capacity"],
            ))

        return sorted(options, key=lambda o: o.estimated_price_min)
