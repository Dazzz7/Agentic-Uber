"""
Uber platform adapter — mock implementation.

Uses realistic NYC pricing, surge simulation, and a geocoding table of
well-known locations. Bad/nonsensical addresses trigger AddressError.

To connect to the real Uber API:
  - Replace _geocode() with calls to Uber's Places or Google Maps Geocoding API
  - Replace search_rides() with GET /v1.2/estimates/price
  - Replace book_ride() with POST /v1.2/requests
  - Replace track_ride() with GET /v1.2/requests/{request_id}
  - Replace cancel_ride() with DELETE /v1.2/requests/{request_id}
  - Add OAuth2 server-token auth header to every request
"""

import math
import random
import uuid
from typing import Dict, List, Optional

from .base import (
    AddressError,
    BookedRide,
    PlatformAdapter,
    RideOption,
    RideTrackingInfo,
)

# ---------------------------------------------------------------------------
# Static data tables
# ---------------------------------------------------------------------------

KNOWN_LOCATIONS: Dict[str, dict] = {
    "san francisco":            {"lat": 37.7749, "lng": -122.4194, "formatted": "San Francisco, CA 94103"},
    "downtown san francisco":   {"lat": 37.7749, "lng": -122.4194, "formatted": "Downtown, San Francisco, CA 94103"},
    "sfo airport":              {"lat": 37.6213, "lng": -122.3790, "formatted": "San Francisco International Airport (SFO), CA 94128"},
    "san francisco airport":    {"lat": 37.6213, "lng": -122.3790, "formatted": "San Francisco International Airport (SFO), CA 94128"},

    "san jose":                 {"lat": 37.3382, "lng": -121.8863, "formatted": "San Jose, CA 95113"},
    "downtown san jose":        {"lat": 37.3382, "lng": -121.8863, "formatted": "Downtown San Jose, CA 95113"},
    "san jose airport":         {"lat": 37.3639, "lng": -121.9289, "formatted": "Norman Y. Mineta San Jose International Airport (SJC), CA 95110"},
    "sjc":                      {"lat": 37.3639, "lng": -121.9289, "formatted": "Norman Y. Mineta San Jose International Airport (SJC), CA 95110"},

    "mountain view":            {"lat": 37.3861, "lng": -122.0839, "formatted": "Mountain View, CA 94040"},
    "googleplex":               {"lat": 37.4220, "lng": -122.0841, "formatted": "Googleplex, Mountain View, CA 94043"},

    "palo alto":                {"lat": 37.4419, "lng": -122.1430, "formatted": "Palo Alto, CA 94301"},
    "stanford":                 {"lat": 37.4275, "lng": -122.1697, "formatted": "Stanford University, Stanford, CA 94305"},

    "sunnyvale":                {"lat": 37.3688, "lng": -122.0363, "formatted": "Sunnyvale, CA 94085"},
    "santa clara":              {"lat": 37.3541, "lng": -121.9552, "formatted": "Santa Clara, CA 95050"},
    "levi's stadium":           {"lat": 37.4030, "lng": -121.9700, "formatted": "Levi's Stadium, Santa Clara, CA 95054"},

    "cupertino":                {"lat": 37.3229, "lng": -122.0322, "formatted": "Cupertino, CA 95014"},
    "apple park":               {"lat": 37.3349, "lng": -122.0090, "formatted": "Apple Park, Cupertino, CA 95014"},

    "milpitas":                 {"lat": 37.4323, "lng": -121.8996, "formatted": "Milpitas, CA 95035"},
    "fremont":                  {"lat": 37.5485, "lng": -121.9886, "formatted": "Fremont, CA 94536"},

    "redwood city":             {"lat": 37.4852, "lng": -122.2364, "formatted": "Redwood City, CA 94063"},
    "menlo park":               {"lat": 37.4529, "lng": -122.1817, "formatted": "Menlo Park, CA 94025"},
    "meta hq":                  {"lat": 37.4848, "lng": -122.1484, "formatted": "Meta Headquarters, Menlo Park, CA 94025"},

    "san mateo":                {"lat": 37.5630, "lng": -122.3255, "formatted": "San Mateo, CA 94401"},
    "foster city":              {"lat": 37.5585, "lng": -122.2711, "formatted": "Foster City, CA 94404"},

    "oakland":                  {"lat": 37.8044, "lng": -122.2712, "formatted": "Oakland, CA 94607"},
    "oakland airport":          {"lat": 37.7126, "lng": -122.2197, "formatted": "Oakland International Airport (OAK), CA 94621"},

    "berkeley":                 {"lat": 37.8715, "lng": -122.2730, "formatted": "Berkeley, CA 94704"},
    "uc berkeley":              {"lat": 37.8719, "lng": -122.2585, "formatted": "University of California, Berkeley, CA 94720"},

    "hayward":                  {"lat": 37.6688, "lng": -122.0808, "formatted": "Hayward, CA 94541"},
    "union city":               {"lat": 37.5934, "lng": -122.0438, "formatted": "Union City, CA 94587"},
}

