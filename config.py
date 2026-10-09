"""
ETM Matrix Bias Bot Configuration
Symbols, Tolerances, Timeframes, and API Settings
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# Workspace Base Directory
BASE_DIR = Path(__file__).resolve().parent

# TwelveData and Telegram Credentials
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 31 Symbols specified: Forex majors/minors + Gold + Silver + BTC
SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "EURAUD", "EURCAD", "EURNZD", "EURCHF",
    "GBPJPY", "GBPAUD", "GBPCAD", "GBPNZD", "GBPCHF",
    "AUDJPY", "AUDCAD", "AUDNZD", "AUDCHF",
    "CADJPY", "CADCHF",
    "CHFJPY",
    "NZDJPY", "NZDCAD", "NZDCHF",
    "XAUUSD", "XAGUSD", "BTCUSD"
]

# Timeframes supported
TIMEFRAME_WEEKLY = "Weekly"
TIMEFRAME_DAILY = "Daily"
TIMEFRAME_H4 = "H4"

# TwelveData API Interval Mapping
INTERVAL_MAP = {
    TIMEFRAME_WEEKLY: "1week",
    TIMEFRAME_DAILY: "1day",
    TIMEFRAME_H4: "4h"
}

# TwelveData Rate Limiting & Safety
# Free plan limit: 8 calls/min, 800 calls/day
API_CALL_DELAY = float(os.getenv("API_CALL_DELAY", "1.2"))  # 1.2s delay as requested
API_RATE_LIMIT_PER_MIN = int(os.getenv("API_RATE_LIMIT_PER_MIN", "8"))
API_MAX_DAILY_CALLS = 800

# File Storage Paths
DATA_DIR = BASE_DIR / "data"
CHARTS_DIR = BASE_DIR / "charts"
LEVELS_FILE = DATA_DIR / "levels.json"
SWEPT_LEVELS_FILE = DATA_DIR / "swept_levels.json"
ALERTS_HISTORY_FILE = DATA_DIR / "alerts_history.json"

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
CHARTS_DIR.mkdir(parents=True, exist_ok=True)


def get_tolerance(symbol: str, price: float) -> float:
    """
    Calculate tolerance for level detection based on asset class.
    - Gold / Silver: 0.15
    - BTC: 15.0
    - Forex: 0.00015 * price
    """
    sym = symbol.upper().replace("/", "")
    if sym in ("XAUUSD", "XAGUSD"):
        return 0.15
    elif sym in ("BTCUSD", "BTC"):
        return 15.0
    else:
        # Forex pairs
        return 0.00015 * price


def format_twelvedata_symbol(symbol: str) -> str:
    """
    Format standard symbol (e.g. EURUSD, XAUUSD, BTCUSD) for TwelveData API.
    TwelveData standard format for currency pairs and commodities is 'BASE/QUOTE'.
    """
    clean_sym = symbol.strip().upper().replace("/", "")
    if len(clean_sym) == 6:
        return f"{clean_sym[:3]}/{clean_sym[3:]}"
    elif clean_sym == "BTCUSD":
        return "BTC/USD"
    return symbol
