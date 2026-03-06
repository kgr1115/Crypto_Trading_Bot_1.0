"""
Scalping Strategy — 1-minute chart
Entry conditions (BUY):
  - RSI < oversold threshold (default 30)
  - Close at or below lower Bollinger Band
  - Volume above 1.5x 20-period average (avoids false signals in thin markets)

Exit / take-profit:
  - Target: middle Bollinger Band (mean-reversion)
  - Stop-loss: SCALP_STOP_PCT % below entry price
"""
import pandas as pd

from config import Config
from strategies.base_strategy import BaseStrategy, Signal, TradeSignal
from utils.indicators import rsi, bollinger_bands, is_volume_spike


class ScalpingStrategy(BaseStrategy):
    name = "scalping"
    timeframe = Config.SCALPING_TIMEFRAME

    def analyze(self, df: pd.DataFrame, symbol: str) -> TradeSignal:
        if len(df) < max(Config.SCALP_RSI_PERIOD, Config.SCALP_BB_PERIOD) + 5:
            return self._hold(symbol, float(df["close"].iloc[-1]))

        # Use the last confirmed candle (not the live, incomplete one)
        close = df["close"]
        current_price = float(close.iloc[-1])

        rsi_series = rsi(close, Config.SCALP_RSI_PERIOD)
        upper_bb, mid_bb, lower_bb = bollinger_bands(close, Config.SCALP_BB_PERIOD, Config.SCALP_BB_STD)
        vol_spike = is_volume_spike(df, Config.SCALP_VOLUME_MULTIPLIER)

        last_rsi = float(rsi_series.iloc[-2])
        prev_rsi = float(rsi_series.iloc[-3])   # one bar earlier
        last_close = float(close.iloc[-2])
        last_lower_bb = float(lower_bb.iloc[-2])
        last_mid_bb = float(mid_bb.iloc[-2])
        last_vol_spike = bool(vol_spike.iloc[-2])

        # RSI must be in or approaching oversold AND still falling (not already bouncing)
        rsi_declining = last_rsi < prev_rsi

        buy_signal = (
            last_rsi < Config.SCALP_RSI_OVERSOLD
            and last_close <= last_lower_bb
            and last_vol_spike
            and rsi_declining
        )

        if buy_signal:
            stop_loss = round(current_price * (1 - Config.SCALP_STOP_PCT / 100), 8)
            take_profit = round(last_mid_bb, 8)
            return TradeSignal(
                signal=Signal.BUY,
                symbol=symbol,
                strategy=self.name,
                timeframe=self.timeframe,
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=round((Config.SCALP_RSI_OVERSOLD - last_rsi) / Config.SCALP_RSI_OVERSOLD, 2),
                notes=f"RSI={last_rsi:.1f}↓ | Close={last_close:.4f} | LowerBB={last_lower_bb:.4f}",
            )

        return self._hold(symbol, current_price)
