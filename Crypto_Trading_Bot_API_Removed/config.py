import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Kraken API
    KRAKEN_API_KEY: str = os.getenv("KRAKEN_API_KEY", "")
    KRAKEN_API_SECRET: str = os.getenv("KRAKEN_API_SECRET", "")

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Trading pairs (Kraken spot)
    TRADING_PAIRS: list[str] = [
        p.strip() for p in os.getenv("TRADING_PAIRS", "BTC/USD,ETH/USD").split(",")
    ]

    # Risk management
    RISK_PER_TRADE_PCT: float = float(os.getenv("RISK_PER_TRADE_PCT", "2.0"))
    MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "20.0"))  # max % of balance per trade
    MAX_DAILY_LOSS_PCT: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "5.0"))
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "2"))
    MAX_POSITION_HOURS: float = float(os.getenv("MAX_POSITION_HOURS", "4.0"))  # force-close after N hours

    # Trailing stop-loss
    TRAIL_ACTIVATION_PCT: float = float(os.getenv("TRAIL_ACTIVATION_PCT", "50.0"))  # % toward TP before trail activates
    TRAIL_DISTANCE_PCT: float = float(os.getenv("TRAIL_DISTANCE_PCT", "0.8"))       # trail this % below highest price
    TRAIL_MIN_STEP_PCT: float = float(os.getenv("TRAIL_MIN_STEP_PCT", "0.25"))      # min % the trail must move before updating Kraken order

    # Dry run mode — no real orders placed
    DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() == "true"

    # Strategy toggles
    STRATEGY_SCALPING: bool = os.getenv("STRATEGY_SCALPING", "true").lower() == "true"
    STRATEGY_BREAKOUT: bool = os.getenv("STRATEGY_BREAKOUT", "true").lower() == "true"
    STRATEGY_RANGE_TRADING: bool = os.getenv("STRATEGY_RANGE_TRADING", "true").lower() == "true"
    STRATEGY_MA_TREND: bool = os.getenv("STRATEGY_MA_TREND", "true").lower() == "true"

    # Timeframes
    SCALPING_TIMEFRAME: str = "1m"
    BREAKOUT_TIMEFRAME: str = "5m"
    RANGE_TIMEFRAME: str = "15m"
    MA_TREND_TIMEFRAME: str = "15m"

    # Scalping strategy params
    SCALP_RSI_PERIOD: int = 14
    SCALP_RSI_OVERSOLD: float = 30.0
    SCALP_RSI_OVERBOUGHT: float = 70.0
    SCALP_BB_PERIOD: int = 20
    SCALP_BB_STD: float = 2.0
    SCALP_VOLUME_MULTIPLIER: float = 1.5    # Volume must be X times the average
    SCALP_STOP_PCT: float = 0.5            # Stop-loss 0.5% below entry

    # Breakout strategy params
    BREAKOUT_ATR_PERIOD: int = 14
    BREAKOUT_COMPRESSION_BARS: int = 20    # Look-back for compression detection
    BREAKOUT_VOLUME_MULTIPLIER: float = 2.0
    BREAKOUT_ATR_COMPRESSION_RATIO: float = 0.6  # ATR must be below X * max ATR
    BREAKOUT_MIN_BODY_RATIO: float = 0.4          # breakout candle body must be ≥ 40% of its range

    # Range trading (S/R reversal) params
    RANGE_LOOKBACK: int = 50               # Bars to find S/R levels
    RANGE_TOUCH_ZONE_PCT: float = 0.3      # % distance to S/R to qualify as "at level"
    RANGE_MIN_TOUCHES: int = 2             # Minimum touches to validate S/R level
    RANGE_RSI_CONFIRM: bool = True         # Require RSI confirmation

    # MA trend strategy params
    MA_FAST: int = 50
    MA_SLOW: int = 200
    MA_PULLBACK_PCT: float = 0.7           # % from fast MA to count as pullback (tighter = fewer, better setups)
    MA_TREND_RSI_THRESHOLD: float = 50.0   # RSI must be below this to enter (not mid-cycle)

    # Range trading RSI threshold
    RANGE_RSI_THRESHOLD: float = 45.0     # RSI must be below this to enter at support

    # Minimum risk/reward ratio — trades below this are skipped
    MIN_RISK_REWARD: float = float(os.getenv("MIN_RISK_REWARD", "1.5"))

    # Polling interval (seconds) — how often the main loop runs
    POLL_INTERVAL: int = 60

    # Kraken maker fee (Pro tier approximate)
    TAKER_FEE_PCT: float = 0.26
    MAKER_FEE_PCT: float = 0.16

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    @classmethod
    def validate(cls) -> None:
        errors = []
        if not cls.DRY_RUN:
            if not cls.KRAKEN_API_KEY:
                errors.append("KRAKEN_API_KEY is required for live trading")
            if not cls.KRAKEN_API_SECRET:
                errors.append("KRAKEN_API_SECRET is required for live trading")
        if not cls.TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN is required")
        if not cls.TELEGRAM_CHAT_ID:
            errors.append("TELEGRAM_CHAT_ID is required")
        if errors:
            raise ValueError("Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))
