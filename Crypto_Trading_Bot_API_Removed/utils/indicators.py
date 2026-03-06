"""
Technical indicator calculations used by all strategy modules.
All functions accept a pandas DataFrame with columns: open, high, low, close, volume.
They return Series or scalar values; they do NOT mutate the input DataFrame.
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Moving Averages
# ---------------------------------------------------------------------------

def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, middle, lower) bands."""
    middle = sma(series, period)
    std = series.rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


# ---------------------------------------------------------------------------
# Average True Range (ATR)
# ---------------------------------------------------------------------------

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


# ---------------------------------------------------------------------------
# Volume helpers
# ---------------------------------------------------------------------------

def volume_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    return df["volume"].rolling(window=period).mean()


def is_volume_spike(df: pd.DataFrame, multiplier: float = 1.5, period: int = 20) -> pd.Series:
    """Returns a boolean Series — True where volume is above multiplier * average."""
    avg = volume_sma(df, period)
    return df["volume"] > (avg * multiplier)


# ---------------------------------------------------------------------------
# Support & Resistance detection
# ---------------------------------------------------------------------------

def find_support_resistance(
    df: pd.DataFrame,
    lookback: int = 50,
    min_touches: int = 2,
    zone_pct: float = 0.3,
) -> tuple[list[float], list[float]]:
    """
    Identify horizontal support and resistance levels from recent price action.

    Returns:
        supports  — list of support prices (sorted descending, strongest first)
        resistances — list of resistance prices (sorted ascending, strongest first)
    """
    closes = df["close"].iloc[-lookback:]
    highs = df["high"].iloc[-lookback:]
    lows = df["low"].iloc[-lookback:]

    # Pivot highs / lows using a simple 3-bar window
    pivot_highs: list[float] = []
    pivot_lows: list[float] = []

    for i in range(1, len(highs) - 1):
        if highs.iloc[i] > highs.iloc[i - 1] and highs.iloc[i] > highs.iloc[i + 1]:
            pivot_highs.append(float(highs.iloc[i]))
        if lows.iloc[i] < lows.iloc[i - 1] and lows.iloc[i] < lows.iloc[i + 1]:
            pivot_lows.append(float(lows.iloc[i]))

    current_price = float(closes.iloc[-1])
    zone = current_price * (zone_pct / 100)

    def cluster_levels(levels: list[float]) -> list[float]:
        """Merge nearby pivots into a single level and keep those with min_touches."""
        if not levels:
            return []
        levels_sorted = sorted(levels)
        clusters: list[list[float]] = []
        current_cluster = [levels_sorted[0]]
        for lvl in levels_sorted[1:]:
            if lvl - current_cluster[-1] <= zone:
                current_cluster.append(lvl)
            else:
                clusters.append(current_cluster)
                current_cluster = [lvl]
        clusters.append(current_cluster)
        return [
            float(np.mean(c))
            for c in clusters
            if len(c) >= min_touches
        ]

    resistances = sorted(
        [lvl for lvl in cluster_levels(pivot_highs) if lvl > current_price]
    )
    supports = sorted(
        [lvl for lvl in cluster_levels(pivot_lows) if lvl < current_price],
        reverse=True,
    )
    return supports, resistances


# ---------------------------------------------------------------------------
# Compression / volatility squeeze detection
# ---------------------------------------------------------------------------

def is_compressed(df: pd.DataFrame, period: int = 20, ratio: float = 0.6) -> bool:
    """
    Return True when ATR is significantly below its recent maximum,
    indicating a period of price consolidation (pre-breakout squeeze).
    """
    atr_series = atr(df, period=14)
    if atr_series.isna().all():
        return False
    recent_atr = atr_series.iloc[-period:]
    current_atr = float(recent_atr.iloc[-1])
    max_atr = float(recent_atr.max())
    if max_atr == 0:
        return False
    return current_atr < (max_atr * ratio)


def compression_range(df: pd.DataFrame, period: int = 20) -> tuple[float, float]:
    """Return the (low, high) of the compression zone over `period` bars."""
    recent = df.iloc[-period:]
    return float(recent["low"].min()), float(recent["high"].max())
