"""
WeatherTraderStrategy — exploits mispricings in Polymarket temperature markets.

For each active "temperature" market the strategy:
1. Parses the question to extract city, temperature bound(s), and target date.
2. Fetches the NOAA daily-high forecast for that city and date.
3. Computes a "true" probability with a Gaussian model (σ = 3.5 °F).
4. Enters a BUY YES position when the market under-prices the true probability
   by at least entry_threshold.
5. Exits positions when a profit target or stop-loss is hit.
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from scipy.stats import norm

from bot.data.noaa_client import NOAAClient
from bot.strategies.base_strategy import BaseStrategy
from bot.utils.logger import setup_logger

logger = setup_logger(__name__)

# Standard deviation (°F) used for all Gaussian probability calculations.
_TEMP_SIGMA: float = 3.5

# Regex patterns used to parse temperature conditions from market questions.
_RE_RANGE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*°?\s*[Ff]", re.IGNORECASE
)
_RE_BETWEEN = re.compile(
    r"between\s+(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)", re.IGNORECASE
)
_RE_ABOVE = re.compile(r"above\s+(\d+(?:\.\d+)?)\s*°?\s*[Ff]", re.IGNORECASE)
_RE_BELOW = re.compile(r"below\s+(\d+(?:\.\d+)?)\s*°?\s*[Ff]", re.IGNORECASE)

# Regex to find a date in various formats inside a question string.
_RE_DATE_MDY = re.compile(
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2}(?:,\s*\d{4})?",
    re.IGNORECASE,
)
_RE_DATE_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# Month abbreviation → number mapping used by _parse_date_from_text.
_MONTH_MAP: Dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_date_from_text(text: str, fallback_iso: Optional[str] = None) -> Optional[str]:
    """
    Attempt to extract a YYYY-MM-DD date from *text*.

    Tries ISO format first, then common English month-name formats.
    Falls back to *fallback_iso* (already in YYYY-MM-DD) when nothing matches.
    """
    # 1. ISO format: 2025-06-15
    m = _RE_DATE_ISO.search(text)
    if m:
        return m.group(1)

    # 2. English month name: "June 15" or "June 15, 2025"
    m = _RE_DATE_MDY.search(text)
    if m:
        raw = m.group(0)
        parts = raw.replace(",", "").split()
        month_str = parts[0][:3].lower()
        month = _MONTH_MAP.get(month_str)
        if month is None:
            return fallback_iso
        day = int(parts[1])
        year = int(parts[2]) if len(parts) >= 3 else datetime.now(timezone.utc).year
        return f"{year:04d}-{month:02d}-{day:02d}"

    return fallback_iso


def _parse_temp_condition(
    question: str,
) -> Optional[Tuple[str, Optional[float], Optional[float]]]:
    """
    Parse a temperature condition from a market question string.

    Returns a tuple (condition_type, low, high) where condition_type is one of:
        "range"  → both low and high are set
        "above"  → only low is set (high is None)
        "below"  → only high is set (low is None)

    Returns None when no recognisable pattern is found.
    """
    # Range: "58-59°F" or "between 58 and 59"
    m = _RE_RANGE.search(question)
    if m:
        return "range", float(m.group(1)), float(m.group(2))

    m = _RE_BETWEEN.search(question)
    if m:
        return "range", float(m.group(1)), float(m.group(2))

    # Above threshold
    m = _RE_ABOVE.search(question)
    if m:
        return "above", float(m.group(1)), None

    # Below threshold
    m = _RE_BELOW.search(question)
    if m:
        return "below", None, float(m.group(1))

    return None


def _calc_true_prob(
    condition: Tuple[str, Optional[float], Optional[float]],
    noaa_temp: float,
) -> float:
    """
    Compute P(condition is TRUE) given a Gaussian forecast centred on noaa_temp
    with standard deviation _TEMP_SIGMA.
    """
    ctype, low, high = condition
    if ctype == "range" and low is not None and high is not None:
        return float(norm.cdf(high, loc=noaa_temp, scale=_TEMP_SIGMA) -
                     norm.cdf(low, loc=noaa_temp, scale=_TEMP_SIGMA))
    if ctype == "above" and low is not None:
        return float(1.0 - norm.cdf(low, loc=noaa_temp, scale=_TEMP_SIGMA))
    if ctype == "below" and high is not None:
        return float(norm.cdf(high, loc=noaa_temp, scale=_TEMP_SIGMA))
    return 0.0


def _find_city(question: str, locations: List[str]) -> Optional[str]:
    """Return the first location name found inside question (case-insensitive)."""
    q_lower = question.lower()
    for city in locations:
        if city.lower() in q_lower:
            return city
    # Also try common aliases / alternate spellings
    aliases = {
        "new york": "NYC",
        "nyc": "NYC",
    }
    for alias, canonical in aliases.items():
        if alias in q_lower and canonical in locations:
            return canonical
    return None


def _find_yes_token(market: dict) -> Optional[dict]:
    """Return the YES token entry from market['tokens'], or None."""
    for token in market.get("tokens", []):
        if str(token.get("outcome", "")).lower() == "yes":
            return token
    return None


class WeatherTraderStrategy(BaseStrategy):
    """
    Identifies and trades mispricings in Polymarket temperature markets.

    For every active market matching keyword "temperature" the strategy
    computes a Gaussian true-probability using NOAA forecast data and
    enters a position when the market price offers sufficient edge.
    """

    def __init__(
        self,
        poly_client: Any,
        risk_manager: Any,
        noaa_client: Optional[Any] = None,
        entry_threshold: float = 0.08,
        exit_threshold: float = 0.08,
        max_position_size: float = 2.0,
        locations: Optional[List[str]] = None,
        scan_frequency: int = 120,
        notify_callback: Optional[Callable] = None,
    ) -> None:
        """
        Args:
            poly_client:       PolymarketClient instance.
            risk_manager:      RiskManager instance.
            noaa_client:       Optional NOAAClient instance. A new one is created if None.
            entry_threshold:   Minimum edge (true_prob - market_price) required to enter.
            exit_threshold:    Profit target: exit when price rises by this much above entry.
            max_position_size: Maximum USDC size per trade.
            locations:         City names to scan. Defaults to six major US cities.
            scan_frequency:    Seconds between full market scans.
            notify_callback:   Optional async/sync callable(event_dict) for alerts.
        """
        super().__init__(poly_client, risk_manager)
        self._noaa = noaa_client if noaa_client is not None else NOAAClient()
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.max_position_size = max_position_size
        self.locations: List[str] = locations if locations is not None else [
            "NYC", "Chicago", "Seattle", "Atlanta", "Dallas", "Miami"
        ]
        self.scan_frequency = scan_frequency
        self._notify_callback = notify_callback

    # ------------------------------------------------------------------
    # BaseStrategy interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "WeatherTrader"

    async def run(self) -> None:
        """
        Main loop: scan temperature markets every scan_frequency seconds,
        look for entry opportunities, and manage existing positions.
        """
        self._running = True
        logger.info("[%s] Strategy started. Scan frequency: %ds.", self.name, self.scan_frequency)

        while self._running:
            try:
                await self._scan_markets()
                await self._manage_positions()
            except asyncio.CancelledError:
                logger.info("[%s] Run loop cancelled.", self.name)
                break
            except Exception as exc:  # noqa: BLE001
                logger.error("[%s] Unexpected error in run loop: %s", self.name, exc, exc_info=True)

            if self._running:
                await asyncio.sleep(self.scan_frequency)

        logger.info("[%s] Strategy stopped.", self.name)

    # ------------------------------------------------------------------
    # Market scanning
    # ------------------------------------------------------------------

    async def _scan_markets(self) -> None:
        """Fetch active temperature markets and evaluate entry opportunities."""
        try:
            markets: List[dict] = await self._poly_client.get_markets(
                keyword="temperature", active=True, limit=100
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] Failed to fetch markets: %s", self.name, exc)
            return

        logger.debug("[%s] Fetched %d temperature markets.", self.name, len(markets))

        for market in markets:
            try:
                await self._evaluate_market(market)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[%s] Error evaluating market %s: %s",
                    self.name, market.get("id", "?"), exc,
                    exc_info=True,
                )

    async def _evaluate_market(self, market: dict) -> None:
        """
        For a single market: parse question, fetch NOAA data, compute edge,
        and enter a position if conditions are met.
        """
        question: str = market.get("question", "")
        market_id: str = market.get("id", "")

        # --- parse city ---
        city = _find_city(question, self.locations)
        if city is None:
            logger.debug("[%s] No monitored city found in question: '%s'", self.name, question)
            return

        # --- parse temperature condition ---
        condition = _parse_temp_condition(question)
        if condition is None:
            logger.debug("[%s] No temp condition parsed from: '%s'", self.name, question)
            return

        # --- parse target date ---
        end_date_raw: str = market.get("end_date", market.get("endDate", ""))
        # Normalise ISO datetime → date-only string
        fallback_date: Optional[str] = None
        if end_date_raw:
            fallback_date = end_date_raw[:10]  # "YYYY-MM-DD"

        target_date = _parse_date_from_text(question, fallback_iso=fallback_date)
        if target_date is None:
            logger.debug(
                "[%s] Could not determine target date for market %s.", self.name, market_id
            )
            return

        # --- NOAA forecast ---
        noaa_temp = await self._noaa.get_daily_high(city, target_date)
        if noaa_temp is None:
            logger.warning(
                "[%s] No NOAA data for city=%s date=%s (market %s).",
                self.name, city, target_date, market_id,
            )
            return

        # --- true probability ---
        true_prob = _calc_true_prob(condition, noaa_temp)

        # --- locate YES token ---
        yes_token = _find_yes_token(market)
        if yes_token is None:
            logger.debug("[%s] No YES token in market %s.", self.name, market_id)
            return

        token_id: str = yes_token.get("token_id", "")
        market_price: float = float(yes_token.get("price", 0.0))

        # Skip if we already hold a position in this token
        if token_id in self.open_positions:
            return

        edge = true_prob - market_price
        logger.info(
            "[%s] Market %s | city=%s | date=%s | NOAA=%.1f°F | "
            "true_prob=%.4f | market_price=%.4f | edge=%.4f",
            self.name, market_id, city, target_date, noaa_temp,
            true_prob, market_price, edge,
        )

        # --- entry check ---
        if edge >= self.entry_threshold and self._risk_manager.can_trade():
            await self._enter_position(market, yes_token, market_price, true_prob, edge, city)

    async def _enter_position(
        self,
        market: dict,
        yes_token: dict,
        market_price: float,
        true_prob: float,
        edge: float,
        city: str,
    ) -> None:
        """Place a BUY order and record the open position."""
        token_id: str = yes_token.get("token_id", "")
        market_id: str = market.get("id", "")

        # Size calculation
        position_size_usdc = min(
            self.max_position_size,
            self._risk_manager.stop_loss_per_trade * 0.5,
        )
        if market_price <= 0:
            logger.warning("[%s] Invalid market price %.4f for token %s.", self.name, market_price, token_id)
            return

        shares = position_size_usdc / market_price
        limit_price = round(market_price + 0.01, 4)

        logger.info(
            "[%s] ENTERING position | market=%s | city=%s | "
            "true_prob=%.4f | market_price=%.4f | edge=%.4f | "
            "size_usdc=%.2f | shares=%.4f | limit_price=%.4f",
            self.name, market_id, city, true_prob, market_price, edge,
            position_size_usdc, shares, limit_price,
        )

        if self._poly_client.dry_run:
            logger.info("[%s] DRY RUN — order not submitted.", self.name)
        else:
            order_result = await self._poly_client.place_order(
                token_id=token_id,
                price=limit_price,
                size=shares,
                side="BUY",
            )
            if order_result is None:
                logger.warning("[%s] Order placement failed for token %s.", self.name, token_id)
                return

        # Record open position
        self.open_positions[token_id] = {
            "price": market_price,
            "size": shares,
            "size_usdc": position_size_usdc,
            "entry_time": datetime.now(timezone.utc),
            "market_id": market_id,
            "true_prob": true_prob,
            "edge": edge,
            "city": city,
        }

        event = {
            "event": "entry",
            "strategy": self.name,
            "market_id": market_id,
            "token_id": token_id,
            "city": city,
            "market_price": market_price,
            "true_prob": true_prob,
            "edge": edge,
            "position_size_usdc": position_size_usdc,
        }
        await self._notify(event)

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    async def _manage_positions(self) -> None:
        """Check all open positions for profit-take or stop-loss triggers."""
        if not self.open_positions:
            return

        # Snapshot keys so we can mutate the dict during iteration
        for token_id in list(self.open_positions.keys()):
            pos = self.open_positions.get(token_id)
            if pos is None:
                continue
            try:
                await self._check_exit(token_id, pos)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[%s] Error managing position for token %s: %s",
                    self.name, token_id, exc, exc_info=True,
                )

    async def _check_exit(self, token_id: str, pos: dict) -> None:
        """Evaluate exit conditions for a single position."""
        # Refresh current price via order book
        try:
            order_book = await self._poly_client.get_order_book(token_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Could not fetch order book for %s: %s", self.name, token_id, exc)
            return

        bids: List[dict] = order_book.get("bids", [])
        if not bids:
            logger.debug("[%s] Empty order book for token %s.", self.name, token_id)
            return

        # Best bid = current exit price
        current_price = float(bids[0].get("price", 0.0))
        entry_price: float = pos["price"]
        position_size_usdc: float = pos.get("size_usdc", entry_price * pos["size"])

        # Stop-loss threshold in price terms
        stop_loss_price_delta = (
            self._risk_manager.stop_loss_per_trade / pos["size"]
            if pos["size"] > 0 else 0.0
        )
        stop_loss_price = entry_price - stop_loss_price_delta

        should_exit = False
        exit_reason = ""

        if current_price >= (entry_price + self.exit_threshold):
            should_exit = True
            exit_reason = f"profit_target (price={current_price:.4f} >= {entry_price + self.exit_threshold:.4f})"
        elif current_price <= stop_loss_price:
            should_exit = True
            exit_reason = f"stop_loss (price={current_price:.4f} <= {stop_loss_price:.4f})"

        if not should_exit:
            logger.debug(
                "[%s] Position %s: entry=%.4f current=%.4f — holding.",
                self.name, token_id, entry_price, current_price,
            )
            return

        logger.info(
            "[%s] EXITING position | token=%s | market=%s | reason=%s | "
            "entry=%.4f | current=%.4f",
            self.name, token_id, pos.get("market_id", "?"),
            exit_reason, entry_price, current_price,
        )

        pnl = (current_price - entry_price) * pos["size"]

        if not self._poly_client.dry_run:
            sell_result = await self._poly_client.place_order(
                token_id=token_id,
                price=current_price - 0.01,
                size=pos["size"],
                side="SELL",
            )
            if sell_result is None:
                logger.warning("[%s] Sell order failed for token %s.", self.name, token_id)
                return
        else:
            logger.info("[%s] DRY RUN — sell not submitted for token %s.", self.name, token_id)

        # Record P&L and remove position
        self._risk_manager.record_trade(
            pnl=pnl,
            trade_info={
                "strategy": self.name,
                "token_id": token_id,
                "market_id": pos.get("market_id"),
                "entry_price": entry_price,
                "exit_price": current_price,
                "size": pos["size"],
                "exit_reason": exit_reason,
            },
        )
        del self.open_positions[token_id]

        event = {
            "event": "exit",
            "strategy": self.name,
            "token_id": token_id,
            "market_id": pos.get("market_id"),
            "entry_price": entry_price,
            "exit_price": current_price,
            "pnl": pnl,
            "exit_reason": exit_reason,
        }
        await self._notify(event)

    # ------------------------------------------------------------------
    # Notification helper
    # ------------------------------------------------------------------

    async def _notify(self, event: dict) -> None:
        """Fire notify_callback if one is registered. Handles both sync and async callables."""
        if self._notify_callback is None:
            return
        try:
            result = self._notify_callback(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] notify_callback raised: %s", self.name, exc)

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        status = super().get_status()
        status.update(
            {
                "entry_threshold": self.entry_threshold,
                "exit_threshold": self.exit_threshold,
                "max_position_size": self.max_position_size,
                "locations": self.locations,
                "scan_frequency": self.scan_frequency,
                "risk_stats": self._risk_manager.get_stats(),
            }
        )
        return status
