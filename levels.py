"""
ETM The Matrix Full - Level Detection Module
Detects A Shape, V Shape, Bullish OCL, Bearish OCL.
Tracks disrespect counts and classifies into FRESH, FLIPPED (RBS/SBR), QM.
Persists levels to levels.json.
"""
import json
import logging
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Any
from pathlib import Path

from config import get_tolerance, LEVELS_FILE, TIMEFRAME_DAILY, TIMEFRAME_WEEKLY, TIMEFRAME_H4

logger = logging.getLogger("ETM_Levels")


@dataclass
class ETMLevel:
    id: str
    symbol: str
    price: float
    type: str  # "A", "V", "BullOCL", "BearOCL"
    timeframe: str  # "Daily", "Weekly", "H4"
    disrespect_count: int
    first_seen_date: str
    classification: str  # "FRESH", "RBS", "SBR", "QM"
    active_role: str  # "Resistance", "Support", "RBS", "SBR", "BOTH"
    formed_index: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ETMLevel":
        return cls(**data)


class LevelDetector:
    """
    Detects and manages ETM Matrix levels for all symbols and timeframes.
    """

    def __init__(self, storage_path: Path = LEVELS_FILE):
        self.storage_path = storage_path
        self.levels: Dict[str, Dict[str, List[ETMLevel]]] = {}
        self.load_levels()

    def load_levels(self) -> None:
        """Load levels from JSON storage if exists."""
        if not self.storage_path.exists():
            self.levels = {}
            return

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            self.levels = {}
            for sym, tf_map in raw_data.items():
                self.levels[sym] = {}
                for tf, level_list in tf_map.items():
                    self.levels[sym][tf] = [ETMLevel.from_dict(item) for item in level_list]
            logger.info("Loaded levels for %d symbols from %s", len(self.levels), self.storage_path)
        except Exception as e:
            logger.error("Failed to load levels.json: %s", e)
            self.levels = {}

    def save_levels(self) -> None:
        """Save current levels to JSON storage."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            serializable = {}
            for sym, tf_map in self.levels.items():
                serializable[sym] = {}
                for tf, level_list in tf_map.items():
                    serializable[sym][tf] = [lvl.to_dict() for lvl in level_list]

            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(serializable, f, indent=2)
            logger.debug("Successfully saved levels to %s", self.storage_path)
        except Exception as e:
            logger.error("Failed to save levels.json: %s", e)

    @staticmethod
    def classify_level(base_type: str, disrespect_count: int) -> tuple[str, str]:
        """
        Classifies level based on disrespect count:
        - count 0 = FRESH: Fresh A (Res), Fresh V (Sup), Fresh Bull OCL (Sup), Fresh Bear OCL (Res)
        - count 1 = FLIPPED: A -> RBS, V -> SBR, Bull OCL -> SBR, Bear OCL -> RBS
        - count >= 2 = QM (Quasimodo) - BOTH ways
        Returns: (classification, active_role)
        """
        if disrespect_count == 0:
            classification = "FRESH"
            if base_type in ("A", "BearOCL"):
                role = "Resistance"
            else:  # V, BullOCL
                role = "Support"
        elif disrespect_count == 1:
            classification = "FLIPPED"
            if base_type == "A":
                role = "RBS"  # Resistance Becomes Support (buys)
            elif base_type == "BearOCL":
                role = "RBS"  # Bear OCL flipped -> RBS (buys)
            elif base_type == "V":
                role = "SBR"  # Support Becomes Resistance (sells)
            elif base_type == "BullOCL":
                role = "SBR"  # Bull OCL flipped -> SBR (sells)
            else:
                role = "BOTH"
        else:
            classification = "QM"
            role = "BOTH"

        return classification, role

    def detect_levels_from_candles(
        self, symbol: str, timeframe: str, candles: List[Dict[str, Any]]
    ) -> List[ETMLevel]:
        """
        candles: list of candle dicts in chronological order (oldest to newest):
                 [{'open': float, 'high': float, 'low': float, 'close': float, 'datetime': str}, ...]
        Returns detected and classified ETM levels.
        """
        if len(candles) < 2:
            return []

        detected_levels: List[ETMLevel] = []

        for i in range(len(candles) - 1):
            c1 = candles[i]
            c2 = candles[i + 1]

            c1_open, c1_close = float(c1["open"]), float(c1["close"])
            c2_open, c2_close = float(c2["open"]), float(c2["close"])

            # Mid price for tolerance calculation
            mid_price = (c1_close + c2_open) / 2.0
            tol = get_tolerance(symbol, mid_price)

            # Check if close of c1 matches open of c2 within tolerance
            if abs(c1_close - c2_open) > tol:
                continue

            c1_bullish = c1_close >= c1_open
            c2_bullish = c2_close >= c2_open

            level_type = None

            # a) A Shape: bullish candle close == next bearish candle open at SAME HIGH
            if c1_bullish and not c2_bullish:
                level_type = "A"

            # b) V Shape: bearish candle close == next bullish candle open at SAME LOW
            elif not c1_bullish and c2_bullish:
                level_type = "V"

            # c) Bullish OCL: bullish candle close == next bullish candle open at same level
            elif c1_bullish and c2_bullish:
                level_type = "BullOCL"

            # d) Bearish OCL: bearish candle close == next bearish candle open at same level
            elif not c1_bullish and not c2_bullish:
                level_type = "BearOCL"

            if not level_type:
                continue

            level_price = round((c1_close + c2_open) / 2.0, 5)
            first_seen = str(c2.get("datetime", ""))

            # Calculate disrespect count using subsequent candles (from i + 2 onwards)
            disrespect_count = 0
            for j in range(i + 2, len(candles)):
                cj_close = float(candles[j]["close"])
                # For resistance (A, BearOCL): close > level => count + 1
                if level_type in ("A", "BearOCL") and cj_close > level_price:
                    disrespect_count += 1
                # For support (V, BullOCL): close < level => count + 1
                elif level_type in ("V", "BullOCL") and cj_close < level_price:
                    disrespect_count += 1

            classification, active_role = self.classify_level(level_type, disrespect_count)

            level_id = f"{symbol}_{timeframe}_{level_type}_{level_price:.5f}_{first_seen}"

            etm_level = ETMLevel(
                id=level_id,
                symbol=symbol,
                price=level_price,
                type=level_type,
                timeframe=timeframe,
                disrespect_count=disrespect_count,
                first_seen_date=first_seen,
                classification=classification,
                active_role=active_role,
                formed_index=i + 1
            )
            detected_levels.append(etm_level)

        # Store in internal cache
        if symbol not in self.levels:
            self.levels[symbol] = {}
        self.levels[symbol][timeframe] = detected_levels
        self.save_levels()

        return detected_levels

    def get_support_levels(self, symbol: str, timeframe: str) -> List[ETMLevel]:
        """
        Returns all levels that currently act as SUPPORT:
        - Fresh V
        - Fresh Bull OCL
        - RBS (Flipped A or Bear OCL)
        - QM (both ways)
        """
        levels = self.levels.get(symbol, {}).get(timeframe, [])
        supports = []
        for lvl in levels:
            if lvl.classification == "FRESH" and lvl.type in ("V", "BullOCL"):
                supports.append(lvl)
            elif lvl.classification == "FLIPPED" and lvl.active_role == "RBS":
                supports.append(lvl)
            elif lvl.classification == "QM":
                supports.append(lvl)
        return supports

    def get_resistance_levels(self, symbol: str, timeframe: str) -> List[ETMLevel]:
        """
        Returns all levels that currently act as RESISTANCE:
        - Fresh A
        - Fresh Bear OCL
        - SBR (Flipped V or Bull OCL)
        - QM (both ways)
        """
        levels = self.levels.get(symbol, {}).get(timeframe, [])
        resistances = []
        for lvl in levels:
            if lvl.classification == "FRESH" and lvl.type in ("A", "BearOCL"):
                resistances.append(lvl)
            elif lvl.classification == "FLIPPED" and lvl.active_role == "SBR":
                resistances.append(lvl)
            elif lvl.classification == "QM":
                resistances.append(lvl)
        return resistances

    def get_all_levels(self, symbol: str, timeframe: str) -> List[ETMLevel]:
        """Get all detected levels for a symbol and timeframe."""
        return self.levels.get(symbol, {}).get(timeframe, [])
