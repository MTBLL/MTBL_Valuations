"""Tests for position pool building and assignment functions."""

from mtbl_valuations.domain.models import (
    HitterStats,
    Player,
    PositionPool,
    PositionValuation,
)
from mtbl_valuations.engine.pools import (
    _calc_replacement_threshold,
    assign_final_positions,
    build_position_pools,
    build_util_pool,
    dedupe_multi_position_players,
    rebuild_pools_after_assignment,
    rebuild_replacement_tier_on_z,
)
from mtbl_valuations.engine.valuation import get_composite_metric


def _make_test_player(
    id: str,
    name: str | None = None,
    positions: list[str] | None = None,
    wrc_plus: float = 100.0,
    total_z: float | None = None,
) -> Player:
    """Create a test player with controlled attributes.

    Args:
        id: Player ID.
        name: Player name (defaults to "Player {id}").
        positions: List of eligible positions (defaults to ["SS"]).
        wrc_plus: wRC+ value for composite metric sorting.
        total_z: If provided, sets player.computed.total_z.
    """
    player = Player(
        id=id,
        name=name or f"Player {id}",
        team="TST",
        positions=positions or ["SS"],
        role="HITTER",
        stats=HitterStats(
            pa=600,
            ab=550,
            r=80,
            hr=20,
            rbi=70,
            sbn=10,
            obp=0.340,
            slg=0.460,
            wrc_plus=wrc_plus,
        ),
    )
    if total_z is not None:
        player.computed.total_z = total_z
    return player


class TestBuildPositionPools:
    def test_build_position_pools_multi_eligible(self):
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
        pool_by_pos = {p.position: p for p in pools.values()}

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

    def test_build_position_pools_primary_position_mode(self):
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

        pool_by_pos = {p.position: p for p in pools.values()}

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

    def test_build_position_pools_real_data(
        self, regular_hitter_pools, players_from_hitters
    ):
        hitter_pools = regular_hitter_pools

        ss_pool = regular_hitter_pools["SS"]
        of_pool = regular_hitter_pools["OF"]
        assert hitter_pools is not None
        mookie = next(p for p in players_from_hitters if p.name == "Mookie Betts")
        assert mookie in ss_pool.rostered_players and mookie in of_pool.rostered_players


