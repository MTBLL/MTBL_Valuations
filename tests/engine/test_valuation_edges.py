import math
from types import SimpleNamespace

from mtbl_valuations.domain.models import (
    HitterStats,
    PitcherStats,
    Player,
    PositionPool,
    PositionValuation,
)
from mtbl_valuations.engine.valuation import (
    valuate_unqualified_players,
    _get_categories,
    calc_means,
    calc_stdevs,
    calc_z_scores_for_archetype,
    compute_shadow_valuations,
    distribute_pool_dollars,
    finalize_tiers_by_dollars,
    get_categories,
    get_composite_metric,
    get_player_stat,
    is_inverted,
    resolve_primary_by_best_dollars,
)


class Dummy:
    def __init__(self, stats):
        self.stats = stats


class StatsWithDict:
    def __init__(self, metrics):
        self.metrics = metrics


class StatsWithAttrs:
    def __init__(self, foo, bar):
        self.foo = foo
        self.bar = bar


class StatsEmpty:
    pass


def test_is_inverted_and_get_categories():
    league_settings = {
        "batting_categories": ["R", "HR"],
        "pitching_categories": ["OUTS", "ERA", "WHIP", "K/9", "QS", "SVHD"],
    }

    assert is_inverted("ERA") is True
    assert is_inverted("R") is False
    assert get_categories("SP", league_settings) == ["IP", "ERA", "WHIP", "K/9", "QS"]
    assert get_categories("RP", league_settings) == ["IP", "ERA", "WHIP", "K/9", "SVHD"]


def test_calc_means_variants():
    assert calc_means([], "anything") == {}
    assert calc_means([object()], "anything") == {}

    dict_players = [Dummy({"R": 10.0, "HR": 2.0}), Dummy({"R": 20.0, "HR": 4.0})]
    means = calc_means(dict_players, "ignored")
    assert means["R"] == 15.0
    assert means["HR"] == 3.0

    dict_attr_players = [
        Dummy(StatsWithDict({"A": 1.0, "B": 3.0})),
        Dummy(StatsWithDict({"A": 2.0, "B": 5.0})),
    ]
    means = calc_means(dict_attr_players, "metrics")
    assert means["A"] == 1.5
    assert means["B"] == 4.0

    assert calc_means([Dummy(StatsWithDict(5.0))], "metrics") == {}

    attr_players = [Dummy(StatsWithAttrs(1.0, 3.0)), Dummy(StatsWithAttrs(5.0, 7.0))]
    means = calc_means(attr_players, "missing")
    assert means["foo"] == 3.0
    assert means["bar"] == 5.0

    mixed_players = [Dummy(StatsWithAttrs(1.0, 3.0)), Dummy(StatsEmpty())]
    means = calc_means(mixed_players, "missing")
    assert means["foo"] == 0.5


def test_calc_stdevs_variants():
    assert calc_stdevs([], "anything") == {}
    assert calc_stdevs([object()], "anything") == {}

    one_player = [Dummy({"R": 10.0})]
    stdevs = calc_stdevs(one_player, "ignored")
    assert stdevs["R"] == 0.0

    two_players = [Dummy({"R": 10.0}), Dummy({"R": 14.0})]
    stdevs = calc_stdevs(two_players, "ignored")
    assert stdevs["R"] == 2.0

    mixed_players = [Dummy(StatsWithAttrs(1.0, 3.0)), Dummy(StatsEmpty())]
    stdevs = calc_stdevs(mixed_players, "missing")
    assert stdevs["foo"] >= 0.0

    dict_attr_players = [
        Dummy(StatsWithDict({"A": 1.0})),
        Dummy(StatsWithDict({"A": 3.0})),
    ]
    stdevs = calc_stdevs(dict_attr_players, "metrics")
    assert stdevs["A"] == 1.0


