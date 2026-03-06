"""
Breakout Trading Strategy — 5-minute chart
Entry conditions (BUY):
  - Price has been in a compression / squeeze (low ATR relative to recent range)
  - Current candle closes above the compression zone high
  - Volume is 2x above 20-period average (confirms genuine breakout, not fakeout)

Stop-loss: just below the compression zone low
Take-profit: entry + (compression range height * 1.5)  — projects the range upward
"""
import pandas as pd

from config import Config
from strategies.base_strategy import BaseStrategy, Signal, TradeSignal
from utils.indicators import is_volume_spike, is_compressed, compression_range


class BreakoutStrategy(BaseStrategy):
    name = "breakout"
    timeframe = Config.BREAKOUT_TIMEFRAME

    def analyze(self, df: pd.DataFrame, symbol: str) -> TradeSignal:
        min_bars = Config.BREAKOUT_COMPRESSION_BARS + Config.BREAKOUT_ATR_PERIOD + 5
        if len(df) < min_bars:
            return self._hold(symbol, float(df["close"].iloc[-1]))

        current_price = float(df["close"].iloc[-1])

        # Use data up to (not including) the current live candle
        historical = df.iloc[:-1]

        compressed = is_compressed(
            historical,
            period=Config.BREAKOUT_COMPRESSION_BARS,
            ratio=Config.BREAKOUT_ATR_COMPRESSION_RATIO,
        )
        if not compressed:
            return self._hold(symbol, current_price)

        zone_low, zone_high = compression_range(historical, period=Config.BREAKOUT_COMPRESSION_BARS)
        zone_height = zone_high - zone_low

        vol_spike = is_volume_spike(df, Config.BREAKOUT_VOLUME_MULTIPLIER)
        current_vol_spike = bool(vol_spike.iloc[-1])

        # Breakout candle must have a solid body (not just a wick poking above the zone)
        candle = df.iloc[-1]
        candle_range = float(candle["high"]) - float(candle["low"])
        candle_body = abs(float(candle["close"]) - float(candle["open"]))
        strong_body = candle_range > 0 and (candle_body / candle_range) >= Config.BREAKOUT_MIN_BODY_RATIO

        # Breakout: current close exceeds the compression zone high, strong volume, solid candle
        breakout_up = current_price > zone_high and current_vol_spike and strong_body

        if breakout_up:
            stop_loss = round(zone_low * 0.999, 8)          # Slightly below compression low
            take_profit = round(current_price + zone_height * 1.5, 8)
            confidence = min(1.0, zone_height / (zone_high * 0.01))  # bigger range = higher conf
            return TradeSignal(
                signal=Signal.BUY,
                symbol=symbol,
                strategy=self.name,
                timeframe=self.timeframe,
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=round(min(confidence, 1.0), 2),
                notes=(
                    f"Breakout above {zone_high:.4f} | "
                    f"Zone [{zone_low:.4f}\u2013{zone_high:.4f}] | "
                    f"Body={candle_body/candle_range:.0%} | Vol spike"
                ),
            )

        return self._hold(symbol, current_price)
