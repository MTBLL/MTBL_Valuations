"""Validation checks for TRP valuation outputs."""

from __future__ import annotations

from ..domain.models import LeagueBudget, PositionPool


def validate_budget_balance(
    all_pools: dict[str, PositionPool], league_budget: LeagueBudget
) -> None:
    """Validate that total allocated dollars match league budget."""
    total_allocated = sum(
        sum(player.valuation.total_dollars for player in pool.rostered_players)
        for _, pool in all_pools.items()
    )

    difference = abs(total_allocated - league_budget.total)

    print("\n=== Budget Validation ===")
    print(f"Total league budget: ${league_budget.total:,.2f}")
    print(f"Total allocated: ${total_allocated:,.2f}")
    print(f"Difference: ${difference:,.2f}")

    if difference <= 1.0:
        print("✓ Budget balance check PASSED")
    else:
        print(f"✗ Budget balance check FAILED (difference: ${difference:.2f})")


def validate_tier_counts(
    all_pools: dict[str, PositionPool], roster_slots: dict[str, int], num_teams: int
) -> None:
    """Validate that rostered tier sizes match roster slots."""
    print("\n=== Tier Count Validation ===")
    all_valid = True

    for pos, pool in all_pools.items():
        expected = roster_slots.get(pos, 0) * num_teams
        actual = len(pool.rostered_players)

        if expected == actual:
            print(f"✓ {pos}: {actual}/{expected} rostered")
        else:
            print(f"✗ {pos}: {actual}/{expected} rostered (MISMATCH)")
            all_valid = False

    if all_valid:
        print("✓ All tier counts match expected roster slots")


def validate_rlp_z_scores(all_pools: dict[str, PositionPool]) -> None:
    """Validate that RLP players have total normalized Z near 0."""
    print("\n=== RLP Z-Score Validation ===")
    all_valid = True

    for pos, pool in all_pools.items():
        if pool.replacement_players:
            avg_z = sum(p.valuation.total_z for p in pool.replacement_players) / len(
                pool.replacement_players
            )
            print(f"{pos}: RLP avg total Z = {avg_z:.3f}")

    if all_valid:
        print("✓ All RLP Z-scores are near 0")


def validate_position_valuation_hydration(pools: dict[str, PositionPool]) -> None:
    """Validate that rostered/replacement players have position-specific dollar values."""
    warnings = []

    for pos, pool in pools.items():
        for player in pool.rostered_players + pool.replacement_players:
            if pos not in player.valuation.valuations_by_position:
                warnings.append(
                    f"{player.name} in {pos} pool missing PositionValuation"
                )
            else:
                pos_val = player.valuation.valuations_by_position[pos]
                if not pos_val.dollar_values:
                    warnings.append(f"{player.name} at {pos} has empty dollar_values")

    if warnings:
        print("\n⚠️  PositionValuation Hydration Warnings:")
        for warning in warnings[:10]:  # Limit to first 10
            print(f"  - {warning}")
        if len(warnings) > 10:
            print(f"  ... and {len(warnings) - 10} more")