def test_get_player_stat_and_composite_metric():
    assert get_player_stat(object(), "R") == 0.0

    pitcher = Player(
        id="p1",
        name="Pitcher",
        team="T",
        positions=["SP"],
        role="SP",
        stats=PitcherStats(outs=9, era=3.0, whip=1.1, k9=9.0, qs=1, svhd=0),
    )
    assert get_player_stat(pitcher, "IP") == 3.0

    assert get_composite_metric(object()) == 0.0
    assert get_composite_metric(SimpleNamespace(stats=object())) == 0.0


def test_calc_z_scores_for_archetype():
    players = [
        Player(
            id="p1",
            name="P1",
            team="T",
            positions=["SP"],
            role="SP",
            stats=PitcherStats(outs=9, era=3.5, whip=1.0, k9=9.0, qs=1, svhd=0),
        ),
        Player(
            id="p2",
            name="P2",
            team="T",
            positions=["SP"],
            role="SP",
            stats=PitcherStats(outs=9, era=4.5, whip=1.2, k9=8.0, qs=1, svhd=0),
        ),
    ]

    scores = calc_z_scores_for_archetype({"ERA": 3.0}, players)
    assert math.isclose(scores["ERA"], 1.414213562373095, rel_tol=1e-6)

    empty_scores = calc_z_scores_for_archetype({"ERA": 3.0}, [])
    assert empty_scores["ERA"] == 0.0

    hitters = [
        Player(
            id="h1",
            name="H1",
            team="T",
            positions=["SS"],
            role="HITTER",
            stats=HitterStats(
                pa=10,
                ab=10,
                r=10,
                hr=2,
                rbi=3,
                sbn=1,
                obp=0.3,
                slg=0.4,
            ),
        ),
        Player(
            id="h2",
            name="H2",
            team="T",
            positions=["SS"],
            role="HITTER",
            stats=HitterStats(
                pa=10,
                ab=10,
                r=20,
                hr=1,
                rbi=2,
                sbn=0,
                obp=0.3,
                slg=0.4,
            ),
        ),
    ]

    scores = calc_z_scores_for_archetype({"R": 12.0}, hitters)
    assert "R" in scores


def test_get_categories_with_empty_list():
    """Test _get_categories helper with empty player list."""
    result = _get_categories([], "anything", is_stat=True)
    assert result == []


def _make_hitter(pid: str, normalized_z: dict[str, float], pos: str = "SS") -> Player:
    p = Player(
        id=pid,
        name=pid,
        team="T",
        positions=[pos],
        role="HITTER",
        stats=HitterStats(
            pa=600, ab=540, r=80, hr=20, rbi=70, sbn=10, obp=0.340, slg=0.450,
        ),
    )
    p.valuation.primary_position = pos
    p.valuation.normalized_z = dict(normalized_z)
    p.valuation.valuations_by_position[pos] = PositionValuation(
        position=pos,
        normalized_z=dict(normalized_z),
        total_z=sum(normalized_z.values()),
        tier="ROSTERED",
        position_rank=1,
    )
    return p


def test_distribute_pool_dollars_formula_across_tiers():
    """Every tier gets formula-$. Negative BELOW stays BELOW; positive
    BELOW promotes to REPLACEMENT with its formula-$ preserved."""
    rost = _make_hitter("rost", {"R": 2.0, "HR": 1.0})
    rlp = _make_hitter("rlp", {"R": 0.5, "HR": 0.0})
    rlp.valuation.valuations_by_position["SS"].tier = "REPLACEMENT"
    below_neg = _make_hitter("below_neg", {"R": -1.0, "HR": -2.0})
    below_neg.valuation.valuations_by_position["SS"].tier = "BELOW_REPLACEMENT"
    below_pos = _make_hitter("below_pos", {"R": 1.0, "HR": 0.0})
    below_pos.valuation.valuations_by_position["SS"].tier = "BELOW_REPLACEMENT"

    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.category_budgets = {"R": 10.0, "HR": 5.0}
    pool.dollars_per_z = {"R": 5.0, "HR": 5.0}
    pool.rostered_players = [rost]
    pool.replacement_players = [rlp]
    pool.below_replacement = [below_neg, below_pos]

    distribute_pool_dollars({"SS": pool}, store_per_position=True)

    # ROSTERED: formula-$
    assert rost.valuation.total_dollars == 15.0
    # REPLACEMENT: formula-$ (small ±, not pinned)
    assert rlp.valuation.total_dollars == 2.5
    # BELOW negative stays
    assert below_neg.valuation.total_dollars == -15.0
    assert below_neg in pool.below_replacement
    # BELOW positive promoted, formula-$ preserved
    assert below_pos.valuation.tier == "REPLACEMENT"
    assert below_pos.valuation.total_dollars == 5.0
    assert below_pos in pool.replacement_players
    assert below_pos not in pool.below_replacement


