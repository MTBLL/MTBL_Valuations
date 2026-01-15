"""Tests for position pool building and assignment functions."""

from mtbl_valuations.domain.models import (
    HitterStats,
    Player,
    PositionPool,
    PositionValuation,
)
from mtbl_valuations.engine.pools import (
    assign_final_positions,
    build_position_pools,
    rebuild_pools_after_assignment,
)


def test_build_position_pools_multi_eligible():
    """Test that players appear in ALL eligible positions with use_eligibility=True."""
    # Create a multi-position player
    multi_pos_player = Player(
        id="multi1",
        name="Multi Position Guy",
        team="TST",
        positions=["SS", "2B", "3B"],
        role="HITTER",
        stats=HitterStats(
            pa=600, ab=550, r=80, hr=25, rbi=90, sbn=10, obp=0.350, slg=0.500
        ),
    )

    # Create single-position players for each position
    ss_only = Player(
        id="ss1",
        name="SS Only",
        team="TST",
        positions=["SS"],
        role="HITTER",
        stats=HitterStats(
            pa=600, ab=550, r=70, hr=15, rbi=60, sbn=20, obp=0.320, slg=0.420
        ),
    )

    second_only = Player(
        id="2b1",
        name="2B Only",
        team="TST",
        positions=["2B"],
        role="HITTER",
        stats=HitterStats(
            pa=600, ab=550, r=75, hr=18, rbi=65, sbn=15, obp=0.330, slg=0.440
        ),
    )

    third_only = Player(
        id="3b1",
        name="3B Only",
        team="TST",
        positions=["3B"],
        role="HITTER",
        stats=HitterStats(
            pa=600, ab=550, r=85, hr=30, rbi=100, sbn=5, obp=0.360, slg=0.520
        ),
    )

    players = [multi_pos_player, ss_only, second_only, third_only]

    roster_slots = {"SS": 1, "2B": 1, "3B": 1}
    budget_config = {"replacement_tier_pct": 0.03, "min_replacement_tier_size": 1}

    # Build pools with multi-eligibility
    pools = build_position_pools(
        players,
        roster_slots,
        num_teams=1,
        role="HITTER",
        budget_config=budget_config,
        use_eligibility=True,
    )

    # Find pools by position
    pool_by_pos = {p.position: p for p in pools}

    # Multi-position player should appear in SS, 2B, and 3B pools
    ss_pool = pool_by_pos["SS"]
    second_pool = pool_by_pos["2B"]
    third_pool = pool_by_pos["3B"]

    ss_ids = {p.id for p in ss_pool.rostered_players + ss_pool.replacement_players}
    second_ids = {
        p.id for p in second_pool.rostered_players + second_pool.replacement_players
    }
    third_ids = {
        p.id for p in third_pool.rostered_players + third_pool.replacement_players
    }

    assert "multi1" in ss_ids, "Multi-position player should be in SS pool"
    assert "multi1" in second_ids, "Multi-position player should be in 2B pool"
    assert "multi1" in third_ids, "Multi-position player should be in 3B pool"


def test_build_position_pools_primary_position_mode():
    """Test that only primary-position players are included with use_eligibility=False."""
    # Create players with primary positions set
    ss_player = Player(
        id="ss1",
        name="SS Primary",
        team="TST",
        positions=["SS", "2B"],
        role="HITTER",
        stats=HitterStats(
            pa=600, ab=550, r=80, hr=20, rbi=70, sbn=15, obp=0.340, slg=0.460
        ),
    )
    ss_player.computed.primary_position = "SS"

    second_player = Player(
        id="2b1",
        name="2B Primary",
        team="TST",
        positions=["2B", "SS"],
        role="HITTER",
        stats=HitterStats(
            pa=600, ab=550, r=75, hr=18, rbi=65, sbn=12, obp=0.330, slg=0.440
        ),
    )
    second_player.computed.primary_position = "2B"

    players = [ss_player, second_player]
    roster_slots = {"SS": 1, "2B": 1}
    budget_config = {"replacement_tier_pct": 0.03, "min_replacement_tier_size": 1}

    # Build pools with primary_position mode (post-assignment)
    pools = build_position_pools(
        players,
        roster_slots,
        num_teams=1,
        role="HITTER",
        budget_config=budget_config,
        use_eligibility=False,
    )

    pool_by_pos = {p.position: p for p in pools}

    ss_pool = pool_by_pos["SS"]
    second_pool = pool_by_pos["2B"]

    # Each player should only be in their primary position pool
    ss_ids = {p.id for p in ss_pool.rostered_players + ss_pool.replacement_players}
    second_ids = {
        p.id for p in second_pool.rostered_players + second_pool.replacement_players
    }

    assert "ss1" in ss_ids
    assert "ss1" not in second_ids  # Even though eligible for 2B
    assert "2b1" in second_ids
    assert "2b1" not in ss_ids  # Even though eligible for SS


