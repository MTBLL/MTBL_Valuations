"""Specs for the Statcast-derived synthetic valuation source."""

from __future__ import annotations

import json

import pytest

from mtbl_valuations.io.synthetic import (
    load_batters_synthetic,
    load_pitchers_synthetic,
)


def _has_savant_all(record: dict) -> bool:
    savant = record.get("stats", {}).get("savant") or {}
    savant_all = savant.get("all") or {}
    return all(savant_all.get(k) is not None for k in ("xOBP", "xSLG", "xwOBA"))


def test_synthetic_batters_use_savant_rates(batters_file, budget_config, qualified_pa):
    with open(batters_file) as f:
        records = json.load(f)

    hitters = load_batters_synthetic(batters_file, budget_config, qualified_pa)

    # Savant is absent for most players, so the synthetic universe is a
    # strict subset of the raw record count.
    assert 0 < len(hitters) < len(records)
    assert all(hp.player.role == "HITTER" for hp in hitters)
    assert all(hp.stats.pa > 0 for hp in hitters)

    # OBP / SLG come straight from Savant xOBP / xSLG.
    by_id = {hp.player.id: hp for hp in hitters}
    checked = 0
    for record in records:
        pid = str(record["id_espn"])
        if pid not in by_id or not _has_savant_all(record):
            continue
        savant_all = record["stats"]["savant"]["all"]
        hp = by_id[pid]
        assert hp.stats.obp == pytest.approx(float(savant_all["xOBP"]))
        assert hp.stats.slg == pytest.approx(float(savant_all["xSLG"]))
        checked += 1
        if checked >= 25:
            break
    assert checked > 0


def test_synthetic_batters_skip_players_without_savant(
    batters_file, budget_config, qualified_pa
):
    with open(batters_file) as f:
        records = json.load(f)

    hitters = load_batters_synthetic(batters_file, budget_config, qualified_pa)
    synthetic_ids = {hp.player.id for hp in hitters}

    # A player with no Savant "all" block must not be synthesized.
    no_savant = next(
        r for r in records if not _has_savant_all(r)
    )
    assert str(no_savant["id_espn"]) not in synthetic_ids


def test_synthetic_pitchers_use_savant_xera(
    pitchers_file, budget_config, qualified_pa
):
    with open(pitchers_file) as f:
        records = json.load(f)

    pitchers = load_pitchers_synthetic(pitchers_file, budget_config, qualified_pa)

    assert 0 < len(pitchers) < len(records)
    assert all(pp.player.role in ("SP", "RP") for pp in pitchers)

    # ERA is taken directly from Savant xERA.
    by_id = {pp.player.id: pp for pp in pitchers}
    checked = 0
    for record in records:
        pid = str(record["id_espn"])
        exp = (record.get("stats", {}).get("savant") or {}).get(
            "expected_statistics"
        ) or {}
        if pid not in by_id or exp.get("xERA") is None:
            continue
        assert by_id[pid].stats.era == pytest.approx(float(exp["xERA"]))
        checked += 1
        if checked >= 25:
            break
    assert checked > 0