def test_resolve_primary_picks_higher_tier_over_higher_dollars():
    """A player ROSTERED in pool A ($5) and REPLACEMENT in pool B ($10)
    must export with primary_position=A, tier=ROSTERED, $5 — losing the
    ROSTERED label would break budget conservation."""
    p = _make_hitter("dual", {"R": 1.0, "HR": 0.0}, pos="SS")
    # SS pool: ROSTERED $5
    p.valuation.valuations_by_position["SS"] = PositionValuation(
        position="SS",
        normalized_z={"R": 1.0, "HR": 0.0},
        total_z=1.0,
        dollar_values={"R": 5.0, "HR": 0.0},
        total_dollars=5.0,
        tier="ROSTERED",
        position_rank=1,
    )
    # UTIL pool: REPLACEMENT $10 (higher raw $)
    p.valuation.valuations_by_position["UTIL"] = PositionValuation(
        position="UTIL",
        normalized_z={"R": 2.0, "HR": 0.0},
        total_z=2.0,
        dollar_values={"R": 10.0, "HR": 0.0},
        total_dollars=10.0,
        tier="REPLACEMENT",
        position_rank=1,
    )

    ss_pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    ss_pool.rostered_players = [p]
    util_pool = PositionPool(position="UTIL", role="HITTER", roster_slots=1)
    util_pool.replacement_players = [p]

    changes = resolve_primary_by_best_dollars({"SS": ss_pool, "UTIL": util_pool})

    # If primary was already SS, no change; either way the headline must be SS.
    assert p.valuation.primary_position == "SS"
    assert p.valuation.tier == "ROSTERED"
    assert p.valuation.total_dollars == 5.0
    # changes >= 0 is fine — function returns count of re-assignments.
    assert changes >= 0


def test_resolve_primary_picks_max_dollars_within_same_tier():
    """Two pools, both REPLACEMENT — max-$ wins."""
    p = _make_hitter("dual", {"R": 0.5, "HR": 0.0}, pos="UTIL")
    p.valuation.primary_position = "UTIL"  # start with low-$ pool as primary
    # SS REPLACEMENT $8
    p.valuation.valuations_by_position["SS"] = PositionValuation(
        position="SS",
        normalized_z={"R": 1.6, "HR": 0.0},
        total_z=1.6,
        dollar_values={"R": 8.0, "HR": 0.0},
        total_dollars=8.0,
        tier="REPLACEMENT",
        position_rank=1,
    )
    # UTIL REPLACEMENT $1
    p.valuation.valuations_by_position["UTIL"] = PositionValuation(
        position="UTIL",
        normalized_z={"R": 0.2, "HR": 0.0},
        total_z=0.2,
        dollar_values={"R": 1.0, "HR": 0.0},
        total_dollars=1.0,
        tier="REPLACEMENT",
        position_rank=1,
    )

    ss_pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    ss_pool.replacement_players = [p]
    util_pool = PositionPool(position="UTIL", role="HITTER", roster_slots=1)
    util_pool.replacement_players = [p]

    changes = resolve_primary_by_best_dollars({"SS": ss_pool, "UTIL": util_pool})

    assert changes == 1
    assert p.valuation.primary_position == "SS"
    assert p.valuation.total_dollars == 8.0


