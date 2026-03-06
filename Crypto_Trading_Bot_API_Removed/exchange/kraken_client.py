import logging
import time
from typing import Optional
import ccxt
import pandas as pd

from config import Config

logger = logging.getLogger(__name__)


class KrakenClient:
    """
    Wrapper around ccxt Kraken for spot (non-margin) trading.
    All order placement respects Config.DRY_RUN — in dry-run mode,
    orders are logged but never sent to the exchange.
    """

    def __init__(self) -> None:
        self._exchange = ccxt.kraken(
            {
                "apiKey": Config.KRAKEN_API_KEY,
                "secret": Config.KRAKEN_API_SECRET,
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
        )
        self._dry_run = Config.DRY_RUN
        if self._dry_run:
            logger.info("KrakenClient initialised in DRY-RUN mode — no real orders will be placed.")

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        """Return OHLCV data as a DataFrame with columns: timestamp, open, high, low, close, volume."""
        raw = self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        return df

    def fetch_ticker(self, symbol: str) -> dict:
        return self._exchange.fetch_ticker(symbol)

    def fetch_order_book(self, symbol: str, limit: int = 20) -> dict:
        return self._exchange.fetch_order_book(symbol, limit=limit)

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    # Kraken uses legacy currency codes internally; map to standard names
    _CURRENCY_ALIASES: dict[str, list[str]] = {
        "USD":  ["USD", "ZUSD"],
        "USDT": ["USDT", "ZUSDT", "USD", "ZUSD"],  # treat USD and USDT as interchangeable cash
        "BTC":  ["BTC", "XBT", "XXBT"],
        "ETH":  ["ETH", "XETH"],
        "SOL":  ["SOL"],
    }

    def fetch_balance(self) -> dict:
        """Return free (available) balances as {currency: amount}."""
        if self._dry_run:
            return {"USDT": 10000.0, "BTC": 0.0, "ETH": 0.0}
        raw = self._exchange.fetch_balance()
        return {k: float(v) for k, v in raw["free"].items() if v and float(v) > 0}

    def get_free_balance(self, currency: str) -> float:
        balance = self.fetch_balance()
        total = sum(
            float(balance[alias])
            for alias in self._CURRENCY_ALIASES.get(currency, [currency])
            if alias in balance
        )
        if total == 0:
            logger.debug("Balance keys available: %s", list(balance.keys()))
        return total

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def place_market_buy(self, symbol: str, amount: float) -> Optional[dict]:
        """Place a market buy order for `amount` units of base currency."""
        if self._dry_run:
            order = self._simulated_order(symbol, "buy", "market", amount)
            logger.info("[DRY-RUN] Market BUY %s | amount=%.6f", symbol, amount)
            return order
        try:
            order = self._exchange.create_order(symbol, "market", "buy", amount)
            logger.info("Market BUY placed | %s | amount=%.6f | id=%s", symbol, amount, order["id"])
            return order
        except ccxt.BaseError as exc:
            logger.error("Failed to place market buy for %s: %s", symbol, exc)
            return None

    def place_market_sell(self, symbol: str, amount: float) -> Optional[dict]:
        """Place a market sell order for `amount` units of base currency."""
        if self._dry_run:
            order = self._simulated_order(symbol, "sell", "market", amount)
            logger.info("[DRY-RUN] Market SELL %s | amount=%.6f", symbol, amount)
            return order
        try:
            order = self._exchange.create_order(symbol, "market", "sell", amount)
            logger.info("Market SELL placed | %s | amount=%.6f | id=%s", symbol, amount, order["id"])
            return order
        except ccxt.BaseError as exc:
            logger.error("Failed to place market sell for %s: %s", symbol, exc)
            return None

    def place_stop_loss(self, symbol: str, side: str, amount: float, stop_price: float) -> Optional[dict]:
        """
        Place a stop-loss order.
        side: 'sell' to protect a long, 'buy' to protect a short.
        Kraken uses order type 'stop-loss'.
        """
        if self._dry_run:
            order = self._simulated_order(symbol, side, "stop-loss", amount, price=stop_price)
            logger.info(
                "[DRY-RUN] Stop-loss %s %s | amount=%.6f | trigger=%.4f",
                side.upper(), symbol, amount, stop_price,
            )
            return order
        try:
            params = {"stopLossPrice": stop_price}
            order = self._exchange.create_order(symbol, "stop-loss", side, amount, stop_price, params)
            logger.info(
                "Stop-loss placed | %s %s | amount=%.6f | trigger=%.4f | id=%s",
                side.upper(), symbol, amount, stop_price, order["id"],
            )
            return order
        except ccxt.BaseError as exc:
            logger.error("Failed to place stop-loss for %s: %s", symbol, exc)
            return None

    def place_limit_sell(self, symbol: str, amount: float, price: float) -> Optional[dict]:
        """Place a limit sell (take-profit) order."""
        if self._dry_run:
            order = self._simulated_order(symbol, "sell", "limit", amount, price=price)
            logger.info("[DRY-RUN] Limit SELL %s | amount=%.6f | price=%.4f", symbol, amount, price)
            return order
        try:
            order = self._exchange.create_order(symbol, "limit", "sell", amount, price)
            logger.info(
                "Limit SELL placed | %s | amount=%.6f | price=%.4f | id=%s",
                symbol, amount, price, order["id"],
            )
            return order
        except ccxt.BaseError as exc:
            logger.error("Failed to place limit sell for %s: %s", symbol, exc)
            return None

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        if self._dry_run:
            logger.info("[DRY-RUN] Cancel order %s for %s", order_id, symbol)
            return True
        try:
            self._exchange.cancel_order(order_id, symbol)
            return True
        except ccxt.BaseError as exc:
            logger.error("Failed to cancel order %s: %s", order_id, exc)
            return False

    def fetch_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        if self._dry_run:
            return []
        try:
            return self._exchange.fetch_open_orders(symbol)
        except ccxt.BaseError as exc:
            logger.error("Failed to fetch open orders: %s", exc)
            return []

    def fetch_recent_buy_price(self, symbol: str, hours: int = 48) -> Optional[float]:
        """Return the fill price of the most recent buy trade for a symbol within the last N hours."""
        if self._dry_run:
            return None
        try:
            since = int((time.time() - hours * 3600) * 1000)
            trades = self._exchange.fetch_my_trades(symbol, since=since)
            buys = [t for t in trades if t.get("side") == "buy"]
            if not buys:
                return None
            latest = max(buys, key=lambda t: t.get("timestamp", 0))
            price = float(latest.get("price", 0))
            return price if price > 0 else None
        except ccxt.BaseError as exc:
            logger.warning("Could not fetch recent trades for %s: %s", symbol, exc)
            return None

    @staticmethod
    def _get_stop_price(order: dict) -> Optional[float]:
        """Extract the trigger/stop price from a ccxt unified order dict."""
        for key in ("stopPrice", "triggerPrice", "stopLossPrice"):
            val = order.get(key)
            if val:
                return float(val)
        info = order.get("info", {})
        for key in ("stopprice", "price"):
            val = info.get(key)
            if val:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _simulated_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: Optional[float] = None,
    ) -> dict:
        ticker = {}
        try:
            ticker = self.fetch_ticker(symbol)
        except Exception:
            pass
        fill_price = price or ticker.get("last", 0.0)
        return {
            "id": f"DRY-{int(time.time() * 1000)}",
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "amount": amount,
            "price": fill_price,
            "cost": amount * fill_price,
            "status": "closed",
            "timestamp": int(time.time() * 1000),
        }

    def get_min_order_amount(self, symbol: str) -> float:
        """Return the minimum order size for a symbol."""
        markets = self._exchange.load_markets()
        market = markets.get(symbol, {})
        return float(market.get("limits", {}).get("amount", {}).get("min", 0.0001))

    def round_amount(self, symbol: str, amount: float) -> float:
        """Round amount to the exchange's required precision."""
        try:
            return float(self._exchange.amount_to_precision(symbol, amount))
        except Exception:
            return round(amount, 6)

    def round_price(self, symbol: str, price: float) -> float:
        """Round price to the exchange's required precision."""
        try:
            return float(self._exchange.price_to_precision(symbol, price))
        except Exception:
            return round(price, 2)
