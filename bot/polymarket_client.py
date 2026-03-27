"""
Polymarket API client for market discovery, order placement, and position management.

Uses:
  - Gamma API (https://gamma-api.polymarket.com) for market data via aiohttp
  - py-clob-client library for authenticated order placement on the CLOB

When dry_run=True, orders are logged but never submitted; mock responses are returned.
"""

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import aiohttp

from bot.utils.logger import setup_logger

logger = setup_logger(__name__)

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"

# Lazy imports for py-clob-client so the module can be imported even if the
# library is not installed (useful for unit tests / dry-run-only deployments).
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, MarketOrderArgs, OrderArgs, OrderType
    from py_clob_client.constants import POLYGON

    _CLOB_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CLOB_AVAILABLE = False
    logger.warning(
        "py-clob-client is not installed. Live order placement will be unavailable."
    )


class PolymarketClient:
    """
    Async Polymarket client.

    Market discovery is done via the Gamma REST API.
    Order placement and account management use py-clob-client.

    Args:
        private_key: Ethereum private key (hex, with or without 0x prefix).
        api_key: Polymarket CLOB API key.
        api_secret: Polymarket CLOB API secret.
        api_passphrase: Polymarket CLOB API passphrase.
        dry_run: If True, orders are simulated and never sent to the exchange.
    """

    def __init__(
        self,
        private_key: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        dry_run: bool = True,
    ) -> None:
        self._private_key = private_key
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._dry_run = dry_run

        self._session: Optional[aiohttp.ClientSession] = None
        self._clob_client: Optional[Any] = None

        if not dry_run and not _CLOB_AVAILABLE:
            raise RuntimeError(
                "py-clob-client must be installed for live trading (dry_run=False)."
            )

        if not dry_run:
            self._init_clob_client()

        logger.info(
            "PolymarketClient initialised. dry_run=%s, CLOB available=%s",
            dry_run,
            _CLOB_AVAILABLE,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def dry_run(self) -> bool:
        """True when running in simulation mode (no real orders submitted)."""
        return self._dry_run

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def _init_clob_client(self) -> None:
        """Initialise the synchronous py-clob-client instance."""
        if not _CLOB_AVAILABLE:
            return
        try:
            creds = ApiCreds(
                api_key=self._api_key,
                api_secret=self._api_secret,
                api_passphrase=self._api_passphrase,
            )
            self._clob_client = ClobClient(
                host=CLOB_HOST,
                chain_id=POLYGON,
                key=self._private_key,
                creds=creds,
            )
            logger.info("ClobClient initialised successfully.")
        except Exception as exc:
            logger.error("Failed to initialise ClobClient: %s", exc)
            self._clob_client = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return (or lazily create) the shared aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "PolymarketBot/1.0"},
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self) -> None:
        """Close the underlying HTTP session. Call on shutdown."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("PolymarketClient HTTP session closed.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _gamma_get(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Any]:
        """
        Perform a GET against the Gamma API.

        Returns parsed JSON on success, None on any error.
        """
        url = f"{GAMMA_BASE_URL}{path}"
        session = await self._get_session()
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                body = await resp.text()
                logger.error(
                    "Gamma API error: GET %s -> HTTP %d | %s",
                    url,
                    resp.status,
                    body[:200],
                )
                return None
        except aiohttp.ClientError as exc:
            logger.error("Gamma API request failed for %s: %s", url, exc)
            return None
        except Exception as exc:
            logger.error("Unexpected error in Gamma API request for %s: %s", url, exc)
            return None

    @staticmethod
    def _parse_market(raw: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalise a raw Gamma API market dict into the canonical structure:

            {
                id, question, slug, outcomes, outcomePrices, volume, liquidity,
                tokens: [{token_id, outcome, price}]
            }
        """
        # outcomePrices may be a JSON string or a list
        outcome_prices_raw = raw.get("outcomePrices", [])
        if isinstance(outcome_prices_raw, str):
            import json as _json

            try:
                outcome_prices_raw = _json.loads(outcome_prices_raw)
            except Exception:
                outcome_prices_raw = []

        outcomes_raw = raw.get("outcomes", [])
        if isinstance(outcomes_raw, str):
            import json as _json

            try:
                outcomes_raw = _json.loads(outcomes_raw)
            except Exception:
                outcomes_raw = []

        # Build tokens list from clobTokenIds / outcomes / outcomePrices
        clob_token_ids = raw.get("clobTokenIds", [])
        if isinstance(clob_token_ids, str):
            import json as _json

            try:
                clob_token_ids = _json.loads(clob_token_ids)
            except Exception:
                clob_token_ids = []

        tokens: List[Dict[str, Any]] = []
        for i, outcome in enumerate(outcomes_raw):
            token_id = clob_token_ids[i] if i < len(clob_token_ids) else ""
            price_str = (
                outcome_prices_raw[i] if i < len(outcome_prices_raw) else "0"
            )
            try:
                price = float(price_str)
            except (TypeError, ValueError):
                price = 0.0
            tokens.append(
                {
                    "token_id": token_id,
                    "outcome": outcome,
                    "price": price,
                }
            )

        return {
            "id": raw.get("id", ""),
            "question": raw.get("question", ""),
            "slug": raw.get("slug", ""),
            "outcomes": outcomes_raw,
            "outcomePrices": outcome_prices_raw,
            "volume": raw.get("volume", 0),
            "liquidity": raw.get("liquidity", 0),
            "tokens": tokens,
            # Preserve raw fields for downstream use
            "_raw": raw,
        }

    # ------------------------------------------------------------------
    # Public API — Market Discovery
    # ------------------------------------------------------------------

    async def get_markets(
        self,
        keyword: Optional[str] = None,
        active: bool = True,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Fetch markets from the Gamma API.

        Args:
            keyword: Optional free-text search filter applied to market questions.
            active: If True, return active (not closed) markets.
            limit: Maximum number of markets to return.

        Returns:
            List of normalised market dicts. Empty list on error.
        """
        params: Dict[str, Any] = {
            "active": str(active).lower(),
            "closed": str(not active).lower(),
            "limit": limit,
        }
        if keyword:
            params["keyword"] = keyword

        logger.debug("Fetching markets: params=%s", params)
        data = await self._gamma_get("/markets", params=params)

        if data is None:
            return []

        # Gamma returns either a list directly or {"data": [...]}
        if isinstance(data, list):
            raw_markets = data
        elif isinstance(data, dict):
            raw_markets = data.get("data", data.get("markets", []))
        else:
            logger.error("Unexpected markets response type: %s", type(data))
            return []

        markets = [self._parse_market(m) for m in raw_markets if isinstance(m, dict)]
        logger.info("Fetched %d markets (keyword=%r, active=%s).", len(markets), keyword, active)
        return markets

    # ------------------------------------------------------------------
    # Public API — Order Book
    # ------------------------------------------------------------------

    async def get_order_book(self, token_id: str) -> Dict[str, Any]:
        """
        Fetch the CLOB order book for a given outcome token.

        Args:
            token_id: The CLOB token ID for the outcome (e.g. YES token).

        Returns:
            Dict with keys:
                bids: list of {price: float, size: float}
                asks: list of {price: float, size: float}
            Returns empty bids/asks on error.
        """
        empty: Dict[str, Any] = {"bids": [], "asks": []}
        if not token_id:
            logger.warning("get_order_book called with empty token_id.")
            return empty

        data = await self._gamma_get(f"/book", params={"token_id": token_id})
        if data is None:
            # Fallback: try CLOB client if available
            if self._clob_client is not None:
                return await self._get_order_book_clob(token_id)
            return empty

        def _parse_side(entries: Any) -> List[Dict[str, float]]:
            result = []
            if not isinstance(entries, list):
                return result
            for entry in entries:
                try:
                    result.append(
                        {
                            "price": float(entry.get("price", 0)),
                            "size": float(entry.get("size", entry.get("amount", 0))),
                        }
                    )
                except (TypeError, ValueError):
                    continue
            return result

        return {
            "bids": _parse_side(data.get("bids", [])),
            "asks": _parse_side(data.get("asks", [])),
        }

    async def _get_order_book_clob(self, token_id: str) -> Dict[str, Any]:
        """Fetch order book using py-clob-client as a fallback."""
        empty: Dict[str, Any] = {"bids": [], "asks": []}
        try:
            loop = asyncio.get_event_loop()
            book = await loop.run_in_executor(
                None, lambda: self._clob_client.get_order_book(token_id)
            )
            if book is None:
                return empty

            def _convert(side: Any) -> List[Dict[str, float]]:
                if not side:
                    return []
                return [{"price": float(e.price), "size": float(e.size)} for e in side]

            return {
                "bids": _convert(getattr(book, "bids", [])),
                "asks": _convert(getattr(book, "asks", [])),
            }
        except Exception as exc:
            logger.error("CLOB order book fetch failed for token_id=%s: %s", token_id, exc)
            return empty

    # ------------------------------------------------------------------
    # Public API — Order Placement
    # ------------------------------------------------------------------

    async def place_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str = "BUY",
    ) -> Optional[Dict[str, Any]]:
        """
        Place a limit order for an outcome token.

        Args:
            token_id: CLOB token ID of the outcome to trade.
            price: Limit price in USDC (0.0 – 1.0 for binary outcomes).
            size: Order size in USDC.
            side: "BUY" or "SELL".

        Returns:
            Order response dict on success, or a mock dict in dry_run mode.
            Returns None if the order fails.
        """
        side_upper = side.upper()
        if side_upper not in ("BUY", "SELL"):
            logger.error("Invalid order side '%s'. Must be BUY or SELL.", side)
            return None

        if not (0.0 < price < 1.0):
            logger.error(
                "Price %.4f is outside the valid range (0, 1) for binary outcome tokens.",
                price,
            )
            return None

        if size <= 0:
            logger.error("Order size must be positive, got %.4f.", size)
            return None

        logger.info(
            "[%s] place_order: token_id=%s, side=%s, price=%.4f, size=%.4f",
            "DRY" if self._dry_run else "LIVE",
            token_id,
            side_upper,
            price,
            size,
        )

        if self._dry_run:
            mock_order = {
                "order_id": f"dry-run-{uuid.uuid4().hex[:12]}",
                "token_id": token_id,
                "side": side_upper,
                "price": price,
                "size": size,
                "status": "SIMULATED",
                "timestamp": time.time(),
            }
            logger.info("[DRY RUN] Simulated order: %s", mock_order)
            return mock_order

        # --- Live order placement ---
        if self._clob_client is None:
            logger.error("ClobClient is not initialised. Cannot place live order.")
            return None

        try:
            loop = asyncio.get_event_loop()

            def _create_and_post() -> Any:
                order_args = OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side=side_upper,
                )
                signed_order = self._clob_client.create_order(order_args)
                return self._clob_client.post_order(signed_order, OrderType.GTC)

            response = await loop.run_in_executor(None, _create_and_post)

            if response is None:
                logger.error("post_order returned None for token_id=%s.", token_id)
                return None

            order_id = getattr(response, "orderID", None) or getattr(
                response, "order_id", str(response)
            )
            result = {
                "order_id": order_id,
                "token_id": token_id,
                "side": side_upper,
                "price": price,
                "size": size,
                "status": "LIVE",
                "timestamp": time.time(),
                "_raw_response": str(response),
            }
            logger.info("Live order placed successfully: %s", result)
            return result

        except Exception as exc:
            logger.error(
                "Failed to place live order for token_id=%s: %s", token_id, exc
            )
            return None

    # ------------------------------------------------------------------
    # Public API — Positions
    # ------------------------------------------------------------------

    async def get_positions(self) -> List[Dict[str, Any]]:
        """
        Return open positions for the authenticated account.

        Returns:
            List of position dicts. Empty list on error or in dry_run mode
            (dry_run has no real positions).
        """
        if self._dry_run:
            logger.debug("[DRY RUN] get_positions called — returning empty list.")
            return []

        if self._clob_client is None:
            logger.error("ClobClient is not initialised. Cannot fetch positions.")
            return []

        try:
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None, self._clob_client.get_positions
            )
            if raw is None:
                return []
            if isinstance(raw, list):
                return raw
            # Some SDK versions return an object with a data attribute
            return getattr(raw, "data", []) or []
        except Exception as exc:
            logger.error("Failed to fetch positions: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Public API — Order Cancellation
    # ------------------------------------------------------------------

    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order by ID.

        Args:
            order_id: The exchange order ID to cancel.

        Returns:
            True on success, False on failure.
            In dry_run mode always returns True (simulated success).
        """
        if self._dry_run:
            logger.info("[DRY RUN] Simulated cancel for order_id=%s.", order_id)
            return True

        if self._clob_client is None:
            logger.error("ClobClient is not initialised. Cannot cancel order.")
            return False

        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None, lambda: self._clob_client.cancel(order_id)
            )
            success = resp is not None
            if success:
                logger.info("Order %s cancelled successfully.", order_id)
            else:
                logger.warning("Cancel request for order %s returned None.", order_id)
            return success
        except Exception as exc:
            logger.error("Failed to cancel order %s: %s", order_id, exc)
            return False

    # ------------------------------------------------------------------
    # Public API — Balance
    # ------------------------------------------------------------------

    async def get_balance(self) -> float:
        """
        Return the account's USDC balance.

        Returns:
            USDC balance as a float. Returns 0.0 on error or in dry_run mode.
        """
        if self._dry_run:
            logger.debug("[DRY RUN] get_balance called — returning mock balance 1000.0.")
            return 1000.0

        if self._clob_client is None:
            logger.error("ClobClient is not initialised. Cannot fetch balance.")
            return 0.0

        try:
            loop = asyncio.get_event_loop()
            balance_info = await loop.run_in_executor(
                None, self._clob_client.get_balance
            )
            if balance_info is None:
                return 0.0
            # SDK may return a dict, a number, or an object
            if isinstance(balance_info, (int, float)):
                return float(balance_info)
            if isinstance(balance_info, dict):
                return float(balance_info.get("balance", balance_info.get("USDC", 0.0)))
            return float(getattr(balance_info, "balance", 0.0))
        except Exception as exc:
            logger.error("Failed to fetch balance: %s", exc)
            return 0.0
