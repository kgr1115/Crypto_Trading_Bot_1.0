"""
Day Trading Bot — Main Orchestrator
Kraken spot (non-margin) | BTC/USD, ETH/USD
Strategies: Scalping, Breakout, Range Trading, MA Trend
Reports sent to Telegram.

Usage:
    python bot.py

Environment:
    Copy .env.example to .env and fill in your credentials.
    Set DRY_RUN=false only when you are ready for live trading.
"""
import logging
import signal
import sys
import time
from datetime import datetime, date, timezone
from typing import Optional

from config import Config
from exchange.kraken_client import KrakenClient
from risk.risk_manager import RiskManager
from strategies.base_strategy import BaseStrategy, Signal, TradeSignal
from strategies.scalping import ScalpingStrategy
from strategies.breakout import BreakoutStrategy
from strategies.range_trading import RangeTradingStrategy
from strategies.ma_trend import MATrendStrategy
from telegram.reporter import TelegramReporter

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("bot")


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

def build_strategies() -> list[BaseStrategy]:
    strategies: list[BaseStrategy] = []
    if Config.STRATEGY_SCALPING:
        strategies.append(ScalpingStrategy())
    if Config.STRATEGY_BREAKOUT:
        strategies.append(BreakoutStrategy())
    if Config.STRATEGY_RANGE_TRADING:
        strategies.append(RangeTradingStrategy())
    if Config.STRATEGY_MA_TREND:
        strategies.append(MATrendStrategy())
    return strategies


# ---------------------------------------------------------------------------
# Main bot class
# ---------------------------------------------------------------------------