class TestBuildUtilPool:
    """Tests for build_util_pool function."""

    def test_build_util_pool_collects_replacement_and_below(self):
        """Test that UTIL pool collects replacement-tier and below-replacement players."""
        # Create SS pool players
        ss_rostered_1 = _make_test_player(
            "ss_r1", "SS Rostered 1", ["SS"], wrc_plus=140.0
        )
        ss_rostered_2 = _make_test_player(
            "ss_r2", "SS Rostered 2", ["SS"], wrc_plus=130.0
        )
        ss_replacement = _make_test_player(
            "ss_rep", "SS Replacement", ["SS"], wrc_plus=100.0
        )
        ss_below = _make_test_player("ss_below", "SS Below", ["SS"], wrc_plus=85.0)

        # Create 2B pool players
        second_rostered = _make_test_player(
            "2b_r1", "2B Rostered", ["2B"], wrc_plus=135.0
        )
        second_replacement = _make_test_player(
            "2b_rep", "2B Replacement", ["2B"], wrc_plus=105.0
        )
        second_below = _make_test_player("2b_below", "2B Below", ["2B"], wrc_plus=80.0)

        # Create pure DH players (only eligible for DH/UTIL)
        dh_star = _make_test_player("dh1", "DH Star", ["DH"], wrc_plus=150.0)
        dh_avg = _make_test_player("dh2", "DH Average", ["DH"], wrc_plus=110.0)
        dh_weak = _make_test_player("dh3", "DH Weak", ["DH"], wrc_plus=90.0)

        # Build position pools with pre-assigned tiers
        ss_pool = PositionPool(
            position="SS",
            role="HITTER",
            roster_slots=2,
            rostered_players=[ss_rostered_1, ss_rostered_2],
            replacement_players=[ss_replacement],
            below_replacement=[ss_below],
        )

        second_pool = PositionPool(
            position="2B",
            role="HITTER",
            roster_slots=1,
            rostered_players=[second_rostered],
            replacement_players=[second_replacement],
            below_replacement=[second_below],
        )

        hitter_pools = {"SS": ss_pool, "2B": second_pool}
        pure_dh_players = [dh_star, dh_avg, dh_weak]
        roster_slots = {"SS": 2, "2B": 1, "UTIL": 2}
        budget_config = {"replacement_tier_pct": 0.03, "min_replacement_tier_size": 1}

        # Build UTIL pool
        util_pool = build_util_pool(
            hitter_pools,
            pure_dh_players,
            roster_slots,
            num_teams=1,
            budget_config=budget_config,
        )

        # Verify UTIL pool structure
        assert util_pool.position == "UTIL"
        assert util_pool.role == "HITTER"
        assert util_pool.roster_slots == 2  # 2 UTIL slots * 1 team

        # Collect all player IDs in UTIL pool
        util_all_ids = {
            p.id
            for p in util_pool.rostered_players
            + util_pool.replacement_players
            + util_pool.below_replacement
        }

        # Replacement and below-replacement from position pools should be in UTIL
        assert "ss_rep" in util_all_ids, "SS replacement should be in UTIL pool"
        assert "ss_below" in util_all_ids, "SS below-replacement should be in UTIL pool"
        assert "2b_rep" in util_all_ids, "2B replacement should be in UTIL pool"
        assert "2b_below" in util_all_ids, "2B below-replacement should be in UTIL pool"

        # Pure DH players should be in UTIL
        assert "dh1" in util_all_ids, "Pure DH star should be in UTIL pool"
        assert "dh2" in util_all_ids, "Pure DH average should be in UTIL pool"
        assert "dh3" in util_all_ids, "Pure DH weak should be in UTIL pool"

        # Rostered players from position pools should NOT be in UTIL
        assert "ss_r1" not in util_all_ids, "SS rostered should NOT be in UTIL pool"
        assert "ss_r2" not in util_all_ids, "SS rostered should NOT be in UTIL pool"
        assert "2b_r1" not in util_all_ids, "2B rostered should NOT be in UTIL pool"

    def test_build_util_pool_sorts_by_wrc_plus(self):
        """Test that UTIL pool is sorted by wrc_plus (composite metric)."""
        # Create players with varied wrc_plus
        p_high = _make_test_player("p_high", "High wRC+", ["DH"], wrc_plus=150.0)
        p_mid = _make_test_player("p_mid", "Mid wRC+", ["SS"], wrc_plus=110.0)
        p_low = _make_test_player("p_low", "Low wRC+", ["2B"], wrc_plus=90.0)

        # Position pool with replacement players
        ss_pool = PositionPool(
            position="SS",
            role="HITTER",
            roster_slots=1,
            rostered_players=[],
            replacement_players=[p_mid],
            below_replacement=[],
        )

        second_pool = PositionPool(
            position="2B",
            role="HITTER",
            roster_slots=1,
            rostered_players=[],
            replacement_players=[p_low],
            below_replacement=[],
        )

        hitter_pools = {"SS": ss_pool, "2B": second_pool}
        pure_dh_players = [p_high]
        roster_slots = {"UTIL": 2}
        budget_config = {"replacement_tier_pct": 0.03, "min_replacement_tier_size": 1}

        util_pool = build_util_pool(
            hitter_pools,
            pure_dh_players,
            roster_slots,
            num_teams=1,
            budget_config=budget_config,
        )

        # Top 2 by wrc_plus should be rostered
        rostered_ids = [p.id for p in util_pool.rostered_players]
        assert rostered_ids == ["p_high", "p_mid"], "Should be sorted by wrc_plus desc"

    def test_build_util_pool_deduplicates_players(self):
        """Test that players appearing in multiple pools are only counted once in UTIL."""
        # Multi-eligible player in replacement tier of both pools
        multi_pos = _make_test_player(
            "multi", "Multi Position", ["SS", "2B"], wrc_plus=100.0
        )

        ss_pool = PositionPool(
            position="SS",
            role="HITTER",
            roster_slots=1,
            rostered_players=[],
            replacement_players=[multi_pos],
            below_replacement=[],
        )

        second_pool = PositionPool(
            position="2B",
            role="HITTER",
            roster_slots=1,
            rostered_players=[],
            replacement_players=[multi_pos],  # Same player object
            below_replacement=[],
        )

        hitter_pools = {"SS": ss_pool, "2B": second_pool}
        roster_slots = {"UTIL": 2}
        budget_config = {"replacement_tier_pct": 0.03, "min_replacement_tier_size": 1}

        util_pool = build_util_pool(
            hitter_pools,
            pure_dh_players=[],
            roster_slots=roster_slots,
            num_teams=1,
            budget_config=budget_config,
        )

        # Player should only appear once
        all_util_players = (
            util_pool.rostered_players
            + util_pool.replacement_players
            + util_pool.below_replacement
        )
        assert len(all_util_players) == 1
        assert all_util_players[0].id == "multi"


