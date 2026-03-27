"""
Telegram bot interface for the Polymarket trading bot.
Uses python-telegram-bot v21 (async API).
"""

import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Telegram bot that exposes trading commands and sends notifications.

    Parameters
    ----------
    token:
        Bot token obtained from @BotFather.
    chat_id:
        The Telegram chat / user ID that is authorised to control the bot.
    strategies:
        List of strategy objects.  Each must expose ``start()`` and ``stop()``
        coroutines and a ``name`` attribute.
    risk_manager:
        Risk-manager object that exposes ``get_stats()`` and ``poly_client``
        (which must expose ``get_balance()``).
    """

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def __init__(
        self,
        token: str,
        chat_id: str,
        strategies: list,
        risk_manager,
    ) -> None:
        self.token = token
        self.chat_id = str(chat_id)
        self.strategies = strategies
        self.risk_manager = risk_manager

        self._app: Optional[Application] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Build the Application and register all command/callback handlers."""
        self._app = Application.builder().token(self.token).build()

        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("stop_trading", self._cmd_stop_trading))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume))
        self._app.add_handler(CommandHandler("balance", self._cmd_balance))
        self._app.add_handler(CommandHandler("stats", self._cmd_stats))
        self._app.add_handler(CommandHandler("help", self._cmd_help))

        self._app.add_handler(
            CallbackQueryHandler(self._on_callback_query)
        )

        await self._app.initialize()
        logger.info("TelegramBot handlers registered")

    async def run_polling(self) -> None:
        """Start long-polling in a non-blocking asyncio manner."""
        if self._app is None:
            await self.start()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("TelegramBot polling started")

    async def stop_polling(self) -> None:
        """Stop the polling loop gracefully."""
        if self._app and self._app.updater.running:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("TelegramBot polling stopped")

    # ------------------------------------------------------------------
    # Notification helper
    # ------------------------------------------------------------------

    async def notify(self, message: str) -> None:
        """Send *message* to the configured chat_id (Markdown formatting)."""
        if self._app is None:
            logger.warning("TelegramBot not started — cannot send notification")
            return
        try:
            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send Telegram notification: %s", exc)

    # ------------------------------------------------------------------
    # Authorization guard
    # ------------------------------------------------------------------

    def _is_authorised(self, update: Update) -> bool:
        """Return True only if the update comes from the configured chat_id."""
        return str(update.effective_chat.id) == self.chat_id

    async def _unauthorised(self, update: Update) -> None:
        await update.message.reply_text("⛔ Unauthorized.")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorised(update):
            await self._unauthorised(update)
            return

        text = (
            "🤖 *Polymarket Trading Bot*\n\n"
            "Bienvenue! Le bot est en ligne.\n\n"
            "*Commandes disponibles :*\n"
            "/status — État de toutes les stratégies\n"
            "/balance — Solde du portefeuille\n"
            "/stats — Statistiques P&L\n"
            "/stop_trading — Mettre en pause toutes les stratégies\n"
            "/resume — Relancer les stratégies\n"
            "/help — Afficher l'aide\n"
        )

        keyboard = [
            [
                InlineKeyboardButton("📊 Status", callback_data="status"),
                InlineKeyboardButton("⏹ Stop Trading", callback_data="stop_trading"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorised(update):
            await self._unauthorised(update)
            return

        text = (
            "📖 *Aide — Polymarket Bot*\n\n"
            "`/start` — Message de bienvenue\n"
            "`/status` — État des stratégies & risk manager\n"
            "`/balance` — Solde USDC du portefeuille\n"
            "`/stats` — P&L journalier et statistiques\n"
            "`/stop_trading` — Mettre en pause toutes les stratégies\n"
            "`/resume` — Relancer toutes les stratégies\n"
            "`/help` — Afficher ce message\n"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorised(update):
            await self._unauthorised(update)
            return

        lines = ["📊 *Status des stratégies*\n"]
        for strat in self.strategies:
            is_running = getattr(strat, "running", None)
            if is_running is None:
                is_running = getattr(strat, "_running", False)
            icon = "✅" if is_running else "⏸"
            name = getattr(strat, "name", type(strat).__name__)
            lines.append(f"{icon} *{name}*: {'active' if is_running else 'paused'}")

        try:
            stats = self.risk_manager.get_stats()
            lines.append("\n📈 *Risk Manager*")
            lines.append(f"• Perte journalière : `{stats.get('daily_loss', 0):.2f}` USDC")
            lines.append(f"• Pertes consécutives : `{stats.get('consecutive_losses', 0)}`")
            lines.append(f"• Trades aujourd'hui : `{stats.get('trades_today', 0)}`")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch risk manager stats: %s", exc)
            lines.append("\n⚠️ Impossible de récupérer les stats du risk manager.")

        keyboard = [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="status"),
                InlineKeyboardButton("⏹ Stop Trading", callback_data="stop_trading"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )

    async def _cmd_stop_trading(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorised(update):
            await self._unauthorised(update)
            return

        await update.message.reply_text("⏳ Arrêt de toutes les stratégies en cours...")
        errors = []
        for strat in self.strategies:
            try:
                await strat.stop()
            except Exception as exc:  # noqa: BLE001
                name = getattr(strat, "name", type(strat).__name__)
                logger.error("Error stopping strategy %s: %s", name, exc)
                errors.append(name)

        if errors:
            await update.message.reply_text(
                f"⚠️ Stratégies arrêtées avec erreurs : `{', '.join(errors)}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text("⏹ Toutes les stratégies sont mises en pause.")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorised(update):
            await self._unauthorised(update)
            return

        await update.message.reply_text("⏳ Relancement des stratégies...")
        errors = []
        for strat in self.strategies:
            try:
                await strat.start()
            except Exception as exc:  # noqa: BLE001
                name = getattr(strat, "name", type(strat).__name__)
                logger.error("Error resuming strategy %s: %s", name, exc)
                errors.append(name)

        if errors:
            await update.message.reply_text(
                f"⚠️ Erreur au démarrage : `{', '.join(errors)}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text("▶️ Toutes les stratégies ont été relancées.")

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorised(update):
            await self._unauthorised(update)
            return

        try:
            poly_client = self.risk_manager.poly_client
            balance = await poly_client.get_balance()
            await update.message.reply_text(
                f"💰 *Solde du portefeuille*\n\n`{balance:.4f}` USDC",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Error fetching balance: %s", exc)
            await update.message.reply_text(
                "⚠️ Impossible de récupérer le solde du portefeuille."
            )

    async def _cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorised(update):
            await self._unauthorised(update)
            return

        try:
            stats = self.risk_manager.get_stats()
            text = (
                "📈 *Statistiques P&L*\n\n"
                f"• Profit net : `{stats.get('net_profit', 0):.2f}` USDC\n"
                f"• Perte journalière : `{stats.get('daily_loss', 0):.2f}` USDC\n"
                f"• Perte max journalière : `{stats.get('max_daily_loss', 0):.2f}` USDC\n"
                f"• Trades gagnants : `{stats.get('winning_trades', 0)}`\n"
                f"• Trades perdants : `{stats.get('losing_trades', 0)}`\n"
                f"• Pertes consécutives : `{stats.get('consecutive_losses', 0)}`\n"
                f"• Trading actif : `{'Oui' if stats.get('trading_enabled', True) else 'Non'}`\n"
            )
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error fetching stats: %s", exc)
            await update.message.reply_text(
                "⚠️ Impossible de récupérer les statistiques."
            )

    # ------------------------------------------------------------------
    # Inline keyboard callback handler
    # ------------------------------------------------------------------

    async def _on_callback_query(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()

        if not self._is_authorised(update):
            await query.edit_message_text("⛔ Unauthorized.")
            return

        data = query.data

        if data == "status":
            # Re-use the status logic but send as an edit
            lines = ["📊 *Status des stratégies*\n"]
            for strat in self.strategies:
                is_running = getattr(strat, "running", getattr(strat, "_running", False))
                icon = "✅" if is_running else "⏸"
                name = getattr(strat, "name", type(strat).__name__)
                lines.append(f"{icon} *{name}*: {'active' if is_running else 'paused'}")

            try:
                stats = self.risk_manager.get_stats()
                lines.append("\n📈 *Risk Manager*")
                lines.append(f"• Perte journalière : `{stats.get('daily_loss', 0):.2f}` USDC")
                lines.append(f"• Pertes consécutives : `{stats.get('consecutive_losses', 0)}`")
            except Exception:  # noqa: BLE001
                lines.append("\n⚠️ Stats indisponibles.")

            keyboard = [
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="status"),
                    InlineKeyboardButton("⏹ Stop Trading", callback_data="stop_trading"),
                ]
            ]
            await query.edit_message_text(
                "\n".join(lines),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

        elif data == "stop_trading":
            for strat in self.strategies:
                try:
                    await strat.stop()
                except Exception as exc:  # noqa: BLE001
                    logger.error("Error stopping strategy via callback: %s", exc)
            await query.edit_message_text(
                "⏹ Toutes les stratégies ont été mises en pause.",
                parse_mode=ParseMode.MARKDOWN,
            )

        else:
            logger.warning("Unknown callback_data: %s", data)
