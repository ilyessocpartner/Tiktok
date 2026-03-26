"""
Binance WebSocket BTC/USDT real-time price feed.
Maintains a rolling price history and exposes the latest price plus a
percentage-change helper.
"""

import asyncio
import json
import logging
from collections import deque
from datetime import datetime
from typing import Optional

import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

logger = logging.getLogger(__name__)

RECONNECT_DELAY = 5  # seconds to wait before reconnecting


class BTCFeed:
    """
    Subscribes to the Binance trade stream for BTC/USDT and maintains
    an in-memory price history.

    Usage::

        feed = BTCFeed()
        await feed.start()

        price = feed.get_price()
        change = feed.get_price_change_pct(seconds=60)

        await feed.stop()
    """

    WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@trade"

    def __init__(self) -> None:
        self._price: Optional[float] = None
        # Each entry is a (datetime, price) tuple
        self._price_history: deque[tuple[datetime, float]] = deque(maxlen=1000)
        self._running: bool = False
        self._ws_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the WebSocket listener as a background asyncio task."""
        if self._running:
            logger.warning("BTCFeed is already running")
            return
        logger.info("Starting BTCFeed WebSocket connection …")
        self._running = True
        self._ws_task = asyncio.create_task(self._connect(), name="btc_feed_ws")

    async def stop(self) -> None:
        """Stop the WebSocket listener and clean up."""
        logger.info("Stopping BTCFeed …")
        self._running = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        self._ws_task = None
        logger.info("BTCFeed stopped")

    # ------------------------------------------------------------------
    # Internal WebSocket loop
    # ------------------------------------------------------------------

    async def _connect(self) -> None:
        """
        Maintain a persistent WebSocket connection to Binance.
        Automatically reconnects after disconnection or errors.
        Stops gracefully when self._running is set to False.
        """
        while self._running:
            try:
                logger.info("Connecting to Binance WebSocket: %s", self.WS_URL)
                async with websockets.connect(
                    self.WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    logger.info("BTCFeed connected")
                    async for raw_message in ws:
                        if not self._running:
                            break
                        self._handle_message(raw_message)

            except asyncio.CancelledError:
                logger.debug("BTCFeed _connect task cancelled")
                break
            except (ConnectionClosedOK, ConnectionClosedError) as exc:
                if not self._running:
                    break
                logger.warning(
                    "BTCFeed WebSocket closed (%s). Reconnecting in %ds …",
                    exc,
                    RECONNECT_DELAY,
                )
            except Exception as exc:  # noqa: BLE001
                if not self._running:
                    break
                logger.error(
                    "BTCFeed unexpected error: %s. Reconnecting in %ds …",
                    exc,
                    RECONNECT_DELAY,
                )

            if self._running:
                await asyncio.sleep(RECONNECT_DELAY)

        logger.info("BTCFeed _connect loop exited")

    def _handle_message(self, raw: str) -> None:
        """Parse a raw trade message and update price state."""
        try:
            data = json.loads(raw)
            price_str = data.get("p")
            if price_str is None:
                return
            price = float(price_str)
            now = datetime.utcnow()
            self._price = price
            self._price_history.append((now, price))
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.debug("BTCFeed message parse error: %s | raw=%s", exc, raw[:120])

    # ------------------------------------------------------------------
    # Public data accessors
    # ------------------------------------------------------------------

    def get_price(self) -> Optional[float]:
        """Return the most recent BTC/USDT trade price, or None if unavailable."""
        return self._price

    def get_price_change_pct(self, seconds: int = 30) -> float:
        """
        Return the percentage price change over the last *seconds* seconds.

        Calculation: ``(current_price - past_price) / past_price * 100``

        Returns ``0.0`` if there is insufficient history or the current price
        is unavailable.
        """
        if self._price is None or len(self._price_history) < 2:
            return 0.0

        current_price = self._price
        now = datetime.utcnow()
        cutoff_timestamp = now.timestamp() - seconds

        # Walk history backwards to find the oldest entry within the window
        past_price: Optional[float] = None
        for ts, price in self._price_history:
            if ts.timestamp() <= cutoff_timestamp:
                past_price = price  # keep updating; we want the entry just at/before cutoff

        if past_price is None or past_price == 0.0:
            # Not enough history reaching back that far; use the oldest available entry
            oldest_ts, oldest_price = self._price_history[0]
            if oldest_price == 0.0:
                return 0.0
            return (current_price - oldest_price) / oldest_price * 100.0

        return (current_price - past_price) / past_price * 100.0

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True if the feed listener is active."""
        return self._running

    @property
    def history_len(self) -> int:
        """Number of data points currently held in the rolling history."""
        return len(self._price_history)
