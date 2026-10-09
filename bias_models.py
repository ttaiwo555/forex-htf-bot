"""
ETM Matrix Bias Models
Model 1: HTF Key Level Rejection (Reusable)
Model 2: 3-Candle Sweep One-Time Use (Swept Levels Tracker)
Confluence: Determines Rejection Only, Sweep Only, or Both Confluence 🔥
"""
import json
import logging
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Any, Tuple
from pathlib import Path

from config import SWEPT_LEVELS_FILE, TIMEFRAME_DAILY, TIMEFRAME_WEEKLY
from levels import ETMLevel

logger = logging.getLogger("ETM_BiasModels")


@dataclass
class RejectionResult:
    is_rejection: bool
    direction: str  # "BULLISH", "BEARISH", "NONE"
    rejected_level: Optional[ETMLevel] = None
    candle_index: int = -1
    candle_datetime: str = ""
    details: str = ""


@dataclass
class SweepResult:
    is_sweep: bool
    direction: str  # "BULLISH", "BEARISH", "NONE"
    candle1: Optional[Dict[str, Any]] = None
    candle2: Optional[Dict[str, Any]] = None
    swept_level_type: str = ""  # "LOW" or "HIGH"
    swept_price: float = 0.0
    preserved_price: float = 0.0
    close_price: float = 0.0
    details: str = ""


@dataclass
class BiasSignal:
    symbol: str
    timeframe: str  # "Daily" or "Weekly"
    direction: str  # "BULLISH" or "BEARISH"
    confluence_type: str  # "REJECTION ONLY", "SWEEP ONLY - 3-Candle BRS/BRB", "BOTH CONFLUENCE 🔥"
    priority: str  # "Normal" or "HIGH"
    rejection: Optional[RejectionResult] = None
    sweep: Optional[SweepResult] = None
    key_level: Optional[ETMLevel] = None
    timestamp: str = ""


class SweptLevelsTracker:
    """
    Tracks swept Candle1 victim levels to guarantee one-time use per prompt requirement.
    """

    def __init__(self, storage_path: Path = SWEPT_LEVELS_FILE):
        self.storage_path = storage_path
        self.swept_records: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        self.load()

    def load(self) -> None:
        if not self.storage_path.exists():
            self.swept_records = {}
            return
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                self.swept_records = json.load(f)
            logger.info("Loaded swept levels from %s", self.storage_path)
        except Exception as e:
            logger.error("Failed to load swept_levels.json: %s", e)
            self.swept_records = {}

    def save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.swept_records, f, indent=2)
        except Exception as e:
            logger.error("Failed to save swept_levels.json: %s", e)

    def is_swept(self, symbol: str, timeframe: str, candle1_time: str, level_type: str) -> bool:
        """Check if Candle1's HIGH or LOW has already been marked as used."""
        records = self.swept_records.get(symbol, {}).get(timeframe, [])
        for r in records:
            if r.get("candle1_time") == candle1_time and r.get("type") == level_type:
                return True
        return False

    def mark_as_swept(
        self,
        symbol: str,
        timeframe: str,
        candle1_time: str,
        level_type: str,
        price: float,
        swept_by_time: str,
        direction: str
    ) -> None:
        """Record Candle1 level as used in swept_levels.json."""
        if symbol not in self.swept_records:
            self.swept_records[symbol] = {}
        if timeframe not in self.swept_records[symbol]:
            self.swept_records[symbol][timeframe] = []

        self.swept_records[symbol][timeframe].append({
            "candle1_time": candle1_time,
            "type": level_type,
            "price": price,
            "swept_by_time": swept_by_time,
            "direction": direction
        })
        self.save()
        logger.info(
            "Marked %s %s %s [%s=%.5f] as USED in swept_levels.json",
            symbol, timeframe, candle1_time, level_type, price
        )


