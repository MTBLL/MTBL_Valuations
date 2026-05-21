from mtbl_valuations.domain.models import HitterStats, Player, PositionPool
from mtbl_valuations.engine.iteration import (
    _compute_thin_cell_floor,
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
    recompute_pool_z_in_place(pool_1b, pools, budget_config, league_settings)
    util_pre = dict(shared.valuation.normalized_z)
    recompute_pool_z_in_place(pool_util, pools, budget_config, league_settings)

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


def test_compute_dollars_per_z_proxy_skips_empty_pool():
    """An empty rostered pool participating in the cross-pool rate-share
    calc must be skipped to avoid div-by-zero on its OBP/SLG average."""
    from mtbl_valuations.engine.iteration import _compute_dollars_per_z_proxy

    target = _make_hitter("t", runs=10.0)
    target.valuation.normalized_z = {"R": 1.0, "OBP": 1.0, "SLG": 1.0}
    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.rostered_players = [target]
    pool.total_pool_z = {"R": 1.0, "OBP": 1.0, "SLG": 1.0}

    empty = PositionPool(position="C", role="HITTER", roster_slots=1)
    empty.rostered_players = []  # triggers the skip branch

    pools = {"SS": pool, "C": empty}
    budget_config = {
        "hitter_category_weights": {"R": 0.5, "OBP": 0.25, "SLG": 0.25},
        "pa_weights": {"default": 600},
    }
    # No crash + returns a dict keyed by every category.
    out = _compute_dollars_per_z_proxy(pool, pools, budget_config)
    assert set(out.keys()) >= {"R", "OBP", "SLG"}


def test_settle_pools_skips_pool_with_no_snapshot():
    """A pool that never produced a best snapshot (converged on iter 0)
    must be silently skipped by _settle_pools."""
    from mtbl_valuations.engine.iteration import _settle_pools

    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.rostered_players = [_make_hitter("a", runs=10.0)]
    histories = {
        "SS": {
            "best_snapshot": None,  # no snapshot ever taken
            "oscillating": False,
            "naturally_converged": True,
        }
    }
    # Must not raise even though no restore is possible.
    _settle_pools({"SS": pool}, histories, converged=True, per_position=True)


def test_swap_pass_positions_is_deterministic_tuple():
    """``_SWAP_PASS_POSITIONS`` must be an ordered tuple so the swap loop
    visits pools in the same order across Python invocations."""
    from mtbl_valuations.engine.pipeline import _SWAP_PASS_POSITIONS

    assert isinstance(_SWAP_PASS_POSITIONS, tuple)
    assert _SWAP_PASS_POSITIONS == ("C", "1B", "2B", "3B", "SS", "OF", "UTIL")


_HITTER_LS = {"batting_categories": ["R", "HR"], "pitching_categories": []}


def test_compute_thin_cell_floor_skips_empty_pool_and_returns_none():
    """An empty rostered tier is skipped; with too few cells to form a
    distribution the league floor is None (the shift then falls back to
    the plain Sigma raw z <= 0 trigger)."""
    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.rostered_players = []
    pool.replacement_players = []
    pool.below_replacement = []

    assert _compute_thin_cell_floor({"SS": pool}, {}, _HITTER_LS) is None


def test_compute_thin_cell_floor_reads_top_level_z():
    """Rostered players with no per-position valuation fall back to the
    top-level normalized_z; the floor still computes (mean - k*stdev)."""
    p1 = _make_hitter("1", 5)
    p1.valuation.normalized_z = {"R": 2.0, "HR": 1.0}
    p2 = _make_hitter("2", 10)
    p2.valuation.normalized_z = {"R": 1.0, "HR": 0.0}

    pool = PositionPool(position="SS", role="HITTER", roster_slots=2)
    pool.rostered_players = [p1, p2]
    pool.replacement_players = []
    pool.below_replacement = []

    # per-player z: R -> 1.5, HR -> 0.5; mean 1.0, pstdev 0.5; k defaults
    # to 1.0 -> floor = 1.0 - 0.5 = 0.5.
    floor = _compute_thin_cell_floor({"SS": pool}, {}, _HITTER_LS)
    assert floor == 0.5