def test_resolve_primary_single_pool_skipped():
    """Single-pool player: no work to do."""
    p = _make_hitter("solo", {"R": 1.0, "HR": 0.0}, pos="SS")
    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.rostered_players = [p]
    changes = resolve_primary_by_best_dollars({"SS": pool})
    assert changes == 0
    assert p.valuation.primary_position == "SS"


def test_compute_shadow_valuations_fills_eligible_pool():
    """A 2B/SS-eligible player primary at 2B gets a shadow SS entry
    computed against the SS pool's archetype + stdev + $/Z."""
    # Player is engine-eligible for both 2B and SS, primary at 2B.
    p = Player(
        id="dual",
        name="dual",
        team="T",
        positions=["2B", "SS"],
        role="HITTER",
        stats=HitterStats(
            pa=600, ab=540, r=85, hr=22, rbi=75, sbn=12,
            obp=0.350, slg=0.470,
        ),
    )
    p.valuation.primary_position = "2B"
    # 2B pool: player is rostered there.
    pool_2b = PositionPool(position="2B", role="HITTER", roster_slots=1)
    pool_2b.rostered_players = [p]
    pool_2b.rlp_raw_avg = {"R": 60.0, "HR": 12.0, "RBI": 55.0, "SBN": 5.0, "OBP": 0.310, "SLG": 0.400}
    pool_2b.rostered_tier_stdevs = {"R": 15.0, "HR": 8.0, "RBI": 18.0, "SBN": 7.0, "OBP": 0.025, "SLG": 0.040}
    pool_2b.dollars_per_z = {"R": 2.0, "HR": 2.0, "RBI": 2.0, "SBN": 2.0, "OBP": 4.0, "SLG": 4.0}
    pool_2b.z_baseline_shift = {c: 0.0 for c in pool_2b.dollars_per_z}
    # SS pool: player not in any tier (will get shadow); pool has 1 other
    # filler so tier-rank works.
    filler = Player(
        id="ss_filler",
        name="ss_filler",
        team="T",
        positions=["SS"],
        role="HITTER",
        stats=HitterStats(
            pa=600, ab=540, r=70, hr=15, rbi=60, sbn=8,
            obp=0.320, slg=0.420,
        ),
    )
    filler.valuation.primary_position = "SS"
    filler.valuation.normalized_z = {"R": 0.5, "HR": 0.5, "RBI": 0.5, "SBN": 0.5, "OBP": 0.5, "SLG": 0.5}
    filler.valuation.total_z = 3.0
    pool_ss = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool_ss.rostered_players = [filler]
    pool_ss.rlp_raw_avg = {"R": 65.0, "HR": 14.0, "RBI": 58.0, "SBN": 6.0, "OBP": 0.315, "SLG": 0.410}
    pool_ss.rostered_tier_stdevs = {"R": 12.0, "HR": 6.0, "RBI": 15.0, "SBN": 5.0, "OBP": 0.020, "SLG": 0.030}
    pool_ss.dollars_per_z = {"R": 3.0, "HR": 3.0, "RBI": 3.0, "SBN": 3.0, "OBP": 5.0, "SLG": 5.0}
    pool_ss.z_baseline_shift = {c: 0.0 for c in pool_ss.dollars_per_z}

    league_settings = {
        "batting_categories": ["R", "HR", "RBI", "SBN", "SLG", "OBP"],
    }

    count = compute_shadow_valuations(
        {"2B": pool_2b, "SS": pool_ss}, league_settings
    )
    assert count == 1
    # Shadow entry exists on the player for SS.
    assert "SS" in p.valuation.valuations_by_position
    shadow = p.valuation.valuations_by_position["SS"]
    assert shadow.shadow is True
    # Shadow z[R] = (85 - 65) / 12 = 1.667. Shadow $[R] = 1.667 * 3 = 5.0.
    assert abs(shadow.normalized_z["R"] - (85 - 65) / 12) < 1e-6
    assert abs(shadow.dollar_values["R"] - (85 - 65) / 12 * 3.0) < 1e-6
    # 2B pool entry was NOT created (player is already rostered there).
    # (Shadow gen only fills missing entries, never overwrites real ones.)
    # The player has no real 2B entry in this test fixture either, but
    # the function correctly skipped 2B because pool_members[2B] = {dual}.


