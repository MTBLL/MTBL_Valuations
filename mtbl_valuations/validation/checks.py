"""Validation checks for TRP valuation outputs."""

from __future__ import annotations

from ..domain.models import LeagueBudget, PositionPool


def validate_budget_balance(
    all_pools: list[PositionPool], league_budget: LeagueBudget
) -> None:
    """Validate that total allocated dollars match league budget."""
    total_allocated = sum(
        sum(
            player.computed.valuations_by_position.get(
                pool.position, player.computed
            ).total_dollars
            for player in pool.rostered_players
        )
        for pool in all_pools
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
    all_pools: list[PositionPool], roster_slots: dict[str, int], num_teams: int
) -> None:
    """Validate that rostered tier sizes match roster slots."""
    print("\n=== Tier Count Validation ===")
    all_valid = True

    for pool in all_pools:
        expected = roster_slots.get(pool.position, 0) * num_teams
        actual = len(pool.rostered_players)

        if expected == actual:
            print(f"✓ {pool.position}: {actual}/{expected} rostered")
        else:
            print(f"✗ {pool.position}: {actual}/{expected} rostered (MISMATCH)")
            all_valid = False

    if all_valid:
        print("✓ All tier counts match expected roster slots")


def validate_rlp_z_scores(all_pools: list[PositionPool]) -> None:
    """Validate that RLP players have total normalized Z near 0."""
    print("\n=== RLP Z-Score Validation ===")

    for pool in all_pools:
        if pool.replacement_players:
            avg_z = sum(
                p.computed.total_z for p in pool.replacement_players
            ) / len(pool.replacement_players)
            print(f"{pool.position}: RLP avg total Z = {avg_z:.3f}")
