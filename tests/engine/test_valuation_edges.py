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
    _get_categories,
    calc_means,
    calc_stdevs,
    calc_z_scores_for_archetype,
    distribute_pool_dollars,
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


def test_distribute_pool_dollars_pin_rlp_to_zero_true():
    """pin_rlp_to_zero=True: REPLACEMENT tier flat-$0, BELOW gets formula
    and any positive-formula BELOW gets promoted with $0 mirroring."""
    rost = _make_hitter("rost", {"R": 2.0, "HR": 1.0})
    rlp = _make_hitter("rlp", {"R": 0.5, "HR": 0.0})
    rlp.valuation.valuations_by_position["SS"].tier = "REPLACEMENT"
    below = _make_hitter("below", {"R": -1.0, "HR": -2.0})
    below.valuation.valuations_by_position["SS"].tier = "BELOW_REPLACEMENT"

    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.category_budgets = {"R": 10.0, "HR": 5.0}
    pool.dollars_per_z = {"R": 5.0, "HR": 5.0}
    pool.rostered_players = [rost]
    pool.replacement_players = [rlp]
    pool.below_replacement = [below]

    distribute_pool_dollars(
        {"SS": pool}, store_per_position=True, pin_rlp_to_zero=True
    )

    assert rost.valuation.total_dollars == 15.0  # 2*5 + 1*5
    # RLP pinned to $0 in both top-level and per-position mirror
    assert rlp.valuation.total_dollars == 0.0
    assert rlp.valuation.valuations_by_position["SS"].total_dollars == 0.0
    # BELOW with negative formula stays BELOW — gets formula-$, no promotion
    assert below.valuation.total_dollars == -15.0  # -1*5 + -2*5
    assert below in pool.below_replacement
    assert below not in pool.replacement_players


def test_distribute_pool_dollars_pin_rlp_to_zero_true_promotes_positive_below_to_zero():
    """pin_rlp_to_zero=True + positive-formula BELOW: player is promoted
    to REPLACEMENT and pinned to $0 (formula-$ is discarded to match the
    pinned-RLP semantics)."""
    rost = _make_hitter("rost", {"R": 2.0, "HR": 1.0})
    rlp = _make_hitter("rlp", {"R": 0.5, "HR": 0.0})
    rlp.valuation.valuations_by_position["SS"].tier = "REPLACEMENT"
    # below with positive net z → would yield formula-$ > 0 → promote
    below = _make_hitter("below_pos", {"R": 1.0, "HR": 0.0})
    below.valuation.valuations_by_position["SS"].tier = "BELOW_REPLACEMENT"

    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.category_budgets = {"R": 10.0, "HR": 5.0}
    pool.dollars_per_z = {"R": 5.0, "HR": 5.0}
    pool.rostered_players = [rost]
    pool.replacement_players = [rlp]
    pool.below_replacement = [below]

    distribute_pool_dollars(
        {"SS": pool}, store_per_position=True, pin_rlp_to_zero=True
    )

    # Promoted to REPLACEMENT, $-fields zeroed to match pinned semantics.
    assert below.valuation.tier == "REPLACEMENT"
    assert below.valuation.total_dollars == 0.0
    assert below.valuation.valuations_by_position["SS"].tier == "REPLACEMENT"
    assert below.valuation.valuations_by_position["SS"].total_dollars == 0.0
    assert below in pool.replacement_players
    assert below not in pool.below_replacement


def test_distribute_pool_dollars_pin_rlp_to_zero_false_promotes_positive_below():
    """pin_rlp_to_zero=False default: a BELOW player whose formula nets
    positive gets promoted to REPLACEMENT — but the formula-$ stands,
    not $0 (because RLP is not pinned in this mode)."""
    rost = _make_hitter("rost", {"R": 2.0, "HR": 1.0})
    rlp = _make_hitter("rlp", {"R": 0.5, "HR": 0.5})
    rlp.valuation.valuations_by_position["SS"].tier = "REPLACEMENT"
    # below_with_positive_formula: net z is +1, formula-$ would be +5
    below = _make_hitter("below_pos", {"R": 1.0, "HR": 0.0})
    below.valuation.valuations_by_position["SS"].tier = "BELOW_REPLACEMENT"

    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.category_budgets = {"R": 10.0, "HR": 5.0}
    pool.dollars_per_z = {"R": 5.0, "HR": 5.0}
    pool.rostered_players = [rost]
    pool.replacement_players = [rlp]
    pool.below_replacement = [below]

    distribute_pool_dollars(
        {"SS": pool}, store_per_position=True, pin_rlp_to_zero=False
    )

    assert rlp.valuation.total_dollars == 5.0  # 0.5*5 + 0.5*5
    # promoted: tier flips to REPLACEMENT, but $ stays at formula value
    assert below.valuation.tier == "REPLACEMENT"
    assert below.valuation.total_dollars == 5.0  # 1.0*5
    # moved out of below_replacement list, into replacement_players
    assert below not in pool.below_replacement
    assert below in pool.replacement_players


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