# Ride types: base fare, per-mile rate, per-minute rate, capacity
RIDE_TYPES: Dict[str, dict] = {
    "UberPool":     {"base": 4.50,  "per_mile": 1.05, "per_min": 0.18, "capacity": 2},
    "UberX":        {"base": 8.00,  "per_mile": 1.50, "per_min": 0.25, "capacity": 4},
    "Uber Comfort": {"base": 14.00, "per_mile": 2.50, "per_min": 0.40, "capacity": 4},
    "UberXL":       {"base": 12.00, "per_mile": 2.20, "per_min": 0.35, "capacity": 6},
    "Uber Black":   {"base": 25.00, "per_mile": 4.00, "per_min": 0.65, "capacity": 4},
}

DRIVERS = [
    {"name": "Marcus T.",  "rating": 4.90, "make": "Toyota",    "model": "Camry",    "color": "Silver", "plate": "8KJR214"},
    {"name": "Sarah K.",   "rating": 4.82, "make": "Honda",     "model": "Accord",   "color": "Black",  "plate": "7ABC123"},
    {"name": "James L.",   "rating": 4.95, "make": "Tesla",     "model": "Model 3",  "color": "White",  "plate": "9XZT103"},
    {"name": "Ana R.",     "rating": 4.73, "make": "Ford",      "model": "Explorer", "color": "Blue",   "plate": "3XPL8819"},
    {"name": "David M.",   "rating": 4.88, "make": "Chevrolet", "model": "Suburban", "color": "Black",  "plate": "2QWE120"},
    {"name": "Priya S.",   "rating": 4.91, "make": "Hyundai",   "model": "Sonata",   "color": "Grey",   "plate": "7HGT492"},
]

