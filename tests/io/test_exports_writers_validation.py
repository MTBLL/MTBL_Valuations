import json

import pandas as pd

from mtbl_valuations.domain.models import (
    HitterStats,
    LeagueBudget,
    PitcherStats,
    Player,
    PositionPool,
)
from mtbl_valuations.io.exports import (
    export_hitter_position_csv,
    export_pitcher_pool_csv,
)
from mtbl_valuations.io.writers import (
    build_player_valuations,
    write_merged_player_json,
    write_player_json,
)
from mtbl_valuations.validation.checks import (
    validate_budget_balance,
    validate_tier_counts,
)


def _make_hitter(player_id: str, position: str = "SS") -> Player:
    player = Player(
        id=player_id,
        name=f"Hitter {player_id}",
        team="T",
        positions=[position],
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
    )
    player.valuation.primary_position = position
    return player


def _make_pitcher(player_id: str, position: str = "RP") -> Player:
    player = Player(
        id=player_id,
        name=f"Pitcher {player_id}",
        team="T",
        positions=[position],
        role="RP",  # Role is always RP for this helper
        stats=PitcherStats(outs=9, era=3.0, whip=1.1, k9=9.0, qs=0, svhd=2),
    )
    player.valuation.primary_position = position
    return player


def test_export_hitter_position_csv(tmp_path):
    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.rostered_players = [_make_hitter("h1", "SS")]
    pool.replacement_players = []
    no_stats_player = Player(
        id="h2",
        name="NoStats",
        team="T",
        positions=["SS"],
        role="HITTER",
        stats=None,
    )
    no_stats_player.valuation.primary_position = "SS"
    pool.below_replacement = [no_stats_player]

    output_path = tmp_path / "ss.csv"
    export_hitter_position_csv(pool, output_path, ["R", "XYZ"])

    df = pd.read_csv(output_path)
    assert "XYZ_raw" in df.columns
    assert len(df) == 1


def test_export_pitcher_pool_csv(tmp_path):
    pool = PositionPool(position="RP", role="RP", roster_slots=1)
    pool.rostered_players = [_make_pitcher("p1", "RP")]
    pool.replacement_players = []
    no_stats_pitcher = Player(
        id="p2",
        name="NoStats",
        team="T",
        positions=["RP"],
        role="RP",
        stats=None,
    )
    no_stats_pitcher.valuation.primary_position = "RP"
    pool.below_replacement = [no_stats_pitcher]

    output_path = tmp_path / "rp.csv"
    export_pitcher_pool_csv(pool, output_path, ["IP", "FOO"])

    df = pd.read_csv(output_path)
    assert "FOO_raw" in df.columns
    assert len(df) == 1


def test_write_player_json_adds_stats(tmp_path):
    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    player = _make_hitter("h3")
    player.valuation.total_z = 1.234
    player.valuation.total_dollars = 12.34
    player.valuation.normalized_z = {"R": 0.5}
    player.valuation.dollar_values = {"R": 1.0}
    pool.rostered_players = [player]

    output_path = tmp_path / "players.json"
    input_data = [{"id_espn": "h3"}]

    write_player_json(output_path, input_data, {"SS": pool})

    data = json.loads(output_path.read_text())
    assert data[0]["valuations"]["total_z"] == 1.234


def test_build_player_valuations_keys_by_id():
    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    player = _make_hitter("h4")
    player.valuation.total_z = 2.0
    player.valuation.total_dollars = 20.0
    player.valuation.normalized_z = {"R": 1.0}
    player.valuation.dollar_values = {"R": 5.0}
    pool.rostered_players = [player]

    valuations = build_player_valuations({"SS": pool})

    assert set(valuations) == {"h4"}
    assert valuations["h4"]["total_dollars"] == 20.0
    assert valuations["h4"]["z_scores"] == {"R": 1.0}


def test_write_merged_player_json_keys_by_source(tmp_path):
    """Each player's valuations are nested by source label; a player absent
    from a source simply doesn't get that key, and a player absent from every
    source gets no ``valuations`` block at all."""
    valuations_by_source = {
        "preseason": {"h1": {"total_dollars": 10.0}, "h2": {"total_dollars": 4.0}},
        "ros": {"h1": {"total_dollars": 12.0}},
    }
    input_data = [{"id_espn": "h1"}, {"id_espn": "h2"}, {"id_espn": "h3"}]

    output_path = tmp_path / "merged.json"
    write_merged_player_json(output_path, input_data, valuations_by_source)

    data = json.loads(output_path.read_text())
    by_id = {rec["id_espn"]: rec for rec in data}
    # h1 valued in both sources
    assert set(by_id["h1"]["valuations"]) == {"preseason", "ros"}
    assert by_id["h1"]["valuations"]["ros"]["total_dollars"] == 12.0
    # h2 valued only in preseason
    assert set(by_id["h2"]["valuations"]) == {"preseason"}
    # h3 valued in no source -> no valuations block
    assert "valuations" not in by_id["h3"]


def test_validation_failures(capsys):
    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    player = _make_hitter("h4")
    player.valuation.total_dollars = 5.0
    pool.rostered_players = [player]

    league_budget = LeagueBudget(
        total=20.0,
        hitter_budget=0.0,
        pitcher_budget=0.0,
        sp_budget=0.0,
        rp_budget=0.0,
    )

    validate_budget_balance({"SS": pool}, league_budget)
    validate_tier_counts({"SS": pool}, {"SS": 2}, 1)

    captured = capsys.readouterr()
    assert "FAILED" in captured.out
    assert "MISMATCH" in captured.out