class BiasEngine:
    """
    Evaluates Model 1 (Key Level Rejection) and Model 2 (3-Candle Sweep).
    Determines confluence and bias direction.
    """

    def __init__(self, swept_tracker: Optional[SweptLevelsTracker] = None):
        self.swept_tracker = swept_tracker or SweptLevelsTracker()

    def evaluate_rejection(
        self,
        symbol: str,
        timeframe: str,
        candle: Dict[str, Any],
        active_supports: List[ETMLevel],
        active_resistances: List[ETMLevel]
    ) -> RejectionResult:
        """
        BIAS MODEL 1 - HTF KEY LEVEL REJECTION (REUSABLE)
        Bullish Rejection: candle low < support level AND close > that level
        Bearish Rejection: candle high > resistance level AND close < that level
        """
        c_low = float(candle["low"])
        c_high = float(candle["high"])
        c_close = float(candle["close"])
        c_time = str(candle.get("datetime", ""))

        # Check Bullish Rejection against active supports
        for sup in active_supports:
            if c_low < sup.price and c_close > sup.price:
                # Level role label
                role_label = (
                    f"Fresh {sup.type}" if sup.classification == "FRESH"
                    else f"RBS from {sup.type}" if sup.classification == "FLIPPED"
                    else "QM Support"
                )
                details = f"Low ({c_low:.5f}) dipped below {role_label} at {sup.price:.5f}, closed above ({c_close:.5f})"
                return RejectionResult(
                    is_rejection=True,
                    direction="BULLISH",
                    rejected_level=sup,
                    candle_datetime=c_time,
                    details=details
                )

        # Check Bearish Rejection against active resistances
        for res in active_resistances:
            if c_high > res.price and c_close < res.price:
                role_label = (
                    f"Fresh {res.type}" if res.classification == "FRESH"
                    else f"SBR from {res.type}" if res.classification == "FLIPPED"
                    else "QM Resistance"
                )
                details = f"High ({c_high:.5f}) spiked above {role_label} at {res.price:.5f}, closed below ({c_close:.5f})"
                return RejectionResult(
                    is_rejection=True,
                    direction="BEARISH",
                    rejected_level=res,
                    candle_datetime=c_time,
                    details=details
                )

        return RejectionResult(is_rejection=False, direction="NONE")

    def evaluate_sweep(
        self,
        symbol: str,
        timeframe: str,
        c1: Dict[str, Any],
        c2: Dict[str, Any]
    ) -> SweepResult:
        """
        BIAS MODEL 2 - 3-CANDLE SWEEP ONE-TIME USE
        Candle1 = previous victim (High1, Low1)
        Candle2 = current sweeper
        BULLISH SWEEP:
          1. Low2 < Low1 (sweeps low)
          2. High2 < High1 (high preserved, NOT touched)
          3. Close2 > Low1 AND Close2 < High1 (closes INSIDE Candle1 range)
        BEARISH SWEEP:
          1. High2 > High1 (sweeps high)
          2. Low2 > Low1 (low preserved, NOT touched)
          3. Close2 < High1 AND Close2 > Low1 (closes INSIDE Candle1 range)
        """
        high1, low1 = float(c1["high"]), float(c1["low"])
        high2, low2, close2 = float(c2["high"]), float(c2["low"]), float(c2["close"])
        c1_time = str(c1.get("datetime", ""))
        c2_time = str(c2.get("datetime", ""))

        # 1. Bullish Sweep Test
        if low2 < low1 and high2 < high1 and (low1 < close2 < high1):
            # Check if Candle1 Low already swept (one-time use)
            if not self.swept_tracker.is_swept(symbol, timeframe, c1_time, "LOW"):
                details = (
                    f"Candle1 Low ({low1:.5f}) swept by Low2 ({low2:.5f}), "
                    f"High preserved ({high2:.5f} < {high1:.5f}), "
                    f"Close inside ({close2:.5f})"
                )
                return SweepResult(
                    is_sweep=True,
                    direction="BULLISH",
                    candle1=c1,
                    candle2=c2,
                    swept_level_type="LOW",
                    swept_price=low1,
                    preserved_price=high1,
                    close_price=close2,
                    details=details
                )

        # 2. Bearish Sweep Test
        if high2 > high1 and low2 > low1 and (low1 < close2 < high1):
            # Check if Candle1 High already swept (one-time use)
            if not self.swept_tracker.is_swept(symbol, timeframe, c1_time, "HIGH"):
                details = (
                    f"Candle1 High ({high1:.5f}) swept by High2 ({high2:.5f}), "
                    f"Low preserved ({low2:.5f} > {low1:.5f}), "
                    f"Close inside ({close2:.5f})"
                )
                return SweepResult(
                    is_sweep=True,
                    direction="BEARISH",
                    candle1=c1,
                    candle2=c2,
                    swept_level_type="HIGH",
                    swept_price=high1,
                    preserved_price=low1,
                    close_price=close2,
                    details=details
                )

        return SweepResult(is_sweep=False, direction="NONE")

    def evaluate_bias(
        self,
        symbol: str,
        timeframe: str,
        candles: List[Dict[str, Any]],
        active_supports: List[ETMLevel],
        active_resistances: List[ETMLevel]
    ) -> Optional[BiasSignal]:
        """
        Analyzes the latest completed candle (Candle2) against Candle1 and active levels.
        Calculates confluence:
        - rejection only = "REJECTION ONLY"
        - sweep only = "SWEEP ONLY - 3-Candle BRS/BRB"
        - both = "BOTH CONFLUENCE 🔥"
        """
        if len(candles) < 2:
            return None

        # Candle2 is the sweeper/rejection candle (most recently closed candle)
        c2 = candles[-1]
        c1 = candles[-2]

        rejection = self.evaluate_rejection(symbol, timeframe, c2, active_supports, active_resistances)
        sweep = self.evaluate_sweep(symbol, timeframe, c1, c2)

        # Check for direction alignment or single bias
        is_bull_rejection = rejection.is_rejection and rejection.direction == "BULLISH"
        is_bear_rejection = rejection.is_rejection and rejection.direction == "BEARISH"
        is_bull_sweep = sweep.is_sweep and sweep.direction == "BULLISH"
        is_bear_sweep = sweep.is_sweep and sweep.direction == "BEARISH"

        direction = None
        confluence_type = None
        priority = "Normal"
        key_level = None

        # Both Bullish Confluence
        if is_bull_rejection and is_bull_sweep:
            direction = "BULLISH"
            confluence_type = "BOTH CONFLUENCE 🔥"
            priority = "HIGH"
            key_level = rejection.rejected_level

        # Both Bearish Confluence
        elif is_bear_rejection and is_bear_sweep:
            direction = "BEARISH"
            confluence_type = "BOTH CONFLUENCE 🔥"
            priority = "HIGH"
            key_level = rejection.rejected_level

        # Bullish Rejection only
        elif is_bull_rejection and not is_bear_sweep:
            direction = "BULLISH"
            confluence_type = "REJECTION ONLY"
            priority = "Normal"
            key_level = rejection.rejected_level

        # Bearish Rejection only
        elif is_bear_rejection and not is_bull_sweep:
            direction = "BEARISH"
            confluence_type = "REJECTION ONLY"
            priority = "Normal"
            key_level = rejection.rejected_level

        # Bullish Sweep only
        elif is_bull_sweep and not is_bear_rejection:
            direction = "BULLISH"
            confluence_type = "SWEEP ONLY - 3-Candle BRS/BRB"
            priority = "Normal"

        # Bearish Sweep only
        elif is_bear_sweep and not is_bull_rejection:
            direction = "BEARISH"
            confluence_type = "SWEEP ONLY - 3-Candle BRS/BRB"
            priority = "Normal"

        if direction and confluence_type:
            return BiasSignal(
                symbol=symbol,
                timeframe=timeframe,
                direction=direction,
                confluence_type=confluence_type,
                priority=priority,
                rejection=rejection if rejection.direction == direction else None,
                sweep=sweep if sweep.direction == direction else None,
                key_level=key_level,
                timestamp=str(c2.get("datetime", ""))
            )

        return None
