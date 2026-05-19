"""Specs for the current-season-actuals valuation source."""

from __future__ import annotations

import json

import pytest

from mtbl_valuations.io.current import (
    load_batters_current,
    load_pitchers_current,
)


def test_current_batters_use_current_season_actuals(
    batters_file, qualified_pa
):
    with open(batters_file) as f:
        records = json.load(f)

    hitters = load_batters_current(batters_file, qualified_pa)

    # Gated by the sliding qualified threshold, so a strict subset.
    assert 0 < len(hitters) < len(records)
    assert all(hp.player.role == "HITTER" for hp in hitters)
    # Every loaded hitter clears the qualified PA bar.
    assert all(hp.stats.pa >= qualified_pa for hp in hitters)

    # Stats are the raw current-season actuals, not projections.
    by_id = {hp.player.id: hp for hp in hitters}
    checked = 0
    for record in records:
        pid = str(record["id_espn"])
        if pid not in by_id:
            continue
        cs = record["stats"]["espn"]["current_season"]
        hp = by_id[pid]
        assert hp.stats.hr == pytest.approx(float(cs["HR"]))
        assert hp.stats.obp == pytest.approx(float(cs["OBP"]))
        assert hp.stats.r == pytest.approx(float(cs["R"]))
        checked += 1
        if checked >= 25:
            break
    assert checked > 0


def test_current_batters_skip_below_threshold(batters_file, qualified_pa):
    hitters = load_batters_current(batters_file, qualified_pa)
    current_ids = {hp.player.id for hp in hitters}

    with open(batters_file) as f:
        records = json.load(f)

    # A player below the qualified PA bar must not be valued.
    below = next(
        r
        for r in records
        if float(
            (
                (r.get("stats", {}).get("espn", {}) or {}).get("current_season")
                or {}
            ).get("PA", 0.0)
        )
        < qualified_pa
    )
    assert str(below["id_espn"]) not in current_ids


def test_current_pitchers_gated_on_batters_faced(pitchers_file, qualified_pa):
    with open(pitchers_file) as f:
        records = json.load(f)

    pitchers = load_pitchers_current(pitchers_file, qualified_pa)

    assert 0 < len(pitchers) < len(records)
    assert all(pp.player.role in ("SP", "RP") for pp in pitchers)

    by_id = {pp.player.id: pp for pp in pitchers}
    checked = 0
    for record in records:
        pid = str(record["id_espn"])
        if pid not in by_id:
            continue
        cs = record["stats"]["espn"]["current_season"]
        # Every loaded pitcher cleared the batters-faced bar...
        assert float(cs["TBF"]) >= qualified_pa
        # ...and is valued on current-season actuals.
        assert by_id[pid].stats.era == pytest.approx(float(cs["ERA"]))
        checked += 1
        if checked >= 25:
            break
    assert checked > 0
