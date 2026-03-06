"""
Risk Manager
Responsibilities:
  - Calculate position size from % risk and stop-loss distance
  - Gate trade execution when daily loss limit is reached
  - Track realised P&L, fees, and open position count across the session
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from config import Config
from strategies.base_strategy import TradeSignal, Signal

logger = logging.getLogger(__name__)


@dataclass
class ClosedTrade:
    symbol: str
    strategy: str
    side: str
    entry_price: float
    exit_price: float
    amount: float
    pnl_usd: float
    fees_usd: float
    net_pnl_usd: float


@dataclass
class SessionStats:
    date: date = field(default_factory=date.today)
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    gross_pnl: float = 0.0
    total_fees: float = 0.0
    net_pnl: float = 0.0
    closed_trades: list[ClosedTrade] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades * 100

    def record(self, trade: ClosedTrade) -> None:
        self.total_trades += 1
        self.gross_pnl += trade.pnl_usd
        self.total_fees += trade.fees_usd
        self.net_pnl += trade.net_pnl_usd
        self.closed_trades.append(trade)
        if trade.net_pnl_usd > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1


class RiskManager:
    def __init__(self, starting_balance_usd: float) -> None:
        self._starting_balance = starting_balance_usd
        self._current_balance = starting_balance_usd
        self._stats = SessionStats()
        self._open_positions: dict[str, dict] = {}  # symbol -> position info

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calculate_position_size(
        self,
        signal: TradeSignal,
        available_balance_usd: float,
    ) -> Optional[float]:
        """
        Return the position size in base currency units, or None if trade should be skipped.
        Uses the fixed-% risk model:
          risk_usd = available_balance * RISK_PCT / 100
          stop_distance = entry - stop_loss
          size = risk_usd / stop_distance
        """
        if signal.signal != Signal.BUY:
            return None

        stop_distance = signal.entry_price - signal.stop_loss
        if stop_distance <= 0:
            logger.warning("Invalid stop-loss for %s: stop >= entry", signal.symbol)
            return None

        risk_usd = available_balance_usd * (Config.RISK_PER_TRADE_PCT / 100)
        size = risk_usd / stop_distance

        # Hard cap: never put more than MAX_POSITION_PCT of balance into one trade.
        # Reserve an extra 1% for fees so Kraken never rejects with insufficient funds.
        max_position_value = available_balance_usd * (Config.MAX_POSITION_PCT / 100) * 0.99
        position_cost = size * signal.entry_price
        if position_cost > max_position_value:
            size = max_position_value / signal.entry_price
            logger.info(
                "Position size capped at %.6f %s (MAX_POSITION_PCT=%.0f%%)",
                size, signal.symbol, Config.MAX_POSITION_PCT,
            )

        return size

    def estimate_fees(self, trade_value_usd: float, is_maker: bool = False) -> float:
        fee_pct = Config.MAKER_FEE_PCT if is_maker else Config.TAKER_FEE_PCT
        return trade_value_usd * (fee_pct / 100)

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------

    def can_trade(self) -> bool:
        """Return False when the daily loss limit has been hit or positions are maxed out."""
        daily_loss_pct = (self._stats.net_pnl / self._starting_balance) * 100
        if daily_loss_pct <= -Config.MAX_DAILY_LOSS_PCT:
            logger.warning(
                "Daily loss limit reached (%.2f%%). Trading halted for today.",
                abs(daily_loss_pct),
            )
            return False
        if len(self._open_positions) >= Config.MAX_OPEN_POSITIONS:
            logger.info("Max open positions (%d) reached.", Config.MAX_OPEN_POSITIONS)
            return False
        return True

    def has_open_position(self, symbol: str) -> bool:
        return symbol in self._open_positions

    def is_position_expired(self, symbol: str) -> bool:
        """Return True if the position has been open longer than MAX_POSITION_HOURS."""
        pos = self._open_positions.get(symbol)
        if not pos:
            return False
        age_hours = (datetime.now(timezone.utc) - pos["opened_at"]).total_seconds() / 3600
        return age_hours >= Config.MAX_POSITION_HOURS

    # ------------------------------------------------------------------
    # Position tracking
    # ------------------------------------------------------------------

    def open_position(
        self,
        symbol: str,
        strategy: str,
        entry_price: float,
        amount: float,
        stop_loss: float,
        take_profit: float,
        order_id: str,
    ) -> None:
        self._open_positions[symbol] = {
            "strategy": strategy,
            "entry_price": entry_price,
            "amount": amount,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "order_id": order_id,
            "opened_at": datetime.now(timezone.utc),
            "highest_price": entry_price,
            "trail_active": False,
        }
        logger.info(
            "Position opened | %s | entry=%.4f | size=%.6f | SL=%.4f | TP=%.4f",
            symbol, entry_price, amount, stop_loss, take_profit,
        )

    def close_position(self, symbol: str, exit_price: float) -> Optional[ClosedTrade]:
        pos = self._open_positions.pop(symbol, None)
        if pos is None:
            logger.warning("close_position called but no open position found for %s", symbol)
            return None

        pnl = (exit_price - pos["entry_price"]) * pos["amount"]
        entry_value = pos["entry_price"] * pos["amount"]
        exit_value = exit_price * pos["amount"]
        fees = self.estimate_fees(entry_value) + self.estimate_fees(exit_value)
        net_pnl = pnl - fees

        self._current_balance += net_pnl
        trade = ClosedTrade(
            symbol=symbol,
            strategy=pos["strategy"],
            side="buy",
            entry_price=pos["entry_price"],
            exit_price=exit_price,
            amount=pos["amount"],
            pnl_usd=pnl,
            fees_usd=fees,
            net_pnl_usd=net_pnl,
        )
        self._stats.record(trade)
        logger.info(
            "Position closed | %s | exit=%.4f | PnL=%.2f USD | Net=%.2f USD",
            symbol, exit_price, pnl, net_pnl,
        )
        return trade

    def update_trail(self, symbol: str, current_price: float) -> Optional[float]:
        """
        Update the trailing stop for a position.
        Returns the new stop-loss price if it moved up, otherwise None.

        Logic:
          - Activation: price has moved at least TRAIL_ACTIVATION_PCT% of the way from entry to TP
          - Once active, trail = highest_price * (1 - TRAIL_DISTANCE_PCT / 100)
          - Stop only ratchets upward — never moves down
        """
        pos = self._open_positions.get(symbol)
        if pos is None:
            return None

        entry = pos["entry_price"]
        take_profit = pos["take_profit"]
        total_move = take_profit - entry
        if total_move <= 0:
            return None

        progress_pct = (current_price - entry) / total_move * 100

        # Check activation threshold
        if not pos["trail_active"]:
            if progress_pct < Config.TRAIL_ACTIVATION_PCT:
                return None
            pos["trail_active"] = True
            logger.info(
                "Trailing stop activated for %s at %.4f (%.1f%% toward TP)",
                symbol, current_price, progress_pct,
            )

        # Update highest price seen
        if current_price > pos["highest_price"]:
            pos["highest_price"] = current_price

        new_stop = pos["highest_price"] * (1 - Config.TRAIL_DISTANCE_PCT / 100)

        # Only move stop upward, and only if it moved enough to warrant a new exchange order
        min_step = pos["stop_loss"] * (Config.TRAIL_MIN_STEP_PCT / 100)
        if new_stop - pos["stop_loss"] < min_step:
            return None

        old_stop = pos["stop_loss"]
        pos["stop_loss"] = new_stop
        logger.info(
            "Trailing stop updated for %s: %.4f -> %.4f (highest=%.4f)",
            symbol, old_stop, new_stop, pos["highest_price"],
        )
        return new_stop

    def get_open_positions(self) -> dict:
        return dict(self._open_positions)

    def get_session_stats(self) -> SessionStats:
        return self._stats

    def update_balance(self, new_balance: float) -> None:
        self._current_balance = new_balance