class TestAssignFinalPositions:
    def test_assign_final_positions_prefers_rostered_tier(self):
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
            normalized_z={"R": 0.5},
            dollar_values={"R": 5.0},
            total_z=0.5,  # Lower Z
            total_dollars=15.0,
            tier="ROSTERED",  # Rostered
            position_rank=5,
        )

        # 2B: Replacement but higher total_z
        player.computed.valuations_by_position["2B"] = PositionValuation(
            position="2B",
            normalized_z={"R": 1.0},
            dollar_values={"R": 10.0},
            total_z=1.0,  # Higher Z
            total_dollars=30.0,
            tier="REPLACEMENT",  # But only replacement level
            position_rank=15,
        )

        pools: dict[str, PositionPool] = {}
        assign_final_positions(pools, [player])

        # Should choose SS because player is ROSTERED there (tier takes priority over Z)
        assert player.computed.primary_position == "SS"

    def test_assign_final_positions_chooses_highest_z(self):
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
            normalized_z={"R": 0.5},
            dollar_values={"R": 5.0},
            total_z=0.5,  # Lower Z
            total_dollars=15.0,
            tier="ROSTERED",
            position_rank=3,
        )

        player.computed.valuations_by_position["2B"] = PositionValuation(
            position="2B",
            normalized_z={"R": 0.7},
            dollar_values={"R": 7.0},
            total_z=0.7,  # Higher Z
            total_dollars=25.0,
            tier="ROSTERED",
            position_rank=2,
        )

        # Create empty pools (not used but required by API)
        pools: dict[str, PositionPool] = {}

        _, changes = assign_final_positions(pools, [player])

        assert changes == 1  # Position changed from "" to "2B"
        assert player.computed.primary_position == "2B"


class TestRebuildPools:
    def test_rebuild_pools_after_assignment(self):
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
        ss_pool = {
            "SS": PositionPool(
                position="SS",
                role="HITTER",
                roster_slots=1,
                rostered_players=[player1, player2],  # player2 shouldn't be here
            )
        }

        second_pool = {
            "2B": PositionPool(
                position="2B",
                role="HITTER",
                roster_slots=1,
                rostered_players=[player2],
            )
        }

        pools = rebuild_pools_after_assignment(ss_pool | second_pool)

        # After rebuild, player2 should only be in 2B pool
        ss_ids = {p.id for p in pools["SS"].rostered_players}
        second_ids = {p.id for p in pools["2B"].rostered_players}

        assert "p1" in ss_ids
        assert "p2" not in ss_ids  # Removed from SS
        assert "p2" in second_ids  # Still in 2B


