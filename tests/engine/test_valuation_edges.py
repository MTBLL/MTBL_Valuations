import math
from types import SimpleNamespace

from mtbl_valuations.domain.models import HitterStats, PitcherStats, Player
from mtbl_valuations.engine.valuation import (
    calc_means,
    calc_stdevs,
    calc_z_scores_for_archetype,
    get_categories,
    get_composite_metric,
    get_player_stat,
    is_inverted,
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
