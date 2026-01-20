import math

from mtbl_valuations.domain.models import HitterStats, Player, PositionPool, PositionValuation
from mtbl_valuations.engine.pools import (
    _debug,
    _refill_tiers_after_dedupe,
    assign_final_positions,
    build_position_pools,
    dedupe_multi_position_players,
    rebuild_pools_after_assignment,
    rebuild_replacement_tier_on_z,
)


def _make_hitter(player_id: str, pos: str, total_z: float, tier: str) -> Player:
    player = Player(
        id=player_id,
        name=f"Player {player_id}",
        team="T",
        positions=[pos],
        role="HITTER",
        stats=HitterStats(
            pa=10,
            ab=10,
            r=10,
            hr=1,
            rbi=1,
            sbn=0,
            obp=0.3,
            slg=0.4,
        ),
    )
    player.valuation.valuations_by_position[pos] = PositionValuation(
        position=pos,
        normalized_z={},
        total_z=total_z,
        tier=tier,
        position_rank=1,
    )
    return player


def test_build_position_pools_sp_rp_and_no_rostered():
    roster_slots = {"SP": 1, "RP": 1}
    sp_pools = build_position_pools([], roster_slots, 1, "SP", 0.03, 1)
    rp_pools = build_position_pools([], roster_slots, 1, "RP", 0.03, 1)

    assert list(sp_pools.keys()) == ["SP"]
    assert list(rp_pools.keys()) == ["RP"]
    assert sp_pools["SP"].rostered_players == []
    assert rp_pools["RP"].rostered_players == []


def test_rebuild_replacement_tier_on_z_empty():
    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    assert rebuild_replacement_tier_on_z([], pool, 0.03, 1) == []


def test_assign_final_positions_fallbacks():
    p_no_vals = Player(
        id="p0",
        name="NoVals",
        team="T",
        positions=["SS"],
        role="HITTER",
        stats=None,
    )

    p_no_rostered = _make_hitter("p1", "SS", 1.0, "REPLACEMENT")
    p_no_rostered.valuation.valuations_by_position["2B"] = PositionValuation(
        position="2B",
        normalized_z={},
        total_z=2.0,
        tier="BELOW_REPLACEMENT",
        position_rank=2,
    )

    p_nan = _make_hitter("p2", "OF", math.nan, "REPLACEMENT")

    players, changes = assign_final_positions({}, [p_no_vals, p_no_rostered, p_nan])
    assert players is not None
    assert changes == 2
    assert p_no_rostered.valuation.primary_position == "2B"
    assert p_nan.valuation.primary_position == "OF"


def test_rebuild_pools_after_assignment_debug(monkeypatch, capsys):
    monkeypatch.setenv("MTBL_DEBUG_POOLS", "1")

    player = _make_hitter("p3", "SS", 1.0, "ROSTERED")
    player.valuation.primary_position = "2B"
    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.rostered_players = [player]

    rebuild_pools_after_assignment({"SS": pool})

    captured = capsys.readouterr()
    assert "rebuild_pools_after_assignment" in captured.out


def test_dedupe_multi_position_players_debug_and_empty_vals(monkeypatch, capsys):
    monkeypatch.setenv("MTBL_DEBUG_POOLS", "1")

    empty_vals = Player(
        id="p4",
        name="EmptyVals",
        team="T",
        positions=["SS"],
        role="HITTER",
        stats=None,
    )
    with_vals = _make_hitter("p5", "SS", 3.0, "ROSTERED")
    with_vals.valuation.valuations_by_position["OF"] = PositionValuation(
        position="OF",
        normalized_z={},
        total_z=2.0,
        tier="ROSTERED",
        position_rank=2,
    )

    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.rostered_players = [empty_vals, with_vals]
    pool.replacement_players = []
    pool.below_replacement = []

    dedupe_multi_position_players({"SS": pool}, 0.03, 1)

    captured = capsys.readouterr()
    assert "dedupe_multi_position_players" in captured.out


def test_refill_tiers_after_dedupe_expands_replacement_debug(monkeypatch, capsys):
    monkeypatch.setenv("MTBL_DEBUG_POOLS", "1")

    p1 = _make_hitter("p6", "SS", 10.0, "ROSTERED")
    p2 = Player(
        id="p7",
        name="MissingValuation",
        team="T",
        positions=["SS"],
        role="HITTER",
        stats=None,
    )
    p3 = _make_hitter("p8", "SS", 5.0, "REPLACEMENT")

    pool = PositionPool(position="SS", role="HITTER", roster_slots=0)
    pool.rostered_players = [p1]
    pool.replacement_players = [p2]
    pool.below_replacement = [p3]

    pools = {"SS": pool}
    target_sizes = {"SS": 2}

    updated = _refill_tiers_after_dedupe(pools, target_sizes, 0.03, 1)

    assert len(updated["SS"].replacement_players) == 2
    captured = capsys.readouterr()
    assert "refill_tiers_after_dedupe" in captured.out


def test_refill_tiers_after_dedupe_skips_seen_ids():
    p1 = _make_hitter("p9", "SS", 10.0, "ROSTERED")
    p2 = _make_hitter("p10", "SS", 5.0, "REPLACEMENT")
    p3 = _make_hitter("p11", "SS", 4.0, "BELOW_REPLACEMENT")

    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.rostered_players = [p1]
    pool.replacement_players = []
    pool.below_replacement = [p2, p3]

    pools = {"SS": pool}
    target_sizes = {"SS": 2}

    updated = _refill_tiers_after_dedupe(pools, target_sizes, 0.03, 1)

    assert len(updated["SS"].replacement_players) == 2


def test_debug_function_prints(monkeypatch, capsys):
    monkeypatch.setenv("MTBL_DEBUG_POOLS", "1")
    _debug("hello")
    captured = capsys.readouterr()
    assert "hello" in captured.out
