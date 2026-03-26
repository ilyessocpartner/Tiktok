"""
Abstract base class for all Polymarket trading strategies.

All concrete strategies must inherit from BaseStrategy and implement
the `run` coroutine and `name` property.
"""

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from bot.utils.logger import setup_logger

logger = setup_logger(__name__)


class BaseStrategy(ABC):
    """
    Base class for Polymarket trading strategies.

    Subclasses must implement:
        - run(): the main async loop
        - name (property): a human-readable strategy identifier

    Attributes:
        _poly_client: PolymarketClient instance for market data and order placement.
        _risk_manager: RiskManager instance for trade gating and P&L tracking.
        _running: Whether the strategy's run loop is active.
        open_positions: Active positions keyed by token_id.
            Each value is a dict: {price, size, entry_time, market_id}.
    """

    def __init__(self, poly_client: Any, risk_manager: Any) -> None:
        self._poly_client = poly_client
        self._risk_manager = risk_manager
        self._running: bool = False
        # token_id -> {price: float, size: float, entry_time: datetime, market_id: str}
        self.open_positions: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def run(self) -> None:
        """
        Main strategy loop.  Implementations should:
        - Set self._running = True at the start.
        - Loop while self._running is True.
        - Call asyncio.sleep(scan_frequency) between scans.
        - Catch and log exceptions without crashing the loop.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name, e.g. 'WeatherTrader'."""
        ...

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """
        Signal the run loop to stop after the current iteration.

        The loop will exit on the next iteration check; it will NOT
        forcibly cancel any in-flight coroutine.
        """
        logger.info("[%s] Stop requested.", self.name)
        self._running = False

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """
        Return a snapshot of the strategy's current state.

        Returns:
            dict with keys:
                name (str): strategy name
                running (bool): whether the run loop is active
                open_positions_count (int): number of open positions
                open_positions (dict): copy of open_positions
                timestamp (str): ISO-8601 UTC timestamp of the snapshot
        """
        return {
            "name": self.name,
            "running": self._running,
            "open_positions_count": len(self.open_positions),
            "open_positions": dict(self.open_positions),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