def test_compute_shadow_valuations_skips_when_in_pool():
    """Player who IS in the target pool's tier list doesn't get a shadow."""
    p = _make_hitter("ros", {"R": 1.0, "HR": 0.0}, pos="SS")
    pool_ss = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool_ss.rostered_players = [p]
    pool_ss.rlp_raw_avg = {"R": 60.0}
    pool_ss.rostered_tier_stdevs = {"R": 15.0}
    pool_ss.dollars_per_z = {"R": 2.0}
    pool_ss.z_baseline_shift = {"R": 0.0}
    league_settings = {"batting_categories": ["R"]}

    count = compute_shadow_valuations({"SS": pool_ss}, league_settings)
    assert count == 0


def test_compute_shadow_valuations_skips_util_pool():
    """UTIL pool is intentionally skipped — every hitter already has a
    UTIL entry from the UTIL pool build."""
    p = Player(
        id="ss_only", name="ss_only", team="T",
        positions=["SS", "UTIL"], role="HITTER",
        stats=HitterStats(
            pa=600, ab=540, r=80, hr=20, rbi=70, sbn=10,
            obp=0.340, slg=0.450,
        ),
    )
    p.valuation.primary_position = "SS"
    pool_ss = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool_ss.rostered_players = [p]
    pool_ss.rlp_raw_avg = {"R": 60.0}
    pool_ss.rostered_tier_stdevs = {"R": 15.0}
    pool_ss.dollars_per_z = {"R": 2.0}
    pool_ss.z_baseline_shift = {"R": 0.0}
    # UTIL pool exists but player has no UTIL entry yet.
    pool_util = PositionPool(position="UTIL", role="HITTER", roster_slots=1)
    pool_util.rostered_players = []
    pool_util.rlp_raw_avg = {"R": 50.0}
    pool_util.rostered_tier_stdevs = {"R": 12.0}
    pool_util.dollars_per_z = {"R": 1.5}
    pool_util.z_baseline_shift = {"R": 0.0}
    league_settings = {"batting_categories": ["R"]}

    count = compute_shadow_valuations(
        {"SS": pool_ss, "UTIL": pool_util}, league_settings
    )
    # No shadow for UTIL even though player is eligible — UTIL is skipped.
    assert "UTIL" not in p.valuation.valuations_by_position
    assert count == 0


def _ss_member(pid, dollars, tier):
    """SS-pool member with a per-position $ + tier set (the fields
    finalize_tiers_by_dollars reads and rewrites)."""
    p = _make_hitter(pid, {"R": 1.0}, pos="SS")
    pv = p.valuation.valuations_by_position["SS"]
    pv.total_dollars = dollars
    pv.tier = tier
    p.valuation.total_dollars = dollars
    p.valuation.tier = tier
    return p