class TestDedupeMultiPositionPlayers:
    """Tests for dedupe_multi_position_players function."""

    def test_assigns_to_best_ranked_position(self):
        """Player ranked #2 at SS and #4 at OF should be assigned to SS."""
        # Create a multi-position player
        player = _make_test_player("multi", "Multi Pos", ["SS", "OF"])

        # Set up valuations with position_rank
        player.computed.valuations_by_position["SS"] = PositionValuation(
            position="SS",
            normalized_z={},
            dollar_values={},
            total_z=1.5,
            total_dollars=0.0,
            tier="ROSTERED",
            position_rank=2,  # Ranked #2 at SS
        )
        player.computed.valuations_by_position["OF"] = PositionValuation(
            position="OF",
            normalized_z={},
            dollar_values={},
            total_z=1.8,  # Higher Z but worse rank
            total_dollars=0.0,
            tier="ROSTERED",
            position_rank=4,  # Ranked #4 at OF
        )

        # Create pools with the player in both
        ss_pool = PositionPool(
            position="SS",
            role="HITTER",
            roster_slots=5,
            rostered_players=[player],
        )
        of_pool = PositionPool(
            position="OF",
            role="HITTER",
            roster_slots=10,
            rostered_players=[player],
        )

        pools = {"SS": ss_pool, "OF": of_pool}

        result_pools, changes = dedupe_multi_position_players(pools)

        # Should assign to SS (better rank) despite OF having higher Z
        assert player.computed.primary_position == "SS"
        assert changes == 1

        # Player should only be in SS pool now
        assert player in result_pools["SS"].rostered_players
        assert player not in result_pools["OF"].rostered_players

    def test_single_position_player_unchanged(self):
        """Single-position player should be assigned to their only position."""
        player = _make_test_player("single", "Single Pos", ["SS"])

        player.computed.valuations_by_position["SS"] = PositionValuation(
            position="SS",
            normalized_z={},
            dollar_values={},
            total_z=1.0,
            total_dollars=0.0,
            tier="ROSTERED",
            position_rank=3,
        )

        ss_pool = PositionPool(
            position="SS",
            role="HITTER",
            roster_slots=5,
            rostered_players=[player],
        )

        pools = {"SS": ss_pool}

        result_pools, changes = dedupe_multi_position_players(pools)

        assert player.computed.primary_position == "SS"
        # First assignment counts as a change (from "" to "SS")
        assert changes == 1
        assert player in result_pools["SS"].rostered_players

    def test_multiple_players_deduped(self):
        """Multiple multi-position players should each be assigned correctly."""
        # Player A: rank 1 at SS, rank 3 at 2B -> assign to SS
        player_a = _make_test_player("a", "Player A", ["SS", "2B"])
        player_a.computed.valuations_by_position["SS"] = PositionValuation(
            position="SS",
            normalized_z={},
            dollar_values={},
            total_z=2.0,
            total_dollars=0.0,
            tier="ROSTERED",
            position_rank=1,
        )
        player_a.computed.valuations_by_position["2B"] = PositionValuation(
            position="2B",
            normalized_z={},
            dollar_values={},
            total_z=1.8,
            total_dollars=0.0,
            tier="ROSTERED",
            position_rank=3,
        )

        # Player B: rank 2 at SS, rank 1 at 2B -> assign to 2B
        player_b = _make_test_player("b", "Player B", ["SS", "2B"])
        player_b.computed.valuations_by_position["SS"] = PositionValuation(
            position="SS",
            normalized_z={},
            dollar_values={},
            total_z=1.9,
            total_dollars=0.0,
            tier="ROSTERED",
            position_rank=2,
        )
        player_b.computed.valuations_by_position["2B"] = PositionValuation(
            position="2B",
            normalized_z={},
            dollar_values={},
            total_z=2.1,
            total_dollars=0.0,
            tier="ROSTERED",
            position_rank=1,
        )

        ss_pool = PositionPool(
            position="SS",
            role="HITTER",
            roster_slots=5,
            rostered_players=[player_a, player_b],
        )
        second_pool = PositionPool(
            position="2B",
            role="HITTER",
            roster_slots=5,
            rostered_players=[player_a, player_b],
        )

        pools = {"SS": ss_pool, "2B": second_pool}

        result_pools, changes = dedupe_multi_position_players(pools)

        assert player_a.computed.primary_position == "SS"
        assert player_b.computed.primary_position == "2B"
        assert changes == 2

        # Each player in their assigned pool only
        assert player_a in result_pools["SS"].rostered_players
        assert player_a not in result_pools["2B"].rostered_players
        assert player_b in result_pools["2B"].rostered_players
        assert player_b not in result_pools["SS"].rostered_players


