"""
Platform adapter interface.

Every ride-sharing platform must implement PlatformAdapter.
The agent tools layer above NEVER touches platform-specific code —
it only calls these methods. Swapping platforms = swapping the adapter.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List


# ── Domain objects ──────────────────────────────────────────────────────────

@dataclass
class RideOption:
    option_id: str
    ride_type: str          # "UberX", "Lyft", "Black", etc.
    estimated_price_min: float
    estimated_price_max: float
    surge_multiplier: float  # 1.0 = no surge
    eta_pickup_minutes: int
    capacity: int
    currency: str = "USD"

    @property
    def price_display(self) -> str:
        s = f"${self.estimated_price_min:.0f}–${self.estimated_price_max:.0f}"
        if self.surge_multiplier > 1.0:
            s += f" ({self.surge_multiplier}x surge)"
        return s


@dataclass
class BookedRide:
    ride_id: str
    driver_name: str
    driver_rating: float
    vehicle_make: str
    vehicle_model: str
    vehicle_color: str
    license_plate: str
    eta_minutes: int
    ride_type: str
    status: str = "arriving"
    pickup: str = ""
    dropoff: str = ""


@dataclass
class RideTrackingInfo:
    ride_id: str
    status: str             # arriving | in_progress | completed | cancelled | not_found
    driver_lat: Optional[float]
    driver_lng: Optional[float]
    eta_minutes: Optional[int]
    message: str
    distance_remaining_miles: Optional[float] = None


# ── Exceptions ──────────────────────────────────────────────────────────────

class AddressError(Exception):
    """Raised when an address cannot be geocoded or resolved."""


class PlatformError(Exception):
    """Raised for platform-side failures (API down, auth, rate limit)."""


# ── Abstract adapter ────────────────────────────────────────────────────────

class PlatformAdapter(ABC):
    """
    Contract every ride-sharing platform adapter must satisfy.

    To add a new platform (Lyft, Via, Bolt, Zocdoc for medical transport…):
      1. Subclass PlatformAdapter
      2. Implement every abstract method
      3. Pass the new adapter to RideAgent — nothing else changes

    The methods map 1-to-1 to agent tools, which is intentional:
    adding a platform capability means adding one method here and one
    tool definition in agent/tools.py.
    """

    @abstractmethod
    def get_platform_name(self) -> str:
        """Return display name, e.g. 'Uber' or 'Lyft'."""

    @abstractmethod
    def validate_address(self, address: str) -> dict:
        """
        Geocode / validate an address.

        Returns:
            {"valid": True,  "formatted": str, "lat": float, "lng": float}
            {"valid": False, "error": str}

        Raises AddressError for clearly invalid input so callers can
        catch it uniformly.
        """

    @abstractmethod
    def search_rides(self, pickup: str, dropoff: str) -> List[RideOption]:
        """
        Return all available ride options, sorted cheapest-first.

        Raises AddressError if either address cannot be resolved.
        """

    @abstractmethod
    def get_price_estimate(self, pickup: str, dropoff: str, ride_type: str) -> RideOption:
        """Return a price estimate for a specific ride type."""

    @abstractmethod
    def book_ride(self, pickup: str, dropoff: str, ride_type: str) -> BookedRide:
        """
        Book the ride. Called ONLY after the user has confirmed.
        Returns driver details and the canonical ride_id.
        """

    @abstractmethod
    def track_ride(self, ride_id: str) -> RideTrackingInfo:
        """Return current driver position, status, and ETA."""

    @abstractmethod
    def cancel_ride(self, ride_id: str) -> dict:
        """
        Cancel a booked ride.

        Returns:
            {"success": True,  "cancellation_fee": float, "message": str}
            {"success": False, "message": str}
        """