def test_finalize_tiers_by_dollars_resorts_and_relabels():
    """A RLP player out-valuing a rostered one is promoted; tiers become a
    clean $-sorted cut: top roster_slots ROSTERED, next len(RLP) REPLACEMENT,
    rest BELOW."""
    pool = PositionPool(position="SS", role="HITTER", roster_slots=2)
    # Tiers deliberately disagree with $-order: a $9 player sits in RLP,
    # a $3 player sits in ROSTERED.
    rost_hi = _ss_member("rost_hi", 20.0, "ROSTERED")
    rost_lo = _ss_member("rost_lo", 3.0, "ROSTERED")   # mis-tiered (too low)
    rlp_hi = _ss_member("rlp_hi", 9.0, "REPLACEMENT")  # out-values rost_lo
    rlp_lo = _ss_member("rlp_lo", 1.0, "REPLACEMENT")
    below = _ss_member("below", -5.0, "BELOW_REPLACEMENT")
    pool.rostered_players = [rost_hi, rost_lo]
    pool.replacement_players = [rlp_hi, rlp_lo]
    pool.below_replacement = [below]

    changed = finalize_tiers_by_dollars({"SS": pool})

    # Top 2 by $ = ROSTERED: rost_hi ($20), rlp_hi ($9).
    assert {p.id for p in pool.rostered_players} == {"rost_hi", "rlp_hi"}
    # Next len(RLP)=2 = REPLACEMENT: rost_lo ($3), rlp_lo ($1).
    assert {p.id for p in pool.replacement_players} == {"rost_lo", "rlp_lo"}
    assert {p.id for p in pool.below_replacement} == {"below"}
    # tier labels mirrored onto vbp + top-level
    assert rlp_hi.valuation.tier == "ROSTERED"
    assert rlp_hi.valuation.valuations_by_position["SS"].tier == "ROSTERED"
    assert rost_lo.valuation.tier == "REPLACEMENT"
    # rlp_hi (REPLACEMENT->ROSTERED) and rost_lo (ROSTERED->REPLACEMENT)
    # each changed.
    assert changed == 2
    # Invariant: no replacement $ exceeds any rostered $.
    assert max(p.valuation.total_dollars for p in pool.replacement_players) <= min(
        p.valuation.total_dollars for p in pool.rostered_players
    )


def test_finalize_tiers_by_dollars_noop_when_already_sorted():
    """Already $-sorted tiers → zero changes (idempotent)."""
    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.rostered_players = [_ss_member("a", 10.0, "ROSTERED")]
    pool.replacement_players = [_ss_member("b", 2.0, "REPLACEMENT")]
    pool.below_replacement = [_ss_member("c", -1.0, "BELOW_REPLACEMENT")]
    assert finalize_tiers_by_dollars({"SS": pool}) == 0


def test_finalize_tiers_by_dollars_skips_empty_pool():
    """An empty pool is skipped without error."""
    empty = PositionPool(position="SS", role="HITTER", roster_slots=1)
    assert finalize_tiers_by_dollars({"SS": empty}) == 0


def test_mu_fringe_count_scales_by_dedicated_slots():
    """_mu_fringe_count = mu_fringe_per_slot * dedicated_slots[pos]; 0 when
    disabled or pool has no dedicated slots."""
    from mtbl_valuations.engine.iteration import _mu_fringe_count

    league = {"dedicated_slots": {"SS": 1, "OF": 3, "SP": 3, "RP": 2}}
    cfg_on = {"replacement_model": {"mu_fringe_per_slot": 2}}
    cfg_off = {"replacement_model": {"mu_fringe_per_slot": 0}}

    def pool(pos):
        return PositionPool(position=pos, role="HITTER", roster_slots=1)

    assert _mu_fringe_count(pool("SS"), cfg_on, league) == 2
    assert _mu_fringe_count(pool("OF"), cfg_on, league) == 6
    assert _mu_fringe_count(pool("SP"), cfg_on, league) == 6  # 3 dedicated, not P-inflated
    assert _mu_fringe_count(pool("RP"), cfg_on, league) == 4
    # disabled
    assert _mu_fringe_count(pool("SS"), cfg_off, league) == 0
    # pos with no dedicated slot entry
    assert _mu_fringe_count(pool("DH"), cfg_on, league) == 0


