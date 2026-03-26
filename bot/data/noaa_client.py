"""
NOAA National Weather Service API client.
Provides async methods to fetch daily high temperatures and hourly forecasts.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# Required by NOAA API
USER_AGENT = "PolymarketBot/1.0 contact@example.com"


class NOAAClient:
    """Async client for the NOAA National Weather Service API."""

    BASE_URL = "https://api.weather.gov"

    CITY_COORDS: dict[str, tuple[float, float]] = {
        "NYC": (40.7128, -74.0060),
        "New York": (40.7128, -74.0060),
        "Chicago": (41.8781, -87.6298),
        "Seattle": (47.6062, -122.3321),
        "Atlanta": (33.7490, -84.3880),
        "Dallas": (32.7767, -96.7970),
        "Miami": (25.7617, -80.1918),
        "Los Angeles": (34.0522, -118.2437),
        "Boston": (42.3601, -71.0589),
        "Denver": (39.7392, -104.9903),
    }

    MAX_RETRIES = 3
    BACKOFF_BASE = 2.0  # seconds

    def __init__(self) -> None:
        # Cache: (lat, lon) -> hourly forecast URL string
        self._grid_cache: dict[tuple[float, float], str] = {}
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_with_retry(self, url: str) -> Optional[dict]:
        """
        Perform a GET request with exponential backoff on failures.
        Returns the parsed JSON dict, or None on persistent failure.
        """
        session = await self._get_session()
        for attempt in range(self.MAX_RETRIES):
            try:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.json()
                    if response.status == 429:
                        # Rate-limited
                        wait = self.BACKOFF_BASE ** (attempt + 1)
                        logger.warning(
                            "NOAA rate limit hit (attempt %d/%d). Waiting %.1fs",
                            attempt + 1,
                            self.MAX_RETRIES,
                            wait,
                        )
                        await asyncio.sleep(wait)
                        continue
                    logger.error(
                        "NOAA API returned HTTP %d for %s", response.status, url
                    )
                    return None
            except aiohttp.ClientError as exc:
                wait = self.BACKOFF_BASE ** (attempt + 1)
                logger.warning(
                    "NOAA request error on attempt %d/%d: %s. Retrying in %.1fs",
                    attempt + 1,
                    self.MAX_RETRIES,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)

        logger.error("All %d NOAA retries exhausted for %s", self.MAX_RETRIES, url)
        return None

    async def _get_hourly_forecast_url(self, lat: float, lon: float) -> Optional[str]:
        """
        Look up the NWS grid point for (lat, lon) and return its hourly
        forecast URL.  Results are cached to avoid redundant API calls.
        """
        cache_key = (round(lat, 4), round(lon, 4))
        if cache_key in self._grid_cache:
            return self._grid_cache[cache_key]

        url = f"{self.BASE_URL}/points/{lat:.4f},{lon:.4f}"
        data = await self._get_with_retry(url)
        if data is None:
            return None

        try:
            forecast_hourly_url: str = data["properties"]["forecastHourly"]
        except (KeyError, TypeError) as exc:
            logger.error("Unexpected /points response structure: %s", exc)
            return None

        self._grid_cache[cache_key] = forecast_hourly_url
        logger.debug("Cached grid forecast URL for (%s, %s)", lat, lon)
        return forecast_hourly_url

    @staticmethod
    def _celsius_to_fahrenheit(celsius: float) -> float:
        return celsius * 9.0 / 5.0 + 32.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_hourly_forecast(self, lat: float, lon: float) -> list[dict]:
        """
        Return a list of hourly forecast periods for the given coordinates.
        Each element is a dict with at least:
          - startTime (ISO-8601 string)
          - temperature (numeric)
          - temperatureUnit ("F" or "C")
        Returns an empty list on failure.
        """
        forecast_url = await self._get_hourly_forecast_url(lat, lon)
        if forecast_url is None:
            return []

        data = await self._get_with_retry(forecast_url)
        if data is None:
            return []

        try:
            periods: list[dict] = data["properties"]["periods"]
        except (KeyError, TypeError) as exc:
            logger.error("Unexpected forecast response structure: %s", exc)
            return []

        # Normalize: always return temperature in Fahrenheit
        normalized: list[dict] = []
        for period in periods:
            temp = period.get("temperature")
            unit = period.get("temperatureUnit", "F")
            if temp is not None and unit == "C":
                temp = self._celsius_to_fahrenheit(float(temp))
                unit = "F"
            normalized.append(
                {
                    "startTime": period.get("startTime", ""),
                    "temperature": temp,
                    "temperatureUnit": unit,
                }
            )

        return normalized

    async def get_daily_high(self, city: str, target_date: str) -> Optional[float]:
        """
        Return the daily high temperature in Fahrenheit for *city* on
        *target_date* (format ``"YYYY-MM-DD"``).

        Process:
        1. Resolve city name to (lat, lon) from CITY_COORDS.
        2. Fetch the grid point info to obtain the hourly forecast URL.
        3. Fetch hourly forecast periods.
        4. Filter periods whose startTime begins with target_date.
        5. Return the maximum temperature among those periods.

        Returns ``None`` if the city is unknown, the API is unreachable, or
        no periods are found for the requested date.
        """
        coords = self.CITY_COORDS.get(city)
        if coords is None:
            logger.warning("City '%s' not found in CITY_COORDS", city)
            return None

        lat, lon = coords

        # Validate date format early so we don't waste an API call
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
        except ValueError:
            logger.error(
                "Invalid target_date format '%s'. Expected YYYY-MM-DD", target_date
            )
            return None

        periods = await self.get_hourly_forecast(lat, lon)
        if not periods:
            return None

        # Filter periods that belong to target_date
        day_temps: list[float] = []
        for period in periods:
            start_time: str = period.get("startTime", "")
            if start_time.startswith(target_date):
                temp = period.get("temperature")
                if temp is not None:
                    day_temps.append(float(temp))

        if not day_temps:
            logger.warning(
                "No hourly periods found for city='%s' on date='%s'",
                city,
                target_date,
            )
            return None

        daily_high = max(day_temps)
        logger.info(
            "Daily high for %s on %s: %.1f°F (from %d periods)",
            city,
            target_date,
            daily_high,
            len(day_temps),
        )
        return daily_high