def test_assign_final_positions_chooses_highest_z():
    """Test that players are assigned to the position with highest total_z.

    We use total_z (not total_dollars) because dollar values aren't available
    until all pools have fully stabilized including UTIL.
    """
    player = Player(
        id="test1",
        name="Test Player",
        team="TST",
        positions=["SS", "2B"],
        role="HITTER",
        stats=HitterStats(
            pa=600, ab=550, r=80, hr=20, rbi=70, sbn=15, obp=0.340, slg=0.460
        ),
    )

    # Set up valuations where 2B has higher total_z than SS
    player.computed.valuations_by_position["SS"] = PositionValuation(
        position="SS",
        raw_z={"R": 1.0},
        normalized_z={"R": 0.5},
        dollar_values={"R": 5.0},
        total_z=0.5,  # Lower Z
        total_dollars=15.0,
        tier="ROSTERED",
    )

    player.computed.valuations_by_position["2B"] = PositionValuation(
        position="2B",
        raw_z={"R": 1.2},
        normalized_z={"R": 0.7},
        dollar_values={"R": 7.0},
        total_z=0.7,  # Higher Z
        total_dollars=25.0,
        tier="ROSTERED",
    )

    # Create empty pools (not used but required by API)
    pools: list[PositionPool] = []

    _, changes = assign_final_positions(pools, [player])

    assert changes == 1  # Position changed from "" to "2B"
    assert player.computed.primary_position == "2B"


def test_assign_final_positions_prefers_rostered_tier():
    """Test that assignment prefers rostered tier even if replacement has higher total_z."""
    player = Player(
        id="test1",
        name="Test Player",
        team="TST",
        positions=["SS", "2B"],
        role="HITTER",
        stats=HitterStats(
            pa=600, ab=550, r=80, hr=20, rbi=70, sbn=15, obp=0.340, slg=0.460
        ),
    )

    # SS: Rostered but lower total_z
    player.computed.valuations_by_position["SS"] = PositionValuation(
        position="SS",
        raw_z={"R": 1.0},
        normalized_z={"R": 0.5},
        dollar_values={"R": 5.0},
        total_z=0.5,  # Lower Z
        total_dollars=15.0,
        tier="ROSTERED",  # Rostered
    )

    # 2B: Replacement but higher total_z
    player.computed.valuations_by_position["2B"] = PositionValuation(
        position="2B",
        raw_z={"R": 1.5},
        normalized_z={"R": 1.0},
        dollar_values={"R": 10.0},
        total_z=1.0,  # Higher Z
        total_dollars=30.0,
        tier="REPLACEMENT",  # But only replacement level
    )

    pools: list[PositionPool] = []
    assign_final_positions(pools, [player])

    # Should choose SS because player is ROSTERED there (tier takes priority over Z)
    assert player.computed.primary_position == "SS"


def test_rebuild_pools_after_assignment():
    """Test that rebuild removes players from non-primary pools."""
    # Create two players
    player1 = Player(
        id="p1",
        name="Player 1",
        team="TST",
        positions=["SS"],
        role="HITTER",
    )
    player1.computed.primary_position = "SS"

    player2 = Player(
        id="p2",
        name="Player 2",
        team="TST",
        positions=["SS", "2B"],
        role="HITTER",
    )
    player2.computed.primary_position = "2B"  # Assigned to 2B, not SS

    # Create pools where player2 appears in both (pre-cleanup state)
    ss_pool = PositionPool(
        position="SS",
        role="HITTER",
        roster_slots=1,
        rostered_players=[player1, player2],  # player2 shouldn't be here
    )

    second_pool = PositionPool(
        position="2B",
        role="HITTER",
        roster_slots=1,
        rostered_players=[player2],
    )

    pools = rebuild_pools_after_assignment([ss_pool, second_pool])

    # After rebuild, player2 should only be in 2B pool
    ss_ids = {p.id for p in pools[0].rostered_players}
    second_ids = {p.id for p in pools[1].rostered_players}

    assert "p1" in ss_ids
    assert "p2" not in ss_ids  # Removed from SS
    assert "p2" in second_ids  # Still in 2B