def test_compute_rlp_archetype_blends_bottom_n_rostered():
    """n_bottom>0 folds the weakest rostered (lowest composite metric) into
    the baseline, raising mu above the RLP-only mean."""
    from mtbl_valuations.engine.iteration import _compute_rlp_archetype

    cats = ["R"]
    cfg = {"rlp_archetype": {"trim_top_pct": 0.0, "sbn_global_mu": None}}

    def hitter(pid, r, wrc):
        p = Player(
            id=pid, name=pid, team="T", positions=["SS"], role="HITTER",
            stats=HitterStats(
                pa=600, ab=540, r=r, hr=20, rbi=70, sbn=10,
                obp=0.340, slg=0.450, wrc_plus=wrc,
            ),
        )
        return p

    rlp = [hitter("rlp1", 50, 90), hitter("rlp2", 60, 95)]  # RLP mean R=55
    # rostered, weakest by wrc_plus = rost_weak (R=80)
    rostered = [hitter("rost_weak", 80, 100), hitter("rost_strong", 120, 140)]

    base = _compute_rlp_archetype(rlp, cats, cfg)
    blended = _compute_rlp_archetype(rlp, cats, cfg, rostered=rostered, n_bottom=1)

    assert base["R"] == 55.0  # mean of 50, 60
    # blends rost_weak (R=80) → mean of 50,60,80 = 63.33 > 55
    assert blended["R"] > base["R"]
    assert abs(blended["R"] - (50 + 60 + 80) / 3) < 1e-6


def _pool_with_settled(pos, members_dollars, archetype, stdev, dpz):
    """Build an SS-style pool with settled archetype/stdev/$/Z and members
    carrying a per-pos total_dollars (for the min-real floor)."""
    pool = PositionPool(position=pos, role="HITTER", roster_slots=1)
    pool.rlp_raw_avg = dict(archetype)
    pool.rostered_tier_stdevs = dict(stdev)
    pool.dollars_per_z = dict(dpz)
    pool.z_baseline_shift = {c: 0.0 for c in archetype}
    for i, d in enumerate(members_dollars):
        p = _make_hitter(f"real{i}", {c: 0.0 for c in archetype}, pos=pos)
        p.valuation.valuations_by_position[pos].total_dollars = d
        (pool.rostered_players if i == 0 else pool.below_replacement).append(p)
    return pool


def test_valuate_unqualified_shifts_below_min_real_and_differentiates():
    cats = ["R", "HR", "RBI", "SBN", "OBP", "SLG"]
    archetype = {c: 10.0 for c in cats}
    stdev = {c: 5.0 for c in cats}
    dpz = {c: 1.0 for c in cats}
    # Pool's lowest real $ = -8.0.
    pool = _pool_with_settled("OF", [20.0, -8.0], archetype, stdev, dpz)

    # Two unqualified OF players with different raw production → different hyp.
    def uq(pid, r):
        p = Player(
            id=pid, name=pid, team="T", positions=["OF"], role="HITTER",
            stats=HitterStats(pa=80, ab=72, r=r, hr=1, rbi=5, sbn=0,
                              obp=0.300, slg=0.350),
        )
        return p

    strong = uq("uq_strong", 18)   # closer to archetype
    weak = uq("uq_weak", 2)        # far below
    n = valuate_unqualified_players([strong, weak], {"OF": pool}, {
        "batting_categories": cats,
    })
    assert n == 2
    sv = strong.valuation.valuations_by_position["OF"]
    wv = weak.valuation.valuations_by_position["OF"]
    # Both below the pool's lowest real ($-8), flagged, BELOW tier.
    assert sv.total_dollars < -8.0
    assert wv.total_dollars < -8.0
    assert sv.unqualified and wv.unqualified and sv.shadow and wv.shadow
    assert sv.tier == "BELOW_REPLACEMENT"
    # Differentiated: the stronger projection ranks above the weaker.
    assert sv.total_dollars > wv.total_dollars
    # Headline mirrors the per-pos entry.
    assert strong.valuation.total_dollars == sv.total_dollars
    assert strong.valuation.primary_position == "OF"


def test_valuate_unqualified_empty_is_noop():
    pool = PositionPool(position="OF", role="HITTER", roster_slots=1)
    assert valuate_unqualified_players([], {"OF": pool}, {}) == 0