class TestRebuildReplacementTier:
    """Tests for rebuild_replacement_tier function."""

    def test_threshold_filtering(self):
        """Players within threshold % of last rostered qualify."""
        # Setup: last rostered has total_z=10.0, threshold at 3% = 9.7
        p1 = _make_test_player("p1", total_z=12.0)  # rostered
        p2 = _make_test_player("p2", total_z=10.0)  # rostered (last)
        p3 = _make_test_player("p3", total_z=9.8)  # >= 9.7, qualifies
        p4 = _make_test_player("p4", total_z=9.7)  # == 9.7, qualifies
        p5 = _make_test_player("p5", total_z=9.6)  # < 9.7, does NOT qualify
        p6 = _make_test_player("p6", total_z=5.0)  # far below

        pool = PositionPool(
            position="SS",
            role="HITTER",
            roster_slots=2,
            rostered_players=[p1, p2],
        )

        all_players = [p1, p2, p3, p4, p5, p6]  # already sorted desc
        budget_config = {"replacement_tier_pct": 0.03, "min_replacement_tier_size": 1}

        result = rebuild_replacement_tier_on_z(all_players, pool, budget_config)

        assert len(result) == 2
        assert result[0].id == "p3"
        assert result[1].id == "p4"

    def test_minimum_tier_size_enforced(self):
        """When fewer players qualify by threshold, enforce minimum size."""
        p1 = _make_test_player("p1", total_z=10.0)  # rostered
        p2 = _make_test_player("p2", total_z=8.0)  # rostered (last)
        p3 = _make_test_player("p3", total_z=5.0)  # below threshold (7.76)
        p4 = _make_test_player("p4", total_z=4.0)  # below threshold
        p5 = _make_test_player("p5", total_z=3.0)  # below threshold

        pool = PositionPool(
            position="SS",
            role="HITTER",
            roster_slots=2,
            rostered_players=[p1, p2],
        )

        all_players = [p1, p2, p3, p4, p5]
        budget_config = {"replacement_tier_pct": 0.03, "min_replacement_tier_size": 3}

        result = rebuild_replacement_tier_on_z(all_players, pool, budget_config)

        # None qualify by threshold, but min_size=3 forces inclusion
        assert len(result) == 3
        assert [p.id for p in result] == ["p3", "p4", "p5"]

    def test_empty_rostered_returns_empty(self):
        """Returns empty list when no rostered players exist."""
        pool = PositionPool(
            position="SS",
            role="HITTER",
            roster_slots=2,
            rostered_players=[],
        )

        result = rebuild_replacement_tier_on_z(
            [], pool, {"replacement_tier_pct": 0.03, "min_replacement_tier_size": 3}
        )

        assert result == []

    def test_use_per_pool_z_flag(self):
        """When use_per_pool_z=True, reads from valuations_by_position."""
        p1 = _make_test_player("p1", total_z=0.0)  # global total_z ignored
        p2 = _make_test_player("p2", total_z=0.0)
        p3 = _make_test_player("p3", total_z=0.0)

        # Set per-pool Z values
        p1.computed.valuations_by_position["SS"] = PositionValuation(
            position="SS",
            normalized_z={},
            dollar_values={},
            total_z=10.0,
            total_dollars=0.0,
            tier="ROSTERED",
            position_rank=0,
        )
        p2.computed.valuations_by_position["SS"] = PositionValuation(
            position="SS",
            normalized_z={},
            dollar_values={},
            total_z=8.0,
            total_dollars=0.0,
            tier="ROSTERED",
            position_rank=1,
        )
        p3.computed.valuations_by_position["SS"] = PositionValuation(
            position="SS",
            normalized_z={},
            dollar_values={},
            total_z=7.8,
            total_dollars=0.0,
            tier="REPLACEMENT",
            position_rank=2,
        )

        pool = PositionPool(
            position="SS",
            role="HITTER",
            roster_slots=2,
            rostered_players=[p1, p2],
        )

        all_players = [p1, p2, p3]
        budget_config = {"replacement_tier_pct": 0.03, "min_replacement_tier_size": 1}

        result = rebuild_replacement_tier_on_z(
            all_players, pool, budget_config, use_per_pool_z=True
        )

        # 7.8 >= 8.0 * 0.97 = 7.76, so p3 qualifies
        assert len(result) == 1
        assert result[0].id == "p3"


class TestCalcReplacementThreshold:
    def test_calc_replacement_threshold_hitters(self, regular_hitter_pools):
        ss_pool: PositionPool = regular_hitter_pools["SS"]
        last_rostered_metric = get_composite_metric(ss_pool.rostered_players[-1])
        result = _calc_replacement_threshold(last_rostered_metric, 0.03)

        assert result == last_rostered_metric * (1 - 0.03)
