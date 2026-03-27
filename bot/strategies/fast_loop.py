"""
FastLoopStrategy — exploits short-term BTC price momentum in near-expiry markets.

Every scan_frequency seconds the strategy:
1. Reads the live BTC price and 30-second momentum from BTCFeed.
2. Finds Polymarket BTC/Bitcoin markets expiring within the next 20 minutes.
3. Detects mispricing when BTC has moved in a direction that the market price
   has not yet reflected.
4. Enters positions (YES or NO) when the signal clears entry_deviation.
5. Exits positions before market close, on a profit target (≥0.80), or on a
   stop-loss (≤0.20 for YES longs).
"""

import asyncio
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional

from bot.data.btc_feed import BTCFeed
from bot.strategies.base_strategy import BaseStrategy
from bot.utils.logger import setup_logger

logger = setup_logger(__name__)

# Regex to extract a dollar-amount strike price such as "$65,000" or "$65000".
_RE_STRIKE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")

# Known end-date field names in market dicts (Polymarket API is not always consistent).
_END_DATE_FIELDS = ("end_date", "endDate", "endDateIso", "end_date_iso")


def _parse_strike(question: str) -> Optional[float]:
    """Return the first dollar-amount found in *question*, or None."""
    m = _RE_STRIKE.search(question)
    if m is None:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_market_close_time(market: dict) -> Optional[datetime]:
    """
    Parse the market's close/end time from its dict and return an
    aware UTC datetime.

    Tries several field names and ISO-8601 parsing.  Returns None when no
    recognisable timestamp is found.
    """
    for field in _END_DATE_FIELDS:
        raw = market.get(field)
        if raw:
            try:
                # Remove trailing "Z" and parse as UTC
                dt_str: str = str(raw).replace("Z", "+00:00")
                dt = datetime.fromisoformat(dt_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except (ValueError, TypeError):
                continue

    # Some markets expose a Unix timestamp in "end_timestamp"
    raw_ts = market.get("end_timestamp") or market.get("endTimestamp")
    if raw_ts is not None:
        try:
            return datetime.fromtimestamp(float(raw_ts), tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            pass

    return None


def _seconds_until_close(market: dict) -> Optional[float]:
    """Return the number of seconds until *market* closes, or None."""
    close_dt = parse_market_close_time(market)
    if close_dt is None:
        return None
    delta = (close_dt - datetime.now(timezone.utc)).total_seconds()
    return delta


def _find_token(market: dict, outcome: str) -> Optional[dict]:
    """Return the token dict matching *outcome* ('Yes' or 'No'), or None."""
    target = outcome.lower()
    for token in market.get("tokens", []):
        if str(token.get("outcome", "")).lower() == target:
            return token
    return None


class FastLoopStrategy(BaseStrategy):
    """
    Short-duration BTC momentum trader targeting near-expiry Polymarket markets.

    Positions are sized in USDC and capped by *max_positions*.  The strategy
    always exits before *exit_before_close* seconds remain on the market clock.
    """

    # Seconds-to-close window in which a market is considered "active enough".
    _MARKET_ACTIVE_WITHIN: int = 20 * 60  # 20 minutes

    # Implied-probability thresholds for entry, profit-take, and stop-loss.
    _ENTRY_MAX_PROB: float = 0.55   # only enter when market is not yet priced in
    _TAKE_PROFIT_PROB: float = 0.80
    _STOP_LOSS_PROB: float = 0.20   # stop-loss for YES longs

    def __init__(
        self,
        poly_client: Any,
        risk_manager: Any,
        btc_feed: Optional[BTCFeed] = None,
        position_size: float = 5.0,
        max_positions: int = 3,
        scan_frequency: int = 5,
        exit_before_close: int = 15,
        entry_deviation: float = 0.005,
        notify_callback: Optional[Callable] = None,
    ) -> None:
        """
        Args:
            poly_client:       PolymarketClient instance.
            risk_manager:      RiskManager instance.
            btc_feed:          Optional BTCFeed instance. A new one is created if None.
            position_size:     Maximum USDC to risk per trade.
            max_positions:     Maximum number of simultaneously open positions.
            scan_frequency:    Seconds between scans.
            exit_before_close: Seconds before market close at which to force-exit.
            entry_deviation:   Minimum absolute price-change pct (from get_price_change_pct)
                               required to enter a trade.
            notify_callback:   Optional async/sync callable(event_dict) for alerts.
        """
        super().__init__(poly_client, risk_manager)
        self._btc_feed: BTCFeed = btc_feed if btc_feed is not None else BTCFeed()
        self.position_size = position_size
        self.max_positions = max_positions
        self.scan_frequency = scan_frequency
        self.exit_before_close = exit_before_close
        self.entry_deviation = entry_deviation
        self._notify_callback = notify_callback

        # Augmented position metadata beyond what BaseStrategy tracks.
        # token_id -> {side: "YES"|"NO", market: dict, ...}
        self._position_meta: Dict[str, dict] = {}

    # ------------------------------------------------------------------
    # BaseStrategy interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "FastLoop"

    async def run(self) -> None:
        """
        Main loop: start the BTC feed, then scan BTC markets every
        scan_frequency seconds.
        """
        self._running = True
        logger.info(
            "[%s] Strategy started. Scan frequency: %ds, exit_before_close: %ds.",
            self.name, self.scan_frequency, self.exit_before_close,
        )

        # Start the WebSocket price feed
        try:
            await self._btc_feed.start()
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] Failed to start BTCFeed: %s", self.name, exc)
            self._running = False
            return

        try:
            while self._running:
                try:
                    await self._manage_positions()
                    await self._scan_markets()
                except asyncio.CancelledError:
                    logger.info("[%s] Run loop cancelled.", self.name)
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "[%s] Unexpected error in run loop: %s",
                        self.name, exc, exc_info=True,
                    )

                if self._running:
                    await asyncio.sleep(self.scan_frequency)
        finally:
            await self._btc_feed.stop()
            logger.info("[%s] Strategy stopped.", self.name)

    # ------------------------------------------------------------------
    # Market scanning
    # ------------------------------------------------------------------

    async def _scan_markets(self) -> None:
        """Fetch near-expiry BTC markets and evaluate entry signals."""
        btc_price = self._btc_feed.get_price()
        if btc_price is None:
            logger.warning("[%s] BTC price unavailable — skipping scan.", self.name)
            return

        price_change_pct = self._btc_feed.get_price_change_pct(seconds=30)
        logger.debug(
            "[%s] BTC=%.2f | 30s_change=%.4f%%", self.name, btc_price, price_change_pct
        )

        # Fetch active BTC markets
        markets: List[dict] = []
        for keyword in ("BTC", "Bitcoin"):
            try:
                batch = await self._poly_client.get_markets(
                    keyword=keyword, active=True, limit=100
                )
                markets.extend(batch)
            except Exception as exc:  # noqa: BLE001
                logger.error("[%s] Failed to fetch '%s' markets: %s", self.name, keyword, exc)

        # Deduplicate by market id
        seen: set = set()
        unique_markets: List[dict] = []
        for m in markets:
            mid = m.get("id")
            if mid and mid not in seen:
                seen.add(mid)
                unique_markets.append(m)

        # Filter to markets closing within the next 20 minutes
        active_markets = []
        for market in unique_markets:
            secs = _seconds_until_close(market)
            if secs is None:
                continue
            if 0 < secs <= self._MARKET_ACTIVE_WITHIN:
                active_markets.append(market)

        logger.debug(
            "[%s] %d near-expiry BTC markets found.", self.name, len(active_markets)
        )

        for market in active_markets:
            try:
                await self._evaluate_market(market, btc_price, price_change_pct)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[%s] Error evaluating market %s: %s",
                    self.name, market.get("id", "?"), exc, exc_info=True,
                )

    async def _evaluate_market(
        self, market: dict, btc_price: float, price_change_pct: float
    ) -> None:
        """Decide whether to enter a position in the given market."""
        market_id: str = market.get("id", "")
        question: str = market.get("question", "")

        # Parse strike price
        strike = _parse_strike(question)
        if strike is None:
            logger.debug("[%s] No strike price in question: '%s'", self.name, question)
            return

        # Locate YES token
        yes_token = _find_token(market, "Yes")
        if yes_token is None:
            logger.debug("[%s] No YES token in market %s.", self.name, market_id)
            return

        yes_price = float(yes_token.get("price", 0.0))
        no_price = 1.0 - yes_price

        secs_to_close = _seconds_until_close(market)
        if secs_to_close is None or secs_to_close <= 0:
            logger.debug("[%s] Market %s already closed or unknown close time.", self.name, market_id)
            return

        # Don't enter if too close to close
        if secs_to_close <= self.exit_before_close:
            logger.debug(
                "[%s] Market %s closing in %.0fs — skipping entry.",
                self.name, market_id, secs_to_close,
            )
            return

        # Check position cap
        if len(self.open_positions) >= self.max_positions:
            logger.debug("[%s] Max positions (%d) reached.", self.name, self.max_positions)
            return

        # Skip markets we already hold
        yes_token_id = yes_token.get("token_id", "")
        if yes_token_id in self.open_positions:
            return

        # Strong momentum flag (double the base deviation)
        strong_momentum = abs(price_change_pct) >= 2.0 * self.entry_deviation

        # --- Signal logic ---
        side: Optional[str] = None
        token_id: Optional[str] = None
        entry_price: Optional[float] = None

        if (
            price_change_pct >= self.entry_deviation
            and btc_price > strike
            and yes_price < self._ENTRY_MAX_PROB
        ):
            # BTC is above strike and momentum is up — BUY YES
            side = "YES"
            token_id = yes_token_id
            entry_price = yes_price

        elif (
            price_change_pct <= -self.entry_deviation
            and btc_price < strike
            and no_price < self._ENTRY_MAX_PROB
        ):
            # BTC is below strike and momentum is down — BUY NO
            no_token = _find_token(market, "No")
            if no_token is None:
                # Synthesise NO position via YES token at inverse price
                token_id = yes_token_id
            else:
                token_id = no_token.get("token_id", yes_token_id)
            side = "NO"
            entry_price = no_price

        if side is None or token_id is None or entry_price is None:
            logger.debug(
                "[%s] No signal for market %s | BTC=%.2f | strike=%.2f | "
                "change_pct=%.4f%% | yes=%.4f | no=%.4f",
                self.name, market_id, btc_price, strike,
                price_change_pct, yes_price, no_price,
            )
            return

        if not self._risk_manager.can_trade():
            logger.info("[%s] Risk manager blocks trade for market %s.", self.name, market_id)
            return

        # Adjust position size for strong momentum
        trade_size = self.position_size * (1.25 if strong_momentum else 1.0)
        await self._enter_position(
            market=market,
            token_id=token_id,
            side=side,
            entry_price=entry_price,
            trade_size=trade_size,
            btc_price=btc_price,
            strike=strike,
            price_change_pct=price_change_pct,
        )

    async def _enter_position(
        self,
        market: dict,
        token_id: str,
        side: str,
        entry_price: float,
        trade_size: float,
        btc_price: float,
        strike: float,
        price_change_pct: float,
    ) -> None:
        """Place an order and record the new position."""
        market_id: str = market.get("id", "")

        if entry_price <= 0:
            logger.warning("[%s] Invalid entry price %.4f for token %s.", self.name, entry_price, token_id)
            return

        shares = trade_size / entry_price
        # Place slightly above current ask to improve fill likelihood
        limit_price = round(entry_price + 0.01, 4)

        logger.info(
            "[%s] ENTERING %s | market=%s | side=%s | BTC=%.2f | strike=%.2f | "
            "change_pct=%.4f%% | entry_price=%.4f | size_usdc=%.2f | shares=%.4f",
            self.name, side, market_id, side, btc_price, strike,
            price_change_pct, entry_price, trade_size, shares,
        )

        if not self._poly_client.dry_run:
            order_result = await self._poly_client.place_order(
                token_id=token_id,
                price=limit_price,
                size=shares,
                side="BUY",
            )
            if order_result is None:
                logger.warning("[%s] Order placement failed for token %s.", self.name, token_id)
                return
        else:
            logger.info("[%s] DRY RUN — BUY order not submitted.", self.name)

        now = datetime.now(timezone.utc)
        self.open_positions[token_id] = {
            "price": entry_price,
            "size": shares,
            "size_usdc": trade_size,
            "entry_time": now,
            "market_id": market_id,
        }
        self._position_meta[token_id] = {
            "side": side,
            "market": market,
            "btc_price_at_entry": btc_price,
            "strike": strike,
        }

        event = {
            "event": "entry",
            "strategy": self.name,
            "market_id": market_id,
            "token_id": token_id,
            "side": side,
            "entry_price": entry_price,
            "shares": shares,
            "size_usdc": trade_size,
            "btc_price": btc_price,
            "strike": strike,
        }
        await self._notify(event)

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    async def _manage_positions(self) -> None:
        """Check all open positions for exit conditions."""
        if not self.open_positions:
            return

        for token_id in list(self.open_positions.keys()):
            pos = self.open_positions.get(token_id)
            meta = self._position_meta.get(token_id)
            if pos is None:
                continue
            try:
                await self._check_exit(token_id, pos, meta)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[%s] Error managing position for token %s: %s",
                    self.name, token_id, exc, exc_info=True,
                )

    async def _check_exit(
        self,
        token_id: str,
        pos: dict,
        meta: Optional[dict],
    ) -> None:
        """Evaluate exit conditions for a single open position."""
        market = (meta or {}).get("market", {})
        side: str = (meta or {}).get("side", "YES")
        market_id: str = pos.get("market_id", "")

        secs_to_close = _seconds_until_close(market)

        # Refresh current market price via order book
        try:
            order_book = await self._poly_client.get_order_book(token_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Could not fetch order book for %s: %s", self.name, token_id, exc)
            order_book = {}

        bids: List[dict] = order_book.get("bids", [])
        current_price = float(bids[0]["price"]) if bids else pos["price"]

        # For NO positions the "current_price" is the YES price, so we invert
        display_price = current_price if side == "YES" else (1.0 - current_price)

        should_exit = False
        exit_reason = ""

        # 1. Time-based exit: market closing soon
        if secs_to_close is not None and secs_to_close <= self.exit_before_close:
            should_exit = True
            exit_reason = f"time_exit (closes_in={secs_to_close:.0f}s)"

        # 2. Take-profit (YES long)
        elif side == "YES" and current_price >= self._TAKE_PROFIT_PROB:
            should_exit = True
            exit_reason = f"take_profit (yes_price={current_price:.4f} >= {self._TAKE_PROFIT_PROB})"

        # 3. Stop-loss (YES long)
        elif side == "YES" and current_price <= self._STOP_LOSS_PROB:
            should_exit = True
            exit_reason = f"stop_loss (yes_price={current_price:.4f} <= {self._STOP_LOSS_PROB})"

        # 4. Take-profit on NO position (YES price has collapsed)
        elif side == "NO" and current_price <= (1.0 - self._TAKE_PROFIT_PROB):
            should_exit = True
            exit_reason = f"take_profit_no (yes_price={current_price:.4f} <= {1.0 - self._TAKE_PROFIT_PROB:.4f})"

        # 5. Stop-loss on NO position (YES price has risen sharply)
        elif side == "NO" and current_price >= (1.0 - self._STOP_LOSS_PROB):
            should_exit = True
            exit_reason = f"stop_loss_no (yes_price={current_price:.4f} >= {1.0 - self._STOP_LOSS_PROB:.4f})"

        if not should_exit:
            logger.debug(
                "[%s] %s pos %s: entry=%.4f current=%.4f — holding. close_in=%s",
                self.name, side, token_id, pos["price"], display_price,
                f"{secs_to_close:.0f}s" if secs_to_close is not None else "?",
            )
            return

        logger.info(
            "[%s] EXITING %s | token=%s | market=%s | reason=%s | "
            "entry=%.4f | current=%.4f",
            self.name, side, token_id, market_id,
            exit_reason, pos["price"], display_price,
        )

        pnl = (display_price - pos["price"]) * pos["size"]

        if not self._poly_client.dry_run:
            sell_price = max(current_price - 0.01, 0.01)
            sell_result = await self._poly_client.place_order(
                token_id=token_id,
                price=sell_price,
                size=pos["size"],
                side="SELL",
            )
            if sell_result is None:
                logger.warning("[%s] Sell order failed for token %s.", self.name, token_id)
                return
        else:
            logger.info("[%s] DRY RUN — SELL not submitted for token %s.", self.name, token_id)

        self._risk_manager.record_trade(
            pnl=pnl,
            trade_info={
                "strategy": self.name,
                "token_id": token_id,
                "market_id": market_id,
                "side": side,
                "entry_price": pos["price"],
                "exit_price": display_price,
                "size": pos["size"],
                "exit_reason": exit_reason,
            },
        )
        del self.open_positions[token_id]
        self._position_meta.pop(token_id, None)

        event = {
            "event": "exit",
            "strategy": self.name,
            "token_id": token_id,
            "market_id": market_id,
            "side": side,
            "entry_price": pos["price"],
            "exit_price": display_price,
            "pnl": pnl,
            "exit_reason": exit_reason,
        }
        await self._notify(event)

    # ------------------------------------------------------------------
    # Public helper (exposed as per interface contract)
    # ------------------------------------------------------------------

    def parse_market_close_time(self, market: dict) -> Optional[datetime]:
        """
        Public wrapper around the module-level parse_market_close_time function.

        Args:
            market: Market dict as returned by PolymarketClient.get_markets().

        Returns:
            Aware UTC datetime of market close, or None.
        """
        return parse_market_close_time(market)

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
                "btc_price": self._btc_feed.get_price(),
                "btc_30s_change_pct": self._btc_feed.get_price_change_pct(seconds=30),
                "btc_feed_running": self._btc_feed.is_running,
                "position_size": self.position_size,
                "max_positions": self.max_positions,
                "scan_frequency": self.scan_frequency,
                "exit_before_close": self.exit_before_close,
                "entry_deviation": self.entry_deviation,
                "risk_stats": self._risk_manager.get_stats(),
            }
        )
        return status
