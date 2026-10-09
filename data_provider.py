"""
TwelveData API Data Provider
Handles clean candle fetching, rate limiting (1.2s delay, 8/min throttle), error handling,
local caching, and mock data generation for testing.
"""
import time
import json
import logging
import random
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
import urllib.request
import urllib.parse
import urllib.error
import ssl

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CONTEXT = ssl._create_unverified_context()

from config import (
    TWELVEDATA_API_KEY,
    INTERVAL_MAP,
    API_CALL_DELAY,
    API_RATE_LIMIT_PER_MIN,
    format_twelvedata_symbol,
    DATA_DIR
)

logger = logging.getLogger("ETM_DataProvider")


class TwelveDataProvider:
    """
    Fetches OHLCV candlestick data from TwelveData REST API.
    Enforces delay, rate limiting, and error handling.
    """

    BASE_URL = "https://api.twelvedata.com/time_series"

    def __init__(self, api_key: str = TWELVEDATA_API_KEY, delay: float = API_CALL_DELAY):
        self.api_key = api_key
        self.delay = delay
        self.last_call_time = 0.0
        self.minute_call_timestamps: List[float] = []
        self.cache_dir = DATA_DIR / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _enforce_rate_limit(self) -> None:
        """
        Respects:
        1. 1.2s delay between consecutive calls.
        2. 8 calls/minute TwelveData free plan limit (waits if 8 calls made in last 60s).
        """
        now = time.time()

        # 1. Delay between calls
        elapsed_since_last = now - self.last_call_time
        if elapsed_since_last < self.delay:
            sleep_needed = self.delay - elapsed_since_last
            time.sleep(sleep_needed)

        # 2. Minute sliding window check
        now = time.time()
        self.minute_call_timestamps = [t for t in self.minute_call_timestamps if now - t < 60.0]
        if len(self.minute_call_timestamps) >= API_RATE_LIMIT_PER_MIN:
            wait_time = 60.0 - (now - self.minute_call_timestamps[0]) + 1.0
            if wait_time > 0:
                logger.warning(
                    "Hit minute rate limit (%d/min). Waiting %.1f seconds...",
                    API_RATE_LIMIT_PER_MIN, wait_time
                )
                time.sleep(wait_time)

        self.last_call_time = time.time()
        self.minute_call_timestamps.append(self.last_call_time)

    def _safe_urlopen(self, req, timeout=20):
        """Executes urlopen with SSL_CONTEXT and fallback if self-signed cert chain."""
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT)
        except urllib.error.URLError as e:
            if "certificate verify failed" in str(e):
                unverified_ctx = ssl._create_unverified_context()
                return urllib.request.urlopen(req, timeout=timeout, context=unverified_ctx)
            raise

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        outputsize: int = 100,
        use_cache: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Fetches candles from TwelveData API for a symbol and timeframe.
        Returns candles in chronological order (oldest to newest):
        [{'open': float, 'high': float, 'low': float, 'close': float, 'datetime': str}, ...]
        """
        interval = INTERVAL_MAP.get(timeframe, "1day")
        cache_key = f"{symbol.replace('/', '')}_{timeframe}_{outputsize}.json"
        cache_file = self.cache_dir / cache_key

        if use_cache and cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

        if not self.api_key or self.api_key.startswith("your_"):
            logger.warning("No valid TWELVEDATA_API_KEY provided. Falling back to synthetic candles.")
            return self.generate_mock_candles(symbol, timeframe, outputsize)

        td_symbol = format_twelvedata_symbol(symbol)
        params = {
            "symbol": td_symbol,
            "interval": interval,
            "outputsize": str(outputsize),
            "apikey": self.api_key,
            "order": "ASC"  # Oldest first
        }

        url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"

        max_retries = 3
        for attempt in range(max_retries):
            self._enforce_rate_limit()
            try:
                logger.info("Requesting TwelveData: %s (%s, outputsize=%d)", td_symbol, interval, outputsize)
                import requests
                resp = requests.get(
                    self.BASE_URL,
                    params=params,
                    headers={"User-Agent": "ETM-Matrix-Bias-Bot/1.0"},
                    timeout=20
                )
                data = resp.json()

                # Check TwelveData API response status
                if data.get("status") == "error":
                    msg = data.get("message", "Unknown API error")
                    code = data.get("code", 0)
                    logger.error("TwelveData API error [%s]: %s", code, msg)
                    if code == 429 or "limit" in msg.lower():
                        logger.warning("Rate limit error. Backing off 65 seconds...")
                        time.sleep(65)
                        continue
                    return []

                raw_values = data.get("values", [])
                if not raw_values:
                    logger.warning("No candles returned for %s", symbol)
                    return []

                # Clean and parse candles
                candles = []
                for val in raw_values:
                    try:
                        candles.append({
                            "datetime": str(val["datetime"]),
                            "open": float(val["open"]),
                            "high": float(val["high"]),
                            "low": float(val["low"]),
                            "close": float(val["close"]),
                            "volume": float(val.get("volume", 0) or 0)
                        })
                    except (ValueError, KeyError):
                        continue

                # Ensure chronological order (oldest to newest)
                candles.sort(key=lambda c: c["datetime"])

                # Save to cache
                try:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(candles, f)
                except Exception:
                    pass

                return candles

            except urllib.error.HTTPError as e:
                logger.error("HTTP error fetching %s: %s", symbol, e)
                if e.code == 429:
                    time.sleep(65)
            except Exception as e:
                logger.error("Error fetching %s (attempt %d): %s", symbol, attempt + 1, e)
                time.sleep(3)

        return []

    def generate_mock_candles(
        self,
        symbol: str,
        timeframe: str,
        count: int = 80
    ) -> List[Dict[str, Any]]:
        """
        Generates realistic clean candle data for offline testing and simulations.
        """
        candles = []
        sym = symbol.upper().replace("/", "")

        # Set baseline price
        if "JPY" in sym:
            base_price = 152.0
            volatility = 0.35
        elif sym in ("XAUUSD",):
            base_price = 2650.0
            volatility = 4.5
        elif sym in ("XAGUSD",):
            base_price = 31.50
            volatility = 0.15
        elif sym in ("BTCUSD", "BTC"):
            base_price = 68000.0
            volatility = 450.0
        else:
            base_price = 1.0850
            volatility = 0.0020

        # Intervals delta
        delta_map = {
            "Weekly": timedelta(days=7),
            "Daily": timedelta(days=1),
            "H4": timedelta(hours=4)
        }
        step = delta_map.get(timeframe, timedelta(days=1))
        curr_time = datetime.utcnow() - (step * count)
        price = base_price

        for i in range(count):
            curr_time += step
            time_str = curr_time.strftime("%Y-%m-%d %H:%M:%S")

            change = (random.random() - 0.49) * volatility
            c_open = price
            c_close = c_open + change
            c_high = max(c_open, c_close) + abs(random.random() * volatility * 0.5)
            c_low = min(c_open, c_close) - abs(random.random() * volatility * 0.5)

            candles.append({
                "datetime": time_str,
                "open": round(c_open, 5),
                "high": round(c_high, 5),
                "low": round(c_low, 5),
                "close": round(c_close, 5),
                "volume": random.randint(1000, 100000)
            })
            price = c_close

        return candles