class DayTradingBot:
    def __init__(self) -> None:
        Config.validate()
        self._client = KrakenClient()
        self._reporter = TelegramReporter()
        self._strategies = build_strategies()
        self._running = False

        # Initialise balance from exchange (or simulated in dry-run)
        usd_balance = self._client.get_free_balance("USDT")
        self._risk = RiskManager(starting_balance_usd=usd_balance)

        # Track which candle timestamps we have already acted on per strategy+symbol
        # to avoid double-entering on the same bar
        self._last_signal_bar: dict[str, str] = {}

        # Track active stop-loss order IDs so we can cancel them when closing
        self._sl_orders: dict[str, str] = {}   # symbol -> stop-loss order_id
        self._tp_orders: dict[str, str] = {}   # symbol -> take-profit order_id

        # Daily summary trigger
        self._last_summary_date: Optional[date] = None

        logger.info("Bot initialised | pairs=%s | strategies=%s | dry_run=%s",
                    Config.TRADING_PAIRS,
                    [s.name for s in self._strategies],
                    Config.DRY_RUN)

        # Restore any positions that were open before this restart
        self._reconcile_positions()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._running = True
        strategy_names = [s.name for s in self._strategies]
        self._reporter.send_startup(Config.DRY_RUN, Config.TRADING_PAIRS, strategy_names)
        logger.info("Bot started. Poll interval: %ds", Config.POLL_INTERVAL)

        while self._running:
            try:
                self._tick()
                self._maybe_send_daily_summary()
            except KeyboardInterrupt:
                self.stop("User interrupt (Ctrl+C)")
                break
            except Exception as exc:
                logger.exception("Unhandled error in main loop: %s", exc)
                self._reporter.send_error("main loop", str(exc))

            time.sleep(Config.POLL_INTERVAL)

    def stop(self, reason: str = "Shutdown requested") -> None:
        self._running = False
        logger.info("Stopping bot: %s", reason)
        # Send final daily summary
        stats = self._risk.get_session_stats()
        balance = self._client.get_free_balance("USDT")
        self._reporter.send_daily_summary(stats, balance)
        self._reporter.send_halt(reason)

    # ------------------------------------------------------------------
    # Core tick
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """Called once per poll interval. Checks signals and manages positions."""
        # 1. Check exit conditions for open positions
        self._manage_open_positions()

        # 2. If we can still trade, look for new entries
        # Evaluate one symbol at a time — stop as soon as a trade is placed
        # to avoid multiple strategies firing on the same tick and exhausting funds
        if self._risk.can_trade():
            for symbol in Config.TRADING_PAIRS:
                if self._risk.has_open_position(symbol):
                    continue
                for strategy in self._strategies:
                    if not self._risk.can_trade():
                        break
                    traded = self._evaluate_strategy(strategy, symbol)
                    if traded:
                        break  # one entry per symbol per tick

    def _evaluate_strategy(self, strategy: BaseStrategy, symbol: str) -> bool:
        """Fetch data, run strategy, and act on any BUY signal. Returns True if a trade was placed."""
        try:
            df = self._client.fetch_ohlcv(symbol, strategy.timeframe, limit=300)
        except Exception as exc:
            logger.error("Failed to fetch OHLCV for %s/%s: %s", symbol, strategy.timeframe, exc)
            return False

        signal = strategy.analyze(df, symbol)
        if signal.signal == Signal.HOLD:
            return False

        # De-duplicate: only act once per candle (using last candle timestamp as key)
        bar_key = f"{strategy.name}:{symbol}:{df.index[-2]}"
        if self._last_signal_bar.get(bar_key) == bar_key:
            return False
        self._last_signal_bar[bar_key] = bar_key

        # Minimum R/R gate — skip poor-quality setups regardless of strategy
        rr = signal.risk_reward
        if rr is None or rr < Config.MIN_RISK_REWARD:
            logger.info(
                "Skipping %s/%s — R/R %.2f below minimum %.1f",
                symbol, strategy.name, rr or 0.0, Config.MIN_RISK_REWARD,
            )
            return False

        logger.info(
            "Signal | %s | %s | %s | entry=%.4f SL=%.4f TP=%.4f R/R=%.2f",
            signal.signal.value, symbol, strategy.name,
            signal.entry_price, signal.stop_loss, signal.take_profit, rr,
        )
        self._reporter.send_signal(signal)

        if signal.signal == Signal.BUY:
            return self._execute_buy(signal)
        return False

    def _execute_buy(self, signal: TradeSignal) -> bool:
        """Size and place a buy order, then set stop-loss and take-profit. Returns True on success."""
        usd_balance = self._client.get_free_balance("USDT")
        size = self._risk.calculate_position_size(signal, usd_balance)
        if size is None or size <= 0:
            logger.warning("Skipping trade — invalid position size for %s", signal.symbol)
            return False

        size = self._client.round_amount(signal.symbol, size)
        min_amount = self._client.get_min_order_amount(signal.symbol)
        if size < min_amount:
            logger.warning(
                "Skipping trade — size %.8f below minimum %.8f for %s",
                size, min_amount, signal.symbol,
            )
            return False

        # Place market entry
        order = self._client.place_market_buy(signal.symbol, size)
        if order is None:
            logger.error("Entry order failed for %s", signal.symbol)
            return False

        entry_price = float(order.get("price") or signal.entry_price)
        order_id = str(order.get("id", "unknown"))

        # Wait for the market buy to settle before placing exit orders
        time.sleep(4)

        # Use the actual received balance — may be slightly less than ordered due to fees/rounding
        base_currency = signal.symbol.split("/")[0]
        actual_balance = self._client.get_free_balance(base_currency) if not Config.DRY_RUN else size
        sell_size = self._client.round_amount(signal.symbol, min(size, actual_balance * 0.999))
        if sell_size <= 0:
            sell_size = size  # fallback to intended size if balance check fails

        # Place stop-loss order
        sl_price = self._client.round_price(signal.symbol, signal.stop_loss)
        sl_order = self._client.place_stop_loss(signal.symbol, "sell", sell_size, sl_price)
        if sl_order:
            self._sl_orders[signal.symbol] = str(sl_order.get("id", ""))

        # Place limit take-profit order
        tp_price = self._client.round_price(signal.symbol, signal.take_profit)
        tp_order = self._client.place_limit_sell(signal.symbol, sell_size, tp_price)
        if tp_order:
            self._tp_orders[signal.symbol] = str(tp_order.get("id", ""))

        # Register with risk manager
        self._risk.open_position(
            symbol=signal.symbol,
            strategy=signal.strategy,
            entry_price=entry_price,
            amount=sell_size,
            stop_loss=sl_price,
            take_profit=tp_price,
            order_id=order_id,
        )

        self._reporter.send_order_opened(
            symbol=signal.symbol,
            strategy=signal.strategy,
            entry_price=entry_price,
            amount=sell_size,
            stop_loss=sl_price,
            take_profit=tp_price,
            order_id=order_id,
            dry_run=Config.DRY_RUN,
        )
        return True

    # ------------------------------------------------------------------
    # Startup reconciliation
    # ------------------------------------------------------------------

    def _reconcile_positions(self) -> None:
        """
        On startup, fetch open orders from Kraken and rebuild internal state
        for any positions that were already running before the restart.
        Prevents re-entering trades that are live on the exchange.
        """
        if Config.DRY_RUN:
            return

        logger.info("Reconciling open positions with Kraken...")
        open_orders = self._client.fetch_open_orders()
        if not open_orders:
            logger.info("No open orders found on Kraken — starting fresh.")
            return

        recovered_count = 0

        for symbol in Config.TRADING_PAIRS:
            if self._risk.has_open_position(symbol):
                continue

            # Collect all open sell orders for this symbol
            sell_orders = [
                o for o in open_orders
                if o.get("symbol") == symbol and (o.get("side") or "").lower() == "sell"
            ]
            if not sell_orders:
                continue

            # Identify the stop-loss and take-profit orders
            sl_order = next(
                (o for o in sell_orders if "stop" in (o.get("type") or "").lower()),
                None,
            )
            tp_order = next(
                (o for o in sell_orders if (o.get("type") or "").lower() == "limit"),
                None,
            )

            if sl_order is None:
                logger.warning(
                    "Found a limit sell for %s but no stop-loss order — skipping "
                    "(review manually; this position has no downside protection).",
                    symbol,
                )
                continue

            stop_price = self._client._get_stop_price(sl_order)
            if not stop_price:
                logger.warning("Could not determine stop price for %s — skipping recovery.", symbol)
                continue

            amount = float(sl_order.get("amount", 0))
            if amount <= 0:
                continue

            tp_price = float(tp_order["price"]) if tp_order else None

            # Recover entry price from trade history; fall back to current price
            entry_price = self._client.fetch_recent_buy_price(symbol)
            if not entry_price:
                ticker = self._client.fetch_ticker(symbol)
                entry_price = float(ticker.get("last", stop_price * 1.01))
                logger.warning(
                    "No recent buy found for %s — using current price %.4f as entry estimate.",
                    symbol, entry_price,
                )

            # If no TP order exists, set a placeholder TP 2% above entry
            if tp_price is None:
                tp_price = round(entry_price * 1.02, 8)
                logger.warning("No TP order found for %s — setting placeholder TP at %.4f.", symbol, tp_price)

            self._risk.open_position(
                symbol=symbol,
                strategy="recovered",
                entry_price=entry_price,
                amount=amount,
                stop_loss=stop_price,
                take_profit=tp_price,
                order_id=sl_order.get("id", "recovered"),
            )
            self._sl_orders[symbol] = str(sl_order.get("id", ""))
            if tp_order:
                self._tp_orders[symbol] = str(tp_order.get("id", ""))

            recovered_count += 1
            logger.info(
                "Recovered position | %s | amount=%.6f | entry≈%.4f | SL=%.4f | TP=%.4f",
                symbol, amount, entry_price, stop_price, tp_price,
            )
            self._reporter.send_error(
                "restart recovery",
                f"Recovered {symbol} position: amount={amount:.6f} entry≈{entry_price:.4f} "
                f"SL={stop_price:.4f} TP={tp_price:.4f}",
            )

        if recovered_count == 0:
            logger.info("No positions to recover.")
        else:
            logger.info("Recovered %d position(s) from previous session.", recovered_count)

    # ------------------------------------------------------------------
    # Position management (check if SL/TP were hit)
    # ------------------------------------------------------------------

    def _manage_open_positions(self) -> None:
        """
        Check each open position against current price to detect
        stop-loss or take-profit hits (important in dry-run mode where
        the exchange won't auto-close; in live mode Kraken handles it,
        but we still need to update our internal state).
        """
        positions = self._risk.get_open_positions()
        for symbol, pos in list(positions.items()):
            try:
                ticker = self._client.fetch_ticker(symbol)
                current_price = float(ticker.get("last", 0))
            except Exception as exc:
                logger.error("Failed to fetch ticker for %s: %s", symbol, exc)
                continue

            if current_price <= 0:
                continue

            # Update trailing stop — may raise the stop-loss level
            new_trail_stop = self._risk.update_trail(symbol, current_price)
            if new_trail_stop is not None and not Config.DRY_RUN:
                # Cancel old SL order and place a new one at the updated level
                old_sl_id = self._sl_orders.pop(symbol, None)
                if old_sl_id:
                    self._client.cancel_order(old_sl_id, symbol)
                rounded_trail = self._client.round_price(symbol, new_trail_stop)
                new_sl_order = self._client.place_stop_loss(symbol, "sell", pos["amount"], rounded_trail)
                if new_sl_order:
                    self._sl_orders[symbol] = str(new_sl_order.get("id", ""))
                    self._reporter.send_trail_updated(symbol, new_trail_stop)
                else:
                    # SL placement failed — Kraken may have already filled the original SL
                    # (i.e. price dipped, exchange sold, price recovered, bot missed the close)
                    base_currency = symbol.split("/")[0]
                    owned = self._client.get_free_balance(base_currency)
                    if owned < pos["amount"] * 0.5:
                        logger.warning(
                            "Trail SL failed and %s balance (%.6f) < position size (%.6f) — "
                            "position likely already closed by exchange; reconciling internally",
                            base_currency, owned, pos["amount"],
                        )
                        trade = self._risk.close_position(symbol, new_trail_stop)
                        if trade:
                            self._reporter.send_order_closed(trade, Config.DRY_RUN)
                        continue  # skip the sl_hit/tp_hit check below

            sl_hit = current_price <= pos["stop_loss"]
            tp_hit = current_price >= pos["take_profit"]
            time_hit = self._risk.is_position_expired(symbol)

            if sl_hit or tp_hit or time_hit:
                if time_hit and not sl_hit and not tp_hit:
                    exit_reason = "TIME-LIMIT"
                    exit_price = current_price
                    logger.info(
                        "TIME-LIMIT exit for %s after %.1fh at %.4f",
                        symbol, Config.MAX_POSITION_HOURS, current_price,
                    )
                else:
                    exit_reason = "STOP-LOSS" if sl_hit else "TAKE-PROFIT"
                    exit_price = pos["stop_loss"] if sl_hit else pos["take_profit"]
                    logger.info("%s hit for %s at %.4f", exit_reason, symbol, exit_price)

                if not Config.DRY_RUN:
                    if time_hit and not sl_hit and not tp_hit:
                        # Time-limit exit: cancel both open orders, then market sell
                        for order_id in [self._sl_orders.pop(symbol, None), self._tp_orders.pop(symbol, None)]:
                            if order_id:
                                self._client.cancel_order(order_id, symbol)
                        self._client.place_market_sell(symbol, pos["amount"])
                    else:
                        # Kraken already filled the SL or TP order; cancel the other one
                        opposing_key = self._tp_orders if sl_hit else self._sl_orders
                        opposing_id = opposing_key.pop(symbol, None)
                        if opposing_id:
                            self._client.cancel_order(opposing_id, symbol)
                else:
                    self._sl_orders.pop(symbol, None)
                    self._tp_orders.pop(symbol, None)

                trade = self._risk.close_position(symbol, exit_price)
                if trade:
                    self._reporter.send_order_closed(trade, Config.DRY_RUN)

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    def _maybe_send_daily_summary(self) -> None:
        now = datetime.now(timezone.utc)
        today = now.date()  # use UTC date to match the UTC hour check
        # Send summary once per day at ~17:00 UTC
        if self._last_summary_date != today and now.hour >= 17:
            stats = self._risk.get_session_stats()
            balance = self._client.get_free_balance("USDT")
            self._reporter.send_daily_summary(stats, balance)
            self._last_summary_date = today
            logger.info("Daily summary sent.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _handle_sigterm(signum, frame):  # noqa: ANN001
    logger.info("SIGTERM received — shutting down gracefully.")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_sigterm)
    bot = DayTradingBot()
    try:
        bot.start()
    except SystemExit:
        bot.stop("SIGTERM")
