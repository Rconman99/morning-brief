"""Market microstructure filters based on Becker's prediction market research.

Implements three key findings from "The Microstructure of Wealth Transfer
in Prediction Markets" (Jon Becker, 2024):

1. Longshot bias — cheap YES contracts are systematically overpriced
2. Maker advantage — limit orders have positive excess returns, market orders negative
3. Category-adjusted edge — sports/retail markets have wider spreads

These filters are applied to all Polymarket proposals before execution.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# ============================================================
# LONGSHOT BIAS CALIBRATION
# ============================================================
# Research shows contracts at certain price points have systematic
# mispricing. Low-price YES contracts win less often than their price
# implies. High-price contracts are better calibrated.

# Calibration adjustments: positive = market overprices (avoid buying YES),
# negative = market underprices (opportunity to buy YES)
LONGSHOT_BIAS = {
    # price_range: (adjustment, description)
    (0.01, 0.10): (+0.05, "Heavy longshot bias — avoid YES, consider NO"),
    (0.10, 0.20): (+0.03, "Moderate longshot bias — YES overpriced"),
    (0.20, 0.35): (+0.02, "Mild longshot bias"),
    (0.35, 0.65): (0.00, "Well-calibrated zone"),
    (0.65, 0.80): (-0.01, "Slight favorite bias — YES slightly underpriced"),
    (0.80, 0.92): (-0.02, "Favorite zone — reasonable value"),
    (0.92, 0.99): (-0.01, "Gimme zone — thin but real edge"),
}

# Category edge multipliers — how much wider the maker-taker spread is
# Sports/retail = more edge for informed maker strategies
# Finance/politics = tighter, less edge
CATEGORY_EDGE_MULTIPLIER = {
    "sports": 1.5,       # Widest maker-taker gap — most retail flow
    "culture": 1.3,      # Celebrity/social media — retail-heavy
    "weather": 1.2,      # Less liquid, more edge
    "crypto": 1.0,       # Average
    "politics": 0.9,     # More sophisticated participants
    "geopolitics": 0.8,  # Well-informed traders
    "economics": 0.7,    # Fed watchers are sharp
    "markets": 0.7,      # Finance-savvy
    "tech": 0.9,
    "other": 1.0,
}

# Time-of-day edge — research shows informed traders cluster at certain hours
# Returns multiplier: >1 = more informed flow (harder to make money),
# <1 = more retail flow (easier to capture spread)
HOUR_EDGE = {
    # Hour (UTC): multiplier
    0: 0.8,   # Late night US — retail
    1: 0.8,
    2: 0.9,
    3: 0.9,
    4: 1.0,   # Asia morning
    5: 1.0,
    6: 1.0,
    7: 1.1,   # EU morning — more institutional
    8: 1.2,
    9: 1.2,
    10: 1.1,
    11: 1.0,
    12: 1.0,
    13: 1.1,  # US pre-market
    14: 1.3,  # US market open — highest informed flow
    15: 1.2,
    16: 1.1,
    17: 1.0,
    18: 0.9,
    19: 0.9,
    20: 0.8,  # US evening — retail
    21: 0.8,
    22: 0.8,
    23: 0.8,
}


def get_longshot_adjustment(price: float) -> tuple[float, str]:
    """Get the longshot bias adjustment for a given market price.

    Returns (adjustment, description). Positive adjustment means the YES
    side is overpriced — avoid buying YES at this price.
    """
    for (low, high), (adj, desc) in LONGSHOT_BIAS.items():
        if low <= price < high:
            return adj, desc
    return 0.0, "Out of range"


def get_category_multiplier(category: str) -> float:
    """Get category-based edge multiplier.

    Higher = more edge available (more retail flow).
    Lower = tighter market (more informed participants).
    """
    return CATEGORY_EDGE_MULTIPLIER.get(category, 1.0)


def get_time_multiplier() -> float:
    """Get current time-of-day edge multiplier.

    Lower = more retail flow right now (better time to trade).
    Higher = more informed flow (worse time to place orders).
    """
    hour = datetime.utcnow().hour
    return HOUR_EDGE.get(hour, 1.0)


def apply_microstructure_filter(proposal: dict) -> dict:
    """Apply all microstructure filters to a trade proposal.

    Adjusts conviction and adds microstructure metadata.
    Returns the proposal with added fields:
    - longshot_adjustment: float
    - category_edge: float
    - time_edge: float
    - microstructure_score: float (combined)
    - microstructure_warning: str (if any)
    """
    price = proposal.get("price", 0.5)
    category = proposal.get("category", "other")
    side = proposal.get("side", "BUY")
    token_hint = proposal.get("token_hint", "yes")

    warnings = []

    # 1. Longshot bias check
    longshot_adj, longshot_desc = get_longshot_adjustment(price)
    proposal["longshot_adjustment"] = longshot_adj
    proposal["longshot_description"] = longshot_desc

    # Block: buying YES at < 15 cents (heavy longshot bias)
    if side == "BUY" and token_hint == "yes" and price < 0.15:
        warnings.append(f"LONGSHOT BLOCK: YES @ {price:.0%} is overpriced (Becker research)")
        proposal["microstructure_blocked"] = True
        proposal["conviction"] = max(0, proposal.get("conviction", 0) - 3)

    # Penalty: buying YES in longshot zone (15-35 cents)
    elif side == "BUY" and token_hint == "yes" and price < 0.35 and longshot_adj > 0:
        warnings.append(f"Longshot penalty: YES @ {price:.0%} has {longshot_adj*100:.0f}% bias")
        proposal["conviction"] = max(0, proposal.get("conviction", 0) - 1)

    # Bonus: buying NO side in longshot zone (exploiting the bias)
    elif side == "BUY" and token_hint == "no" and (1.0 - price) < 0.35:
        # The NO side of a longshot YES is underpriced
        proposal["conviction"] = proposal.get("conviction", 0) + 1
        warnings.append(f"Longshot advantage: NO side benefits from YES overpricing")

    # 2. Category edge
    cat_mult = get_category_multiplier(category)
    proposal["category_edge"] = cat_mult

    if cat_mult >= 1.3:
        proposal["conviction"] = proposal.get("conviction", 0) + 1
        warnings.append(f"High-edge category ({category}): +1 conviction")
    elif cat_mult <= 0.7:
        warnings.append(f"Tight market ({category}): informed participants, less edge")

    # 3. Time-of-day edge
    time_mult = get_time_multiplier()
    proposal["time_edge"] = time_mult

    if time_mult >= 1.2:
        warnings.append(f"High informed flow right now (UTC {datetime.utcnow().hour}:00) — wider spreads")
    elif time_mult <= 0.8:
        warnings.append(f"Retail-heavy hours — better fills expected")

    # Combined microstructure score (lower = better opportunity)
    micro_score = (1.0 + longshot_adj) * (1.0 / max(cat_mult, 0.5)) * time_mult
    proposal["microstructure_score"] = round(micro_score, 3)

    if warnings:
        proposal["microstructure_warnings"] = warnings

    return proposal


def filter_proposals_microstructure(proposals: list) -> list:
    """Apply microstructure filters to all proposals.

    Blocks heavily mispriced longshots, adjusts conviction for category
    and time-of-day effects, adds metadata for audit trail.
    """
    filtered = []
    for p in proposals:
        p = apply_microstructure_filter(p)

        if p.get("microstructure_blocked"):
            logger.info("MICROSTRUCTURE BLOCK: %s — %s",
                        p.get("question", "")[:50],
                        "; ".join(p.get("microstructure_warnings", [])))
            continue

        filtered.append(p)

    blocked = len(proposals) - len(filtered)
    if blocked:
        logger.info("Microstructure filter: blocked %d / %d proposals", blocked, len(proposals))

    return filtered
