"""
ETM Matrix Confirmation Module
Implements Lower Timeframe (LTF) Breakout Detection with Flexible Window:
- Weekly Bias -> Search 50 Daily candles before Candle1 for nearest A/BearOCL (bull) or V/BullOCL (bear)
- Daily Bias -> Search 50 H4 candles before Candle1 for nearest A/BearOCL (bull) or V/BullOCL (bear)
- Flexible Window: Checks breakout during Candle2 vs Candle3 (or within 10 candles for Rejection)
"""
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional, Any
from datetime import datetime

from config import TIMEFRAME_DAILY, TIMEFRAME_WEEKLY, TIMEFRAME_H4
from levels import ETMLevel, LevelDetector
from bias_models import BiasSignal

logger = logging.getLogger("ETM_Confirmation")


@dataclass
class ConfirmationResult:
    is_confirmed: bool
    breakout_level: Optional[ETMLevel] = None
    breakout_price: float = 0.0
    breakout_level_type: str = ""
    breakout_candle_time: str = ""
    timing: str = ""  # "during Candle2", "during Candle3", "after rejection"
    entry_instruction: str = ""  # "Enter Candle3 at open" or "Enter now"
    details: str = ""


class ConfirmationEngine:
    """
    Finds LTF levels and checks for breakouts within the flexible window or post-rejection.
    """

    def __init__(self, level_detector: Optional[LevelDetector] = None):
        self.level_detector = level_detector or LevelDetector()

    @staticmethod
    def parse_time(ts_str: str) -> Optional[datetime]:
        """Parses various datetime string formats from TwelveData."""
        if not ts_str:
            return None
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ"
        ]
        for fmt in formats:
            try:
                return datetime.strptime(ts_str, fmt)
            except ValueError:
                continue
        return None

    def find_nearest_ltf_level(
        self,
        symbol: str,
        ltf_timeframe: str,
        direction: str,
        ltf_candles: List[Dict[str, Any]],
        before_time: Optional[str] = None
    ) -> Optional[ETMLevel]:
        """
        Weekly bias -> find nearest Daily A/BearOCL (bull) or V/BullOCL (bear) that caused that low/high.
        Daily bias -> find nearest H4 A/BearOCL (bull) or V/BullOCL (bear).
        Searches up to 50 candles before Candle1 (or reference time).
        """
        # Filter candles up to before_time if specified
        target_candles = ltf_candles
        if before_time:
            dt_before = self.parse_time(before_time)
            if dt_before:
                target_candles = [
                    c for c in ltf_candles
                    if self.parse_time(str(c.get("datetime", ""))) and self.parse_time(str(c.get("datetime", ""))) <= dt_before
                ]

        # Use up to last 50 candles
        search_window = target_candles[-50:] if len(target_candles) > 50 else target_candles

        # Detect levels in this LTF window
        detected = self.level_detector.detect_levels_from_candles(symbol, ltf_timeframe, search_window)

        if not detected:
            return None

        # Filter by required type:
        # Bullish bias needs Resistance level (A or BearOCL) to break above
        # Bearish bias needs Support level (V or BullOCL) to break below
        candidates: List[ETMLevel] = []
        for lvl in detected:
            if direction == "BULLISH" and lvl.type in ("A", "BearOCL"):
                candidates.append(lvl)
            elif direction == "BEARISH" and lvl.type in ("V", "BullOCL"):
                candidates.append(lvl)

        if not candidates:
            return None

        # Nearest level from the end of the search window (most recent structural pivot)
        return candidates[-1]

    def verify_confirmation(
        self,
        bias_signal: BiasSignal,
        ltf_candles: List[Dict[str, Any]]
    ) -> ConfirmationResult:
        """
        Verifies lower TF breakout according to:
        - Sweep: Flexible window [Candle2 open time, Candle3 close/current time]
        - Rejection only: Next 10 LTF candles after rejection close
        """
        symbol = bias_signal.symbol
        htf = bias_signal.timeframe
        direction = bias_signal.direction

        # Determine Lower Timeframe
        ltf_timeframe = TIMEFRAME_DAILY if htf == TIMEFRAME_WEEKLY else TIMEFRAME_H4

        # Determine reference time before Candle1 / rejection
        reference_time = None
        if bias_signal.sweep and bias_signal.sweep.candle1:
            reference_time = str(bias_signal.sweep.candle1.get("datetime", ""))
        elif bias_signal.rejection:
            reference_time = bias_signal.rejection.candle_datetime

        # 1. Find the LTF key structural level formed before Candle1
        ltf_level = self.find_nearest_ltf_level(
            symbol=symbol,
            ltf_timeframe=ltf_timeframe,
            direction=direction,
            ltf_candles=ltf_candles,
            before_time=reference_time
        )

        if not ltf_level:
            return ConfirmationResult(
                is_confirmed=False,
                details=f"No LTF ({ltf_timeframe}) structural level found in 50 candles before {reference_time}"
            )

        breakout_price = ltf_level.price

        # 2. Case A: Sweep is present (Flexible Window)
        if bias_signal.sweep and bias_signal.sweep.candle2:
            c2_dt = self.parse_time(str(bias_signal.sweep.candle2.get("datetime", "")))
            # Candle2 end time approximation or timestamp
            # For Daily candle, next day is Candle3. For Weekly, next week is Candle3.
            # In flexible window: check all LTF candles from Candle2 open onwards
            for c in ltf_candles:
                c_dt = self.parse_time(str(c.get("datetime", "")))
                if not c_dt or not c2_dt:
                    continue

                if c_dt >= c2_dt:
                    c_close = float(c["close"])
                    # Bullish breakout: close > breakout_price
                    # Bearish breakout: close < breakout_price
                    is_break = (
                        (direction == "BULLISH" and c_close > breakout_price) or
                        (direction == "BEARISH" and c_close < breakout_price)
                    )

                    if is_break:
                        # Determine if breakout happened during Candle2 or during Candle3
                        # We compare timestamp: if it matches Candle2 day/week vs subsequent
                        c_date_str = str(c.get("datetime", ""))
                        c2_date_str = str(bias_signal.sweep.candle2.get("datetime", ""))

                        if c_date_str.split(" ")[0] == c2_date_str.split(" ")[0]:
                            timing = "during Candle2"
                            entry_instruction = "Breakout during Candle2 - Enter Candle3 at open"
                        else:
                            timing = "during Candle3"
                            entry_instruction = "Breakout during Candle3 - Enter now"

                        return ConfirmationResult(
                            is_confirmed=True,
                            breakout_level=ltf_level,
                            breakout_price=breakout_price,
                            breakout_level_type=ltf_level.type,
                            breakout_candle_time=c_date_str,
                            timing=timing,
                            entry_instruction=entry_instruction,
                            details=(
                                f"{ltf_timeframe} {ltf_level.type} breakout at {breakout_price:.5f} "
                                f"({timing}). Close: {c_close:.5f}"
                            )
                        )

            return ConfirmationResult(
                is_confirmed=False,
                breakout_level=ltf_level,
                breakout_price=breakout_price,
                details=f"No {ltf_timeframe} close broke {breakout_price:.5f} in flexible window."
            )

        # 3. Case B: Rejection Only
        if bias_signal.rejection:
            rej_dt = self.parse_time(bias_signal.rejection.candle_datetime)
            post_candles = []
            if rej_dt:
                post_candles = [
                    c for c in ltf_candles
                    if self.parse_time(str(c.get("datetime", ""))) and self.parse_time(str(c.get("datetime", ""))) >= rej_dt
                ]
            else:
                post_candles = ltf_candles[-10:]

            # Monitor next 10 lower TF candles
            for c in post_candles[:10]:
                c_close = float(c["close"])
                is_break = (
                    (direction == "BULLISH" and c_close > breakout_price) or
                    (direction == "BEARISH" and c_close < breakout_price)
                )
                if is_break:
                    c_date_str = str(c.get("datetime", ""))
                    return ConfirmationResult(
                        is_confirmed=True,
                        breakout_level=ltf_level,
                        breakout_price=breakout_price,
                        breakout_level_type=ltf_level.type,
                        breakout_candle_time=c_date_str,
                        timing="after rejection",
                        entry_instruction="Enter now",
                        details=(
                            f"{ltf_timeframe} {ltf_level.type} breakout at {breakout_price:.5f} "
                            f"(after rejection). Close: {c_close:.5f}"
                        )
                    )

            return ConfirmationResult(
                is_confirmed=False,
                breakout_level=ltf_level,
                breakout_price=breakout_price,
                details=f"No {ltf_timeframe} close broke {breakout_price:.5f} within 10 candles after rejection."
            )

        return ConfirmationResult(is_confirmed=False, details="No valid bias to confirm.")
