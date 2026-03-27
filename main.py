"""
Polymarket Trading Bot — Entry Point
Strategies: Weather Trader (temperature brackets) + Fast Loop (BTC 5/15-min markets)
"""

import asyncio
import signal
import sys

from dotenv import load_dotenv

load_dotenv()

from config.config import Config
from bot.polymarket_client import PolymarketClient
from bot.risk.risk_manager import RiskManager
from bot.telegram_bot import TelegramBot
from bot.utils.logger import setup_logger

logger = setup_logger(__name__)


async def main() -> None:
    config = Config()
    logger.info("Starting Polymarket Trading Bot — DRY_RUN=%s", config.DRY_RUN)
    logger.info(repr(config))

    risk_manager = RiskManager(
        max_daily_loss=config.MAX_DAILY_LOSS,
        max_consecutive_losses=config.MAX_CONSECUTIVE_LOSSES,
        stop_loss_per_trade=config.STOP_LOSS_PER_TRADE,
    )

    poly_client = PolymarketClient(
        private_key=config.PRIVATE_KEY,
        api_key=config.POLY_API_KEY,
        api_secret=config.POLY_API_SECRET,
        api_passphrase=config.POLY_API_PASSPHRASE,
        dry_run=config.DRY_RUN,
    )

    strategies = []

    if config.ENABLE_WEATHER_TRADER:
        from bot.strategies.weather_trader import WeatherTraderStrategy

        weather = WeatherTraderStrategy(
            poly_client=poly_client,
            risk_manager=risk_manager,
            entry_threshold=config.WEATHER_ENTRY_THRESHOLD,
            exit_threshold=config.WEATHER_EXIT_THRESHOLD,
            max_position_size=config.WEATHER_MAX_POSITION,
            locations=config.WEATHER_LOCATIONS,
            scan_frequency=config.WEATHER_SCAN_FREQ,
        )
        strategies.append(weather)
        logger.info("Weather Trader strategy enabled (locations: %s)", config.WEATHER_LOCATIONS)

    if config.ENABLE_FAST_LOOP:
        from bot.strategies.fast_loop import FastLoopStrategy

        fast_loop = FastLoopStrategy(
            poly_client=poly_client,
            risk_manager=risk_manager,
            position_size=config.FAST_LOOP_POSITION_SIZE,
            max_positions=config.FAST_LOOP_MAX_POSITIONS,
            scan_frequency=config.FAST_LOOP_SCAN_FREQ,
            exit_before_close=config.FAST_LOOP_EXIT_BEFORE_CLOSE,
            entry_deviation=config.FAST_LOOP_ENTRY_DEVIATION,
        )
        strategies.append(fast_loop)
        logger.info("Fast Loop strategy enabled (BTC 5/15-min markets)")

    if not strategies:
        logger.warning("No strategies enabled. Set ENABLE_WEATHER_TRADER=true or ENABLE_FAST_LOOP=true in .env")

    telegram = TelegramBot(
        token=config.TELEGRAM_TOKEN,
        chat_id=config.TELEGRAM_CHAT_ID,
        strategies=strategies,
        risk_manager=risk_manager,
    )

    # Wire Telegram notify callback into each strategy
    for strategy in strategies:
        strategy.notify_callback = telegram.notify

    # Graceful shutdown
    loop = asyncio.get_running_loop()

    def _handle_shutdown(sig: signal.Signals) -> None:
        logger.info("Signal %s received — stopping bot...", sig.name)
        for s in strategies:
            s.stop()
        loop.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_shutdown, sig)

    await telegram.start()

    tasks = [asyncio.create_task(s.run(), name=s.name) for s in strategies]
    tasks.append(asyncio.create_task(telegram.run_polling(), name="telegram"))

    logger.info("Bot running. Send /status to your Telegram bot for updates.")

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Tasks cancelled — shutdown complete.")
    finally:
        await poly_client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
