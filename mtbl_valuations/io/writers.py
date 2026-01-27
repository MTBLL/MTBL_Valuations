"""Output generation for TRP valuation system."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from ..domain.models import PositionPool


def write_valuations_csv(
    output_path: Path,
    all_pools: dict[str, PositionPool],
    categories: dict[str, list[str]],
) -> None:
    """Write player valuations to CSV.

    Each player appears once per role, using their primary_position.
    Deduplicates players who appear in multiple pools (e.g., UTIL players).
    Two-way players (e.g., Ohtani) appear twice - once as hitter, once as pitcher.
    """
    rows = []
    seen_players = set()  # Track (player_id, role) we've already added

    for pos, pool in all_pools.items():
        all_players = (
            pool.rostered_players + pool.replacement_players + pool.below_replacement
        )

        for player in all_players:
            # Use (player_id, role) as key to allow two-way players
            player_key = (player.id, pool.role)

            # Skip if we've already added this player-role (e.g., UTIL player in original pool)
            if player_key in seen_players:
                continue

            seen_players.add(player_key)

            row = {
                "player_id": player.id,
                "name": player.name,
                "position": player.valuation.primary_position,  # Use primary_position
                "role": pool.role,
                "total_z": round(player.valuation.total_z, 3),
                "dollar_value": round(player.valuation.total_dollars, 2),
                "tier": player.valuation.tier,
            }

            # Add Z-scores per category
            for cat in player.valuation.normalized_z.keys():
                row[f"z_{cat}"] = round(player.valuation.normalized_z[cat], 3)

            # Add dollar values per category
            for cat in player.valuation.dollar_values.keys():
                row[f"dollar_{cat}"] = round(player.valuation.dollar_values[cat], 2)

            rows.append(row)

    # Sort by dollar value descending
    rows = sorted(rows, key=lambda r: float(r["dollar_value"]), reverse=True)  # type: ignore[arg-type]

    # Write to CSV
    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(output_path, index=False)


def write_position_summary_csv(
    output_path: Path, all_pools: dict[str, PositionPool]
) -> None:
    """Write position summary to CSV with pool totals and category budgets."""
    rows = []

    for pos, pool in all_pools.items():
        row = {
            "position": pos,
            "role": pool.role,
            "rostered_count": len(pool.rostered_players),
            "replacement_tier_count": len(pool.replacement_players),
            "total_budget": sum(pool.category_budgets.values()),
        }

        # Add category budgets
        for cat, budget in pool.category_budgets.items():
            # Skip IP for RP pools (weight is 0.0, no budget)
            if pool.position == "RP" and cat == "IP":
                continue
            row[f"budget_{cat}"] = round(budget, 2)

        # Add pool total Z-scores per category
        for cat, total_z in pool.total_pool_z.items():
            # Skip IP for RP pools (weight is 0.0, no Z-score tracking)
            if pool.position == "RP" and cat == "IP":
                continue
            row[f"pool_total_z_{cat}"] = round(total_z, 3)

        # Add $/Z per category
        for cat, rate in pool.dollars_per_z.items():
            # Skip IP for RP pools (weight is 0.0, no dollar value)
            if pool.position == "RP" and cat == "IP":
                continue
            row[f"dollars_per_z_{cat}"] = round(rate, 3)

        # Add replacement baseline stats
        for cat, value in pool.rlp_raw_avg.items():
            # Skip IP for RP pools (weight is 0.0, not used in valuation)
            if pool.position == "RP" and cat == "IP":
                continue
            row[f"replacement_baseline_{cat}"] = round(value, 3)

        rows.append(row)

    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(output_path, index=False)


def write_player_json(
    output_path: Path,
    input_data: list[dict[str, Any]],
    all_pools: dict[str, PositionPool],
) -> None:
    """
    Write enriched player JSON with valuation data.
    Matches input schema and appends stats.valuations object.
    """
    # Build lookup of player valuations
    player_valuations: dict[str, dict[str, Any]] = {}

    for pool in all_pools.values():
        all_players = (
            pool.rostered_players + pool.replacement_players + pool.below_replacement
        )

        for player in all_players:
            player_valuations[player.id] = {
                "primary_position": player.valuation.primary_position,
                "tier": player.valuation.tier,
                "total_z": round(player.valuation.total_z, 3),
                "total_dollars": round(player.valuation.total_dollars, 2),
                "z_scores": {
                    cat: round(val, 3)
                    for cat, val in player.valuation.normalized_z.items()
                },
                "dollar_values": {
                    cat: round(val, 2)
                    for cat, val in player.valuation.dollar_values.items()
                },
            }

    # Enrich input data with valuations
    enriched = []
    for record in input_data:
        player_id = str(record["id_espn"])
        if player_id in player_valuations:
            record["valuations"] = player_valuations[player_id]
        enriched.append(record)

    # Write to JSON
    with open(output_path, "w") as f:
        json.dump(enriched, f, indent=2)
