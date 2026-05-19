from mtbl_valuations.domain.models import HitterStats, Player, PositionPool
from mtbl_valuations.engine.iteration import (
    iterate_to_convergence_global,
    iterate_to_convergence_per_position,
)


def _make_hitter(player_id: str, runs: float) -> Player:
    return Player(
        id=player_id,
        name=f"Player {player_id}",
        team="T",
        positions=["SS"],
        role="HITTER",
        stats=HitterStats(
            pa=10,
            ab=10,
            r=runs,
            hr=1,
            rbi=1,
            sbn=0,
            obp=0.3,
            slg=0.4,
        ),
    )


def test_iterate_to_convergence_composite_and_max_iterations(capsys):
    p1 = _make_hitter("1", 5)
    p2 = _make_hitter("2", 10)

    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.rostered_players = [p1, p2]
    pool.replacement_players = []
    pool.below_replacement = []

    budget_config = {
        "max_iterations": 1,
        "convergence_threshold": -1,
        "replacement_tier_pct": 0.03,
        "min_replacement_tier_size": 1,
    }
    league_settings = {"batting_categories": ["R"], "pitching_categories": []}
    composite = {"R": 7.0}

    iterate_to_convergence_global(
        {"SS": pool},
        budget_config,
        league_settings,
        composite_rlp_archetype=composite,
    )

    captured = capsys.readouterr()
    assert "Max iterations" in captured.out
    assert pool.rlp_raw_avg == composite


def test_iterate_to_convergence_per_position_with_composite(capsys):
    """Test per-position iteration with composite RLP archetype."""
    p1 = _make_hitter("1", 5)
    p2 = _make_hitter("2", 10)

    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.rostered_players = [p1, p2]
    pool.replacement_players = []
    pool.below_replacement = []

    budget_config = {
        "max_iterations": 1,
        "convergence_threshold": -1,
        "replacement_tier_pct": 0.03,
        "min_replacement_tier_size": 1,
    }
    league_settings = {"batting_categories": ["R"], "pitching_categories": []}
    composite = {"R": 7.0}

    iterate_to_convergence_per_position(
        {"SS": pool},
        budget_config,
        league_settings,
        composite_rlp_archetype=composite,
    )

    captured = capsys.readouterr()
    assert "Max iterations" in captured.out
    assert pool.rlp_raw_avg == composite


def test_recompute_pool_z_in_place_preserves_primary_position_top_level():
    """When the swap-pass refreshes multiple pools, the player's top-level
    valuation must reflect their PRIMARY pool — not whichever pool's
    recompute ran last. Regression test for the multi-pool overwrite bug."""
    from mtbl_valuations.domain.models import PositionValuation
    from mtbl_valuations.engine.iteration import recompute_pool_z_in_place

    # Same player rostered in 1B (primary) and UTIL (secondary).
    shared = _make_hitter("multi", runs=20.0)
    shared.valuation.primary_position = "1B"
    shared.valuation.valuations_by_position = {
        "1B": PositionValuation(
            position="1B", normalized_z={}, total_z=0.0, tier="ROSTERED",
            position_rank=1,
        ),
        "UTIL": PositionValuation(
            position="UTIL", normalized_z={}, total_z=0.0, tier="ROSTERED",
            position_rank=1,
        ),
    }
    # Two distinct populations for stdev / mean.
    pool_1b_floor = _make_hitter("1bf", runs=5.0)
    util_floor = _make_hitter("uf", runs=2.0)

    pool_1b = PositionPool(position="1B", role="HITTER", roster_slots=1)
    pool_1b.rostered_players = [shared, pool_1b_floor]
    pool_1b.replacement_players = []
    pool_1b.below_replacement = []

    pool_util = PositionPool(position="UTIL", role="HITTER", roster_slots=1)
    pool_util.rostered_players = [shared, util_floor]
    pool_util.replacement_players = []
    pool_util.below_replacement = []

    pools = {"1B": pool_1b, "UTIL": pool_util}
    league_settings = {"batting_categories": ["R"], "pitching_categories": []}
    budget_config = {"replacement_tier_pct": 0.03, "min_replacement_tier_size": 1}

    # Refresh 1B first, then UTIL — the buggy code would leave UTIL's
    # normalized_z stomped onto the top-level. Correct behavior: top-level
    # reflects 1B (the primary).
    recompute_pool_z_in_place(pool_1b, pools, budget_config, league_settings,
                              per_position=True)
    util_pre = dict(shared.valuation.normalized_z)
    recompute_pool_z_in_place(pool_util, pools, budget_config, league_settings,
                              per_position=True)

    # Per-position store has BOTH pools' z-scores.
    assert "1B" in shared.valuation.valuations_by_position
    assert "UTIL" in shared.valuation.valuations_by_position
    pv_1b = shared.valuation.valuations_by_position["1B"]
    pv_util = shared.valuation.valuations_by_position["UTIL"]
    # Different pools → different stdevs → different normalized_z.
    assert pv_1b.normalized_z != pv_util.normalized_z

    # Top-level reflects PRIMARY (1B), not the last-refreshed pool (UTIL).
    assert shared.valuation.normalized_z == pv_1b.normalized_z
    assert shared.valuation.normalized_z == util_pre
    assert shared.valuation.total_z == pv_1b.total_z


def test_swap_pass_positions_is_deterministic_tuple():
    """``_SWAP_PASS_POSITIONS`` must be an ordered tuple so the swap loop
    visits pools in the same order across Python invocations."""
    from mtbl_valuations.engine.pipeline import _SWAP_PASS_POSITIONS

    assert isinstance(_SWAP_PASS_POSITIONS, tuple)
    assert _SWAP_PASS_POSITIONS == ("C", "1B", "2B", "3B", "SS", "OF", "UTIL")
