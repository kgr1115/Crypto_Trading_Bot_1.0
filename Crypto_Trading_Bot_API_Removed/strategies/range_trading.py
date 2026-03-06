"""
Range Trading / Support-Resistance Reversal Strategy — 15-minute chart
Entry conditions (BUY):
  - Price is within RANGE_TOUCH_ZONE_PCT % of an established support level
  - That support level has been touched at least RANGE_MIN_TOUCHES times
  - RSI is below 45 (price is not overbought near support) — optional confirmation

Stop-loss: just below the support level (support_price * (1 - 0.002))
Take-profit: nearest resistance level above entry (or entry + 1.5 * risk if no resistance found)
"""
import pandas as pd

from config import Config
from strategies.base_strategy import BaseStrategy, Signal, TradeSignal
from utils.indicators import rsi, find_support_resistance, volume_sma


class RangeTradingStrategy(BaseStrategy):
    name = "range_trading"
    timeframe = Config.RANGE_TIMEFRAME

    def analyze(self, df: pd.DataFrame, symbol: str) -> TradeSignal:
        min_bars = Config.RANGE_LOOKBACK + 20
        if len(df) < min_bars:
            return self._hold(symbol, float(df["close"].iloc[-1]))

        current_price = float(df["close"].iloc[-1])
        close = df["close"]

        supports, resistances = find_support_resistance(
            df,
            lookback=Config.RANGE_LOOKBACK,
            min_touches=Config.RANGE_MIN_TOUCHES,
            zone_pct=Config.RANGE_TOUCH_ZONE_PCT,
        )

        if not supports:
            return self._hold(symbol, current_price)

        # Find the nearest support below current price
        nearest_support = supports[0]  # already sorted descending (highest support first)
        distance_pct = abs(current_price - nearest_support) / current_price * 100

        at_support = distance_pct <= Config.RANGE_TOUCH_ZONE_PCT

        if not at_support:
            return self._hold(symbol, current_price)

        # RSI confirmation — must be below threshold (not mid-cycle)
        rsi_series = rsi(close, period=14)
        last_rsi = float(rsi_series.iloc[-2])
        if Config.RANGE_RSI_CONFIRM and last_rsi > Config.RANGE_RSI_THRESHOLD:
            return self._hold(symbol, current_price)

        # Volume confirmation — current bar volume must be at or above average
        vol_avg = volume_sma(df, period=20)
        current_volume = float(df["volume"].iloc[-2])
        avg_volume = float(vol_avg.iloc[-2])
        if avg_volume > 0 and current_volume < avg_volume * 0.8:
            return self._hold(symbol, current_price)

        # Stop-loss just below support
        stop_loss = round(nearest_support * 0.998, 8)
        risk = current_price - stop_loss

        # Take-profit at nearest resistance, or 1.5× risk if none found
        if resistances:
            take_profit = round(resistances[0], 8)
        else:
            take_profit = round(current_price + risk * 1.5, 8)

        return TradeSignal(
            signal=Signal.BUY,
            symbol=symbol,
            strategy=self.name,
            timeframe=self.timeframe,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=round(1.0 - distance_pct / Config.RANGE_TOUCH_ZONE_PCT, 2),
            notes=(
                f"Support={nearest_support:.4f} | "
                f"Distance={distance_pct:.3f}% | "
                f"RSI={last_rsi:.1f}"
            ),
        )
