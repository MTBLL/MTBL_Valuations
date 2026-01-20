"""Specs for loader functions."""

from __future__ import annotations

import json

import pytest

from mtbl_valuations.io.loader import (
    load_batters,
    load_budget_config,
    load_pitchers,
)


def _get_record_by_id(
    records: list[dict[str, object]], player_id: str
) -> dict[str, object]:
    for record in records:
        if str(record.get("id_espn")) == str(player_id):
            return record
    raise AssertionError(f"Expected player id {player_id} in fixture")


def test_load_batters_maps_projection_fields(batters_file):
    with open(batters_file) as f:
        records = json.load(f)

    record: dict[str, object] = _get_record_by_id(records, "32801")
    projections = record.get("stats").get("projections")  # type: ignore

    hitters = load_batters(batters_file)
    hitter = next(hp for hp in hitters if hp.player.id == str(record["id_espn"]))

    assert hitter.player.name == record["name"]
    assert hitter.player.team == record["pro_team"]
    assert hitter.player.positions == record["eligible_slots"]
    assert hitter.player.role == "HITTER"

    assert hitter.stats.pa == pytest.approx(float(projections["PA"]))
    assert hitter.stats.ab == pytest.approx(float(projections["AB"]))
    assert hitter.stats.r == pytest.approx(float(projections["R"]))
    assert hitter.stats.hr == pytest.approx(float(projections["HR"]))
    assert hitter.stats.rbi == pytest.approx(float(projections["RBI"]))
    assert hitter.stats.obp == pytest.approx(float(projections["OBP"]))
    assert hitter.stats.slg == pytest.approx(float(projections["SLG"]))

    expected_sbn = projections.get(
        "SBN", projections.get("SB", 0) - projections.get("CS", 0)
    )
    assert hitter.stats.sbn == pytest.approx(float(expected_sbn))
    assert hitter.stats.wrc_plus == pytest.approx(float(projections.get("wRC+", 100.0)))
    assert hitter.player.stats is hitter.stats


def test_load_pitchers_assigns_roles_and_stats(pitchers_file):
    with open(pitchers_file) as f:
        records = json.load(f)

    swingman_record = _get_record_by_id(records, "42584")
    swingman_proj = swingman_record["stats"]["projections"]  # type: ignore

    rp_record = _get_record_by_id(records, "4734325")
    rp_proj = rp_record["stats"]["projections"]  # type: ignore

    pitchers = load_pitchers(pitchers_file)

    swingman = next(p for p in pitchers if p.player.id == "42584")
    assert swingman.player.role == "SP"
    assert swingman.stats.outs == pytest.approx(float(swingman_proj["IP"]) * 3.0)
    assert swingman.stats.qs == pytest.approx(float(swingman_proj.get("QS", 0.0)))
    assert swingman.stats.svhd == pytest.approx(0.0)
    assert swingman.player.positions == swingman_record["eligible_slots"]
    assert swingman.player.stats is swingman.stats

    reliever_with_gs = next(p for p in pitchers if p.player.id == "4734325")
    assert reliever_with_gs.player.role == "SP"
    assert reliever_with_gs.stats.outs == pytest.approx(float(rp_proj["IP"]) * 3.0)
    assert reliever_with_gs.stats.qs == pytest.approx(1)

    expected_svhd = rp_proj.get("SVHD", rp_proj.get("SV", 0) + rp_proj.get("HLD", 0))
    assert reliever_with_gs.stats.svhd == pytest.approx(float(expected_svhd))
    assert reliever_with_gs.player.positions == rp_record["eligible_slots"]
    assert reliever_with_gs.player.stats is reliever_with_gs.stats


def test_load_league_settings_parses_roster_and_categories(
    league_file, league_settings
):
    with open(league_file) as f:
        raw = json.load(f)

    assert league_settings["num_teams"] == raw["num_teams"]
    assert league_settings["auction_budget"] == raw["draft_auction_budget"]
    assert league_settings["acquisition_budget"] == raw["acquisition_budget"]

    roster_slots = league_settings["roster_slots"]
    assert roster_slots["C"] == 1
    assert roster_slots["OF"] == 3
    assert roster_slots["UTIL"] == 1
    assert roster_slots["SP"] == 4
    assert roster_slots["RP"] == 3
    assert roster_slots["BENCH"] == 5

    expected_batting = [cat["name"] for cat in raw["scoring_categories"]["batting"]]
    expected_pitching = [cat["name"] for cat in raw["scoring_categories"]["pitching"]]
    expected_reverse = [
        cat["name"]
        for cat in raw["scoring_categories"]["batting"]
        + raw["scoring_categories"]["pitching"]
        if cat.get("is_reverse", False)
    ]

    assert league_settings["batting_categories"] == expected_batting
    assert league_settings["pitching_categories"] == expected_pitching
    assert league_settings["reverse_categories"] == expected_reverse


def test_load_budget_config_reads_fixture(fixtures_dir):
    budget_file = fixtures_dir / "budget_config.json"

    with open(budget_file) as f:
        raw = json.load(f)

    assert load_budget_config(budget_file) == raw
