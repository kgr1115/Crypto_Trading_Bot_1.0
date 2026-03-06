from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import pandas as pd


class Signal(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class TradeSignal:
    signal: Signal
    symbol: str
    strategy: str
    timeframe: str
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float = 1.0          # 0.0 – 1.0
    notes: str = ""

    @property
    def risk_reward(self) -> Optional[float]:
        if self.signal == Signal.BUY:
            risk = self.entry_price - self.stop_loss
            reward = self.take_profit - self.entry_price
        else:
            return None
        if risk <= 0:
            return None
        return round(reward / risk, 2)


class BaseStrategy(ABC):
    """
    All strategies must implement `analyze()`.
    They receive a fully-loaded OHLCV DataFrame and return a TradeSignal.
    """

    name: str = "base"
    timeframe: str = "1m"

    @abstractmethod
    def analyze(self, df: pd.DataFrame, symbol: str) -> TradeSignal:
        """
        Analyse the OHLCV DataFrame and produce a TradeSignal.
        The last row of df represents the most recent (incomplete) candle;
        strategies should reference df.iloc[-2] as the last confirmed candle.
        """
        ...

    def _hold(self, symbol: str, current_price: float) -> TradeSignal:
        return TradeSignal(
            signal=Signal.HOLD,
            symbol=symbol,
            strategy=self.name,
            timeframe=self.timeframe,
            entry_price=current_price,
            stop_loss=current_price,
            take_profit=current_price,
        )
