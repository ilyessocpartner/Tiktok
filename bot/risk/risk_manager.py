"""
Risk management for the Polymarket trading bot.

Enforces daily loss limits and consecutive loss limits.
Auto-resets daily counters at midnight UTC using asyncio scheduling.
"""

import asyncio
import datetime
from typing import Any, Dict, List, Optional

from bot.utils.logger import setup_logger

logger = setup_logger(__name__)


class RiskManager:
    """
    Tracks trading P&L and enforces risk limits.

    Limits enforced:
    - Max daily loss: if cumulative daily P&L <= -max_daily_loss, trading is halted.
    - Max consecutive losses: if the streak of losing trades >= max_consecutive_losses,
      trading is halted.

    Daily counters reset automatically at midnight UTC.
    """

    def __init__(
        self,
        max_daily_loss: float,
        max_consecutive_losses: int,
        stop_loss_per_trade: float,
    ) -> None:
        """
        Args:
            max_daily_loss: Maximum allowable daily loss in USD (positive number).
                            Trading halts when daily_pnl <= -max_daily_loss.
            max_consecutive_losses: Number of consecutive losing trades that triggers a halt.
            stop_loss_per_trade: Per-trade stop loss in USD (informational; enforced externally).
        """
        if max_daily_loss <= 0:
            raise ValueError("max_daily_loss must be a positive number.")
        if max_consecutive_losses < 1:
            raise ValueError("max_consecutive_losses must be at least 1.")
        if stop_loss_per_trade <= 0:
            raise ValueError("stop_loss_per_trade must be a positive number.")

        self.max_daily_loss = max_daily_loss
        self.max_consecutive_losses = max_consecutive_losses
        self.stop_loss_per_trade = stop_loss_per_trade

        # Mutable state
        self._daily_pnl: float = 0.0
        self._consecutive_losses: int = 0
        self._trades_today: int = 0
        self._is_stopped: bool = False
        self._trade_history: List[Dict[str, Any]] = []

        # Asyncio task handle for the daily reset scheduler
        self._reset_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def can_trade(self) -> bool:
        """
        Returns True if it is safe to open a new trade.

        Halting conditions:
        - Daily P&L has reached or exceeded the max daily loss limit.
        - Consecutive losses have reached or exceeded the limit.
        - Manual stop has been triggered (is_stopped flag).
        """
        if self._is_stopped:
            logger.warning("Trading halted: manual stop flag is set.")
            return False
        if self._daily_pnl <= -self.max_daily_loss:
            logger.warning(
                "Trading halted: daily P&L %.2f has reached max daily loss limit -%.2f.",
                self._daily_pnl,
                self.max_daily_loss,
            )
            return False
        if self._consecutive_losses >= self.max_consecutive_losses:
            logger.warning(
                "Trading halted: %d consecutive losses reached the limit of %d.",
                self._consecutive_losses,
                self.max_consecutive_losses,
            )
            return False
        return True

    def record_trade(self, pnl: float, trade_info: Optional[Dict[str, Any]] = None) -> None:
        """
        Record the result of a completed trade.

        Args:
            pnl: Realized P&L for the trade (negative = loss).
            trade_info: Optional metadata dict to store alongside the record.
        """
        self._daily_pnl += pnl
        self._trades_today += 1

        if pnl > 0:
            self._consecutive_losses = 0
            logger.info(
                "Trade recorded: PnL=+%.4f | Daily PnL=%.4f | Consecutive losses reset to 0.",
                pnl,
                self._daily_pnl,
            )
        else:
            self._consecutive_losses += 1
            logger.info(
                "Trade recorded: PnL=%.4f | Daily PnL=%.4f | Consecutive losses=%d.",
                pnl,
                self._daily_pnl,
                self._consecutive_losses,
            )

        record: Dict[str, Any] = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "pnl": pnl,
            "daily_pnl_after": self._daily_pnl,
            "consecutive_losses_after": self._consecutive_losses,
        }
        if trade_info:
            record.update(trade_info)
        self._trade_history.append(record)

        # Announce halt conditions after recording
        if not self.can_trade():
            logger.warning(
                "Risk limits breached. Trading is now halted. Stats: %s", self.get_stats()
            )

    def get_stats(self) -> Dict[str, Any]:
        """
        Return a snapshot of current risk stats.

        Returns:
            dict with keys: daily_pnl, consecutive_losses, trades_today, is_stopped,
            max_daily_loss, max_consecutive_losses, stop_loss_per_trade, can_trade.
        """
        return {
            "daily_pnl": round(self._daily_pnl, 4),
            "consecutive_losses": self._consecutive_losses,
            "trades_today": self._trades_today,
            "is_stopped": self._is_stopped,
            "max_daily_loss": self.max_daily_loss,
            "max_consecutive_losses": self.max_consecutive_losses,
            "stop_loss_per_trade": self.stop_loss_per_trade,
            "can_trade": self.can_trade(),
        }

    def reset_daily(self) -> None:
        """
        Reset all daily counters. Called automatically at midnight UTC
        and can also be called manually (e.g., for testing).
        """
        prev = self.get_stats()
        self._daily_pnl = 0.0
        self._consecutive_losses = 0
        self._trades_today = 0
        self._is_stopped = False
        self._trade_history.clear()
        logger.info(
            "Daily risk counters reset. Previous stats: daily_pnl=%.4f, trades=%d, consecutive_losses=%d.",
            prev["daily_pnl"],
            prev["trades_today"],
            prev["consecutive_losses"],
        )

    def manual_stop(self) -> None:
        """Manually halt trading regardless of limits."""
        self._is_stopped = True
        logger.warning("Manual stop triggered. Trading halted until next daily reset.")

    # ------------------------------------------------------------------
    # Asyncio scheduling
    # ------------------------------------------------------------------

    async def start_auto_reset(self) -> None:
        """
        Launch a background asyncio task that resets daily counters at midnight UTC.
        Safe to call multiple times — will not start a second task if one is running.
        """
        if self._reset_task and not self._reset_task.done():
            logger.debug("Auto-reset task already running.")
            return
        self._reset_task = asyncio.create_task(self._midnight_reset_loop(), name="risk_daily_reset")
        logger.info("Risk manager auto-reset task started.")

    async def stop_auto_reset(self) -> None:
        """Cancel the midnight reset background task."""
        if self._reset_task and not self._reset_task.done():
            self._reset_task.cancel()
            try:
                await self._reset_task
            except asyncio.CancelledError:
                pass
            logger.info("Risk manager auto-reset task stopped.")

    async def _midnight_reset_loop(self) -> None:
        """Sleep until the next midnight UTC, reset, then repeat."""
        while True:
            seconds_until_midnight = self._seconds_until_midnight_utc()
            logger.debug(
                "Next daily risk reset in %.0f seconds (%.1f hours).",
                seconds_until_midnight,
                seconds_until_midnight / 3600,
            )
            await asyncio.sleep(seconds_until_midnight)
            self.reset_daily()

    @staticmethod
    def _seconds_until_midnight_utc() -> float:
        """Return the number of seconds until the next midnight UTC."""
        now = datetime.datetime.utcnow()
        tomorrow = (now + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        delta = tomorrow - now
        # Add a small buffer so we don't reset slightly before midnight
        return max(delta.total_seconds() + 1.0, 1.0)