# Bad-address signals — any address containing these strings fails geocoding
_BAD_SIGNALS = ["asdfjkl", "nowhere", "fake address", "xyz123", "invalid", "qwerty", "!!!", "???", "asdf"]


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class UberMockAdapter(PlatformAdapter):
    """
    Fully self-contained Uber mock.

    Parameters
    ----------
    surge_multiplier : float
        Set > 1.0 to simulate surge pricing across all ride types.
    seed : int | None
        Random seed for reproducible test scenarios.
    """

    def __init__(self, surge_multiplier: float = 1.0, seed: Optional[int] = None):
        self._surge = surge_multiplier
        self._booked: Dict[str, BookedRide] = {}
        self._tracking_step: Dict[str, int] = {}
        self._cached_etas: dict[str, int] = {}
        self._trip_duration: Dict[str, int] = {} 
        if seed is not None:
            random.seed(seed)

    # ── Public interface ────────────────────────────────────────────────────

    def get_platform_name(self) -> str:
        return "Uber"

    def validate_address(self, address: str) -> dict:
        try:
            geo = self._geocode(address)
            return {"valid": True, "formatted": geo["formatted"], "lat": geo["lat"], "lng": geo["lng"]}
        except AddressError as e:
            return {"valid": False, "error": str(e)}

    def search_rides(self, pickup: str, dropoff: str) -> List[RideOption]:
        p = self._geocode(pickup)
        d = self._geocode(dropoff)
        dist = self._haversine(p["lat"], p["lng"], d["lat"], d["lng"])

        options = []
        for ride_type, specs in RIDE_TYPES.items():
            lo, hi = self._price(dist, ride_type)
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

    def get_price_estimate(self, pickup: str, dropoff: str, ride_type: str) -> RideOption:
        if ride_type not in RIDE_TYPES:
            raise ValueError(f"Unknown ride type '{ride_type}'. Valid: {list(RIDE_TYPES)}")
        options = self.search_rides(pickup, dropoff)
        for opt in options:
            if opt.ride_type == ride_type:
                return opt
        raise ValueError(f"Ride type '{ride_type}' not found in search results")

    def book_ride(self, pickup: str, dropoff: str, ride_type: str) -> BookedRide:
        driver = random.choice(DRIVERS)
        ride_id = f"UBR-{uuid.uuid4().hex[:8].upper()}"
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

    def track_ride(self, ride_id: str) -> RideTrackingInfo:
        if ride_id not in self._booked:
            return RideTrackingInfo(
                ride_id=ride_id, status="not_found",
                driver_lat=None, driver_lng=None, eta_minutes=None,
                message=f"No ride found with ID {ride_id}",
            )

        ride = self._booked[ride_id]
        if ride.status == "cancelled":
            return RideTrackingInfo(
                ride_id=ride_id, status="cancelled",
                driver_lat=None, driver_lng=None, eta_minutes=None,
                message="This ride has been cancelled.",
            )

        step = self._tracking_step[ride_id]
        self._tracking_step[ride_id] = step + 1

        # Step 0: Driver is on the way
        if step == 0:
            return RideTrackingInfo(
                ride_id=ride_id, status="arriving",
                driver_lat=40.7589, driver_lng=-73.9851,
                eta_minutes=ride.eta_minutes,
                message=f"{ride.driver_name} is on the way — {ride.eta_minutes} min away.",
            )

        # Step 1: Driver is almost there
        elif step == 1:
            return RideTrackingInfo(
                ride_id=ride_id, status="arriving",
                driver_lat=40.7575, driver_lng=-73.9843,
                eta_minutes=2,
                message=(
                    f"{ride.driver_name} is 2 minutes away. "
                    f"Head outside — look for a {ride.vehicle_color} "
                    f"{ride.vehicle_make} {ride.vehicle_model} ({ride.license_plate})."
                ),
            )

        # Step 2: Driver has arrived
        elif step == 2:
            ride.status = "driver_arrived"             # update ride status
            return RideTrackingInfo(
                ride_id=ride_id, status="driver_arrived",
                driver_lat=40.7580, driver_lng=-73.9855,
                eta_minutes=0,
                message=(
                    f"{ride.driver_name} has arrived! "
                    f"Your {ride.vehicle_color} {ride.vehicle_make} {ride.vehicle_model} "
                    f"({ride.license_plate}) is waiting at the pickup point."
                ),
            )

        # Step 3: Ride has started
        elif step == 3:
            ride.status = "in_progress"                # update ride status
            # Compute realistic trip duration from haversine distance
            try:
                p = self._geocode(ride.pickup)
                d = self._geocode(ride.dropoff)
                dist = self._haversine(p["lat"], p["lng"], d["lat"], d["lng"])
                trip_minutes = round(dist * 3.5)       # same NYC traffic formula
            except Exception:
                trip_minutes = 20                      # fallback if geocode fails

            self._trip_duration[ride_id] = trip_minutes   # store for step 4

            return RideTrackingInfo(
                ride_id=ride_id, status="in_progress",
                driver_lat=40.7200, driver_lng=-73.9500,
                eta_minutes=trip_minutes,
                message=(
                    f"Your ride has started! "
                    f"Heading to {ride.dropoff}. "
                    f"Estimated arrival in {trip_minutes} minutes."
                ),
                distance_remaining_miles=round(dist, 1) if 'dist' in dir() else None,
            )

        # Step 4: Ride completed
        else:
            ride.status = "completed"                  # update ride status
            trip_minutes = self._trip_duration.get(ride_id, 20)
            return RideTrackingInfo(
                ride_id=ride_id, status="completed",
                driver_lat=None, driver_lng=None,
                eta_minutes=0,
                message=(
                    f"You have arrived at {ride.dropoff}! "
                    f"Ride completed. Total trip time: ~{trip_minutes} minutes. "
                    f"Thank you for riding with {self.adapter_name if hasattr(self, 'adapter_name') else 'Uber'}!"
                ),
            )

    def cancel_ride(self, ride_id: str) -> dict:
        if ride_id not in self._booked:
            return {"success": False, "message": f"Ride {ride_id} not found"}

        ride = self._booked[ride_id]
        if ride.status == "in_progress":
            return {"success": False, "message": "Cannot cancel a ride that is already in progress"}

        # Fee applies if driver has been dispatched for a while
        fee = 5.00 if self._tracking_step.get(ride_id, 0) >= 1 else 0.00
        ride.status = "cancelled"

        msg = "Ride cancelled successfully."
        if fee:
            msg += f" A ${fee:.2f} cancellation fee has been charged."

        return {"success": True, "ride_id": ride_id, "cancellation_fee": fee, "message": msg}

    # ── Private helpers ─────────────────────────────────────────────────────

    def _geocode(self, address: str) -> dict:
        norm = address.lower().strip()

        # Exact and substring match against known locations
        for key, data in KNOWN_LOCATIONS.items():
            if key in norm or norm in key:
                return data

        # Reject obviously bad input
        if any(bad in norm for bad in _BAD_SIGNALS) or len(norm) < 4:
            raise AddressError(
                f"Could not geocode '{address}'. "
                "No matching location found — please provide a specific address or landmark."
            )

        # Accept plausible-looking addresses (has digits, long enough)
        if len(address) >= 8 and any(c.isdigit() for c in address):
            return {
                "lat": 40.7128 + random.uniform(-0.15, 0.15),
                "lng": -74.0060 + random.uniform(-0.15, 0.15),
                "formatted": address.strip(),
            }

        raise AddressError(
            f"Could not resolve '{address}'. "
            "Try a full street address, neighborhood, or landmark name."
        )

    @staticmethod
    def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        """Distance in miles between two lat/lng points."""
        R = 3959.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lng2 - lng1)
        a = math.sin(dp / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _price(self, distance_miles: float, ride_type: str) -> tuple[float, float]:
        specs = RIDE_TYPES[ride_type]
        duration = distance_miles * 3.5  # minutes, NYC traffic estimate
        raw = specs["base"] + distance_miles * specs["per_mile"] + duration * specs["per_min"]
        surged = raw * self._surge
        return round(surged * 0.90, 2), round(surged * 1.10, 2)
