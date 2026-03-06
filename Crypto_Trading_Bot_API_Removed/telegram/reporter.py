"""
Telegram Reporter
Sends one-way notifications to a Telegram chat via the Bot API.
Uses the requests library directly for simplicity — no async required.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from config import Config
from risk.risk_manager import SessionStats, ClosedTrade
from strategies.base_strategy import TradeSignal, Signal

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramReporter:
    def __init__(self) -> None:
        self._token = Config.TELEGRAM_BOT_TOKEN
        self._chat_id = Config.TELEGRAM_CHAT_ID
        self._enabled = bool(self._token and self._chat_id)
        if not self._enabled:
            logger.warning("Telegram reporter disabled — BOT_TOKEN or CHAT_ID not set.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_startup(self, dry_run: bool, pairs: list[str], strategies: list[str]) -> None:
        mode = "DRY-RUN (paper trading)" if dry_run else "LIVE TRADING"
        msg = (
            f"*Day Trading Bot Started*\n"
            f"Mode: `{mode}`\n"
            f"Pairs: `{', '.join(pairs)}`\n"
            f"Strategies: `{', '.join(strategies)}`\n"
            f"Started: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}`"
        )
        self._send(msg)

    def send_signal(self, signal: TradeSignal) -> None:
        if signal.signal == Signal.HOLD:
            return
        arrow = "BUY" if signal.signal == Signal.BUY else "SELL"
        rr = f"{signal.risk_reward:.2f}" if signal.risk_reward else "N/A"
        msg = (
            f"*{arrow} Signal — {signal.symbol}*\n"
            f"Strategy: `{signal.strategy}` ({signal.timeframe})\n"
            f"Entry:       `{signal.entry_price:.4f}`\n"
            f"Stop-Loss:   `{signal.stop_loss:.4f}`\n"
            f"Take-Profit: `{signal.take_profit:.4f}`\n"
            f"R/R:         `{rr}`\n"
            f"Confidence:  `{signal.confidence:.0%}`\n"
            f"Notes: _{signal.notes}_"
        )
        self._send(msg)

    def send_order_opened(
        self,
        symbol: str,
        strategy: str,
        entry_price: float,
        amount: float,
        stop_loss: float,
        take_profit: float,
        order_id: str,
        dry_run: bool,
    ) -> None:
        tag = "[DRY-RUN] " if dry_run else ""
        cost_usd = entry_price * amount
        msg = (
            f"*{tag}Position Opened — {symbol}*\n"
            f"Strategy: `{strategy}`\n"
            f"Entry:       `{entry_price:.4f}`\n"
            f"Amount:      `{amount:.6f}` (≈ `${cost_usd:.2f}`)\n"
            f"Stop-Loss:   `{stop_loss:.4f}`\n"
            f"Take-Profit: `{take_profit:.4f}`\n"
            f"Order ID: `{order_id}`"
        )
        self._send(msg)

    def send_order_closed(self, trade: ClosedTrade, dry_run: bool) -> None:
        tag = "[DRY-RUN] " if dry_run else ""
        direction = "+" if trade.net_pnl_usd >= 0 else ""
        outcome = "WIN" if trade.net_pnl_usd >= 0 else "LOSS"
        msg = (
            f"*{tag}Position Closed — {trade.symbol}* ({outcome})\n"
            f"Strategy: `{trade.strategy}`\n"
            f"Entry:   `{trade.entry_price:.4f}`\n"
            f"Exit:    `{trade.exit_price:.4f}`\n"
            f"Amount:  `{trade.amount:.6f}`\n"
            f"Gross PnL: `{direction}{trade.pnl_usd:.2f} USD`\n"
            f"Fees:      `-{trade.fees_usd:.2f} USD`\n"
            f"Net PnL:   `{direction}{trade.net_pnl_usd:.2f} USD`"
        )
        self._send(msg)

    def send_daily_summary(self, stats: SessionStats, current_balance: float) -> None:
        direction = "+" if stats.net_pnl >= 0 else ""
        msg = (
            f"*Daily Summary — {stats.date}*\n"
            f"Total Trades:  `{stats.total_trades}`\n"
            f"Wins / Losses: `{stats.winning_trades} / {stats.losing_trades}`\n"
            f"Win Rate:      `{stats.win_rate:.1f}%`\n"
            f"Gross PnL:     `{direction}{stats.gross_pnl:.2f} USD`\n"
            f"Total Fees:    `-{stats.total_fees:.2f} USD`\n"
            f"Net PnL:       `{direction}{stats.net_pnl:.2f} USD`\n"
            f"Balance:       `${current_balance:.2f}`"
        )
        self._send(msg)

    def send_trail_updated(self, symbol: str, new_stop: float) -> None:
        msg = f"*Trailing Stop Updated \u2014 {symbol}*\nNew Stop-Loss: `{new_stop:.4f}`"
        self._send(msg)

    def send_halt(self, reason: str) -> None:
        msg = f"*TRADING HALTED*\nReason: _{reason}_"
        self._send(msg)

    def send_error(self, context: str, error: str) -> None:
        msg = f"*Bot Error*\nContext: `{context}`\nError: `{error}`"
        self._send(msg)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send(self, text: str) -> bool:
        if not self._enabled:
            logger.debug("Telegram (disabled): %s", text)
            return False
        url = _BASE_URL.format(token=self._token)
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if not resp.ok:
                logger.error("Telegram API error %d: %s", resp.status_code, resp.text)
                return False
            return True
        except requests.RequestException as exc:
            logger.error("Telegram send failed: %s", exc)
            return False
