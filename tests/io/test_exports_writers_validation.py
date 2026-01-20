import json

import pandas as pd

from mtbl_valuations.domain.models import (
    HitterStats,
    LeagueBudget,
    PitcherStats,
    Player,
    PositionPool,
)
from mtbl_valuations.io.exports import export_hitter_position_csv, export_pitcher_pool_csv
from mtbl_valuations.io.writers import write_player_json
from mtbl_valuations.validation.checks import (
    validate_budget_balance,
    validate_tier_counts,
)


def _make_hitter(player_id: str) -> Player:
    return Player(
        id=player_id,
        name=f"Hitter {player_id}",
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
    )


def _make_pitcher(player_id: str) -> Player:
    return Player(
        id=player_id,
        name=f"Pitcher {player_id}",
        team="T",
        positions=["RP"],
        role="RP",
        stats=PitcherStats(outs=9, era=3.0, whip=1.1, k9=9.0, qs=0, svhd=2),
    )


def test_export_hitter_position_csv(tmp_path):
    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.rostered_players = [_make_hitter("h1")]
    pool.replacement_players = []
    pool.below_replacement = [
        Player(
            id="h2",
            name="NoStats",
            team="T",
            positions=["SS"],
            role="HITTER",
            stats=None,
        )
    ]

    output_path = tmp_path / "ss.csv"
    export_hitter_position_csv(pool, output_path, ["R", "XYZ"])

    df = pd.read_csv(output_path)
    assert "XYZ_raw" in df.columns
    assert len(df) == 1


def test_export_pitcher_pool_csv(tmp_path):
    pool = PositionPool(position="RP", role="RP", roster_slots=1)
    pool.rostered_players = [_make_pitcher("p1")]
    pool.replacement_players = []
    pool.below_replacement = [
        Player(
            id="p2",
            name="NoStats",
            team="T",
            positions=["RP"],
            role="RP",
            stats=None,
        )
    ]

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
    assert data[0]["stats"]["valuations"]["total_z"] == 1.234


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
