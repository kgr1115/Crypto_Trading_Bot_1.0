"""
Moving Average Trend-Following Strategy — 15-minute chart
Setup:
  - 50-period SMA (fast) and 200-period SMA (slow)
  - Trend is UP when fast MA > slow MA
  - Enter on pullbacks: price dips within MA_PULLBACK_PCT % of the fast MA while trend is up
  - Confirm with RSI not overbought (< 60)

Stop-loss: below the slow (200) MA or below the recent swing low, whichever is higher
Take-profit: entry + 2× risk
"""
import pandas as pd

from config import Config
from strategies.base_strategy import BaseStrategy, Signal, TradeSignal
from utils.indicators import sma, rsi, volume_sma


class MATrendStrategy(BaseStrategy):
    name = "ma_trend"
    timeframe = Config.MA_TREND_TIMEFRAME

    def analyze(self, df: pd.DataFrame, symbol: str) -> TradeSignal:
        min_bars = Config.MA_SLOW + 10
        if len(df) < min_bars:
            return self._hold(symbol, float(df["close"].iloc[-1]))

        close = df["close"]
        current_price = float(close.iloc[-1])

        fast_ma = sma(close, Config.MA_FAST)
        slow_ma = sma(close, Config.MA_SLOW)
        rsi_series = rsi(close, period=14)
        vol_avg = volume_sma(df, period=20)

        last_fast = float(fast_ma.iloc[-2])
        last_slow = float(slow_ma.iloc[-2])
        last_close = float(close.iloc[-2])
        last_rsi = float(rsi_series.iloc[-2])
        last_volume = float(df["volume"].iloc[-2])
        avg_volume = float(vol_avg.iloc[-2])

        uptrend = last_fast > last_slow

        if not uptrend:
            return self._hold(symbol, current_price)

        # Pullback condition: price is at or below the fast MA (true pullback, not extended above it)
        distance_from_fast = abs(last_close - last_fast) / last_fast * 100
        at_pullback = distance_from_fast <= Config.MA_PULLBACK_PCT and last_close <= last_fast

        # RSI must be below threshold — confirms price is not mid-rally
        rsi_ok = last_rsi < Config.MA_TREND_RSI_THRESHOLD

        # Volume must not be collapsing (at least 70% of average)
        volume_ok = avg_volume <= 0 or last_volume >= avg_volume * 0.7

        if not (at_pullback and rsi_ok and volume_ok):
            return self._hold(symbol, current_price)

        # Recent swing low for stop placement
        recent_low = float(df["low"].iloc[-20:].min())
        stop_below_slow_ma = float(slow_ma.iloc[-2]) * 0.998
        stop_loss = round(max(recent_low * 0.998, stop_below_slow_ma), 8)

        risk = current_price - stop_loss
        take_profit = round(current_price + risk * 2.0, 8)

        return TradeSignal(
            signal=Signal.BUY,
            symbol=symbol,
            strategy=self.name,
            timeframe=self.timeframe,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=round(1.0 - distance_from_fast / Config.MA_PULLBACK_PCT, 2),
            notes=(
                f"FastMA={last_fast:.4f} | SlowMA={last_slow:.4f} | "
                f"RSI={last_rsi:.1f} | Pullback={distance_from_fast:.3f}% | "
                f"Vol={last_volume/avg_volume:.1f}x avg"
            ),
        )
