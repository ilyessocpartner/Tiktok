"""
Configuration module for the Polymarket trading bot.
Loads all settings from environment variables with sensible defaults.
"""

import os
import warnings
from typing import List

from dotenv import load_dotenv

load_dotenv()


class Config:
    """
    Central configuration class. All values are loaded from environment variables.
    DRY_RUN defaults to True for safety — set DRY_RUN=false in .env to enable live trading.
    """

    # --- Polymarket credentials ---
    PRIVATE_KEY: str = os.environ.get("PRIVATE_KEY", "")
    POLY_API_KEY: str = os.environ.get("POLY_API_KEY", "")
    POLY_API_SECRET: str = os.environ.get("POLY_API_SECRET", "")
    POLY_API_PASSPHRASE: str = os.environ.get("POLY_API_PASSPHRASE", "")

    # --- Telegram ---
    TELEGRAM_TOKEN: str = os.environ.get("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")

    # --- Safety ---
    DRY_RUN: bool = os.environ.get("DRY_RUN", "true").strip().lower() not in ("false", "0", "no")

    # --- Strategy toggles ---
    ENABLE_WEATHER_TRADER: bool = os.environ.get("ENABLE_WEATHER_TRADER", "true").strip().lower() not in ("false", "0", "no")
    ENABLE_FAST_LOOP: bool = os.environ.get("ENABLE_FAST_LOOP", "false").strip().lower() not in ("false", "0", "no")

    # --- Weather trader settings ---
    WEATHER_ENTRY_THRESHOLD: float = float(os.environ.get("WEATHER_ENTRY_THRESHOLD", "0.08"))
    WEATHER_EXIT_THRESHOLD: float = float(os.environ.get("WEATHER_EXIT_THRESHOLD", "0.08"))
    WEATHER_MAX_POSITION: float = float(os.environ.get("WEATHER_MAX_POSITION", "2.0"))
    WEATHER_SCAN_FREQ: int = int(os.environ.get("WEATHER_SCAN_FREQ", "120"))

    _weather_locations_raw: str = os.environ.get(
        "WEATHER_LOCATIONS", "NYC,Chicago,Seattle,Atlanta,Dallas,Miami"
    )
    WEATHER_LOCATIONS: List[str] = [
        loc.strip() for loc in _weather_locations_raw.split(",") if loc.strip()
    ]

    # --- Fast loop settings ---
    FAST_LOOP_POSITION_SIZE: float = float(os.environ.get("FAST_LOOP_POSITION_SIZE", "5.0"))
    FAST_LOOP_MAX_POSITIONS: int = int(os.environ.get("FAST_LOOP_MAX_POSITIONS", "3"))
    FAST_LOOP_SCAN_FREQ: int = int(os.environ.get("FAST_LOOP_SCAN_FREQ", "5"))
    FAST_LOOP_EXIT_BEFORE_CLOSE: int = int(os.environ.get("FAST_LOOP_EXIT_BEFORE_CLOSE", "15"))
    FAST_LOOP_ENTRY_DEVIATION: float = float(os.environ.get("FAST_LOOP_ENTRY_DEVIATION", "0.005"))

    # --- Risk management ---
    MAX_DAILY_LOSS: float = float(os.environ.get("MAX_DAILY_LOSS", "50.0"))
    MAX_CONSECUTIVE_LOSSES: int = int(os.environ.get("MAX_CONSECUTIVE_LOSSES", "3"))
    STOP_LOSS_PER_TRADE: float = float(os.environ.get("STOP_LOSS_PER_TRADE", "3.0"))

    # --- Logging ---
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()

    def __init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        """Warn about missing critical configuration values."""
        if not self.PRIVATE_KEY:
            warnings.warn(
                "PRIVATE_KEY is not set. Live trading will not be possible. "
                "Set PRIVATE_KEY in your .env file or environment.",
                UserWarning,
                stacklevel=2,
            )
        if not self.POLY_API_KEY:
            warnings.warn(
                "POLY_API_KEY is not set. CLOB authenticated endpoints will fail.",
                UserWarning,
                stacklevel=2,
            )
        if not self.TELEGRAM_TOKEN:
            warnings.warn(
                "TELEGRAM_TOKEN is not set. Telegram notifications will be disabled.",
                UserWarning,
                stacklevel=2,
            )
        if not self.DRY_RUN and not self.PRIVATE_KEY:
            raise ValueError(
                "DRY_RUN is disabled but PRIVATE_KEY is not set. "
                "Cannot run in live mode without a private key."
            )

    def __repr__(self) -> str:
        pk_preview = (self.PRIVATE_KEY[:6] + "...") if self.PRIVATE_KEY else "<not set>"
        return (
            f"Config("
            f"DRY_RUN={self.DRY_RUN}, "
            f"PRIVATE_KEY={pk_preview}, "
            f"ENABLE_WEATHER_TRADER={self.ENABLE_WEATHER_TRADER}, "
            f"ENABLE_FAST_LOOP={self.ENABLE_FAST_LOOP}, "
            f"MAX_DAILY_LOSS={self.MAX_DAILY_LOSS}, "
            f"LOG_LEVEL={self.LOG_LEVEL}"
            f")"
        )
