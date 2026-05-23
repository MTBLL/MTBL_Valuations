"""Targeted tests for defensive fallback branches in io/* modules.

Each spec hand-builds a minimal in-memory fixture that exercises one
specific guard (missing field, empty population, malformed savant block
etc.). Used to cover branches the live fixture data never reaches.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ----- io/current.py -------------------------------------------------


def _write_batters(tmp_path: Path, records: list[dict]) -> Path:
    p = tmp_path / "batters.json"
    p.write_text(json.dumps(records))
    return p


def _write_pitchers(tmp_path: Path, records: list[dict]) -> Path:
    p = tmp_path / "pitchers.json"
    p.write_text(json.dumps(records))
    return p


def _cs_batter_record(*, pa: float, with_sbn: bool) -> dict:
    cs: dict = {
        "PA": pa,
        "AB": pa - 50,
        "R": 50,
        "HR": 10,
        "RBI": 40,
        "SB": 5,
        "CS": 1,
        "OBP": 0.330,
        "SLG": 0.420,
        "OPS": 0.750,
    }
    if with_sbn:
        cs["SBN"] = 7
    return {
        "id_espn": "1001",
        "name": "Test Hitter",
        "pro_team": "TST",
        "eligible_slots": ["1B"],
        "primary_position": "1B",
        "stats": {"espn": {"current_season": cs}, "fangraphs": {}, "savant": {}},
    }


def _cs_pitcher_record(
    *, tbf: float, primary_pos: str, with_outs: bool, with_svhd: bool
) -> dict:
    cs: dict = {
        "TBF": tbf,
        "IP": 80.0,
        "ERA": 3.50,
        "WHIP": 1.20,
        "k_per_9": 9.5,
        "QS": 10,
        "GS": 14,
        "SV": 0,
        "HLD": 0,
    }
    if with_outs:
        cs["OUTS"] = 240.0
    if with_svhd:
        cs["SVHD"] = 0
    return {
        "id_espn": "2001",
        "name": "Test Pitcher",
        "pro_team": "TST",
        "eligible_slots": [primary_pos],
        "primary_position": primary_pos,
        "stats": {"espn": {"current_season": cs}, "fangraphs": {}, "savant": {}},
    }


def test_load_batters_current_derives_sbn_when_missing(tmp_path: Path):
    """Current-source batter loader: when SBN missing, falls back to SB-CS."""
    from mtbl_valuations.io.current import load_batters_current

    path = _write_batters(tmp_path, [_cs_batter_record(pa=200, with_sbn=False)])
    hitters = load_batters_current(path, qualified_pa=100)
    assert len(hitters) == 1
    # SB=5, CS=1 → SBN=4.
    assert hitters[0].stats.sbn == pytest.approx(4.0)


def test_load_pitchers_current_skips_non_pitcher_primary(tmp_path: Path):
    """Pitcher loader skips records whose primary_position isn't SP/RP."""
    from mtbl_valuations.io.current import load_pitchers_current

    rec = _cs_pitcher_record(
        tbf=300, primary_pos="OF", with_outs=True, with_svhd=True
    )
    path = _write_pitchers(tmp_path, [rec])
    pitchers = load_pitchers_current(path, qualified_pa=100)
    assert pitchers == []


def test_load_pitchers_current_skips_sp_below_min_gs(tmp_path: Path):
    """Current-source SPs with too few starts are filtered. ``outs``
    aggregates start + relief innings, so a 2-GS spot-starter would
    otherwise normalize to a 30-IP-per-start "ace.\""""
    from mtbl_valuations.io.current import load_pitchers_current

    rec = _cs_pitcher_record(
        tbf=300, primary_pos="SP", with_outs=True, with_svhd=True
    )
    rec["stats"]["espn"]["current_season"]["GS"] = 2  # below threshold
    path = _write_pitchers(tmp_path, [rec])

    # No filter (default 0) -> kept and normalized to per-start outs.
    pitchers = load_pitchers_current(path, qualified_pa=100)
    assert len(pitchers) == 1
    assert pitchers[0].stats.outs == pytest.approx(240.0 / 2.0)

    # With min_gs=5 -> filtered out.
    pitchers = load_pitchers_current(path, qualified_pa=100, min_gs_for_sp=5)
    assert pitchers == []


def test_load_pitchers_current_derives_outs_when_missing(tmp_path: Path):
    """Pitcher loader: when OUTS missing, falls back to IP × 3. The
    current-source SP path then normalizes to per-start outs (outs / GS)
    so the IP-z is opportunity-fair across IL-thinned rosters."""
    from mtbl_valuations.io.current import load_pitchers_current

    rec = _cs_pitcher_record(
        tbf=300, primary_pos="SP", with_outs=False, with_svhd=True
    )
    path = _write_pitchers(tmp_path, [rec])
    pitchers = load_pitchers_current(path, qualified_pa=100)
    assert len(pitchers) == 1
    # IP=80 → outs=240; GS=14 → per-start outs = 240 / 14.
    assert pitchers[0].stats.outs == pytest.approx(240.0 / 14.0)


# ----- io/qualified.py -----------------------------------------------


def test_compute_qualified_pa_returns_zero_when_no_current_season(tmp_path: Path):
    """No batters with a current_season block → games list empty → return 0."""
    from mtbl_valuations.io.qualified import compute_qualified_pa

    rec = {
        "id_espn": "1",
        "name": "X",
        "pro_team": "T",
        "eligible_slots": ["1B"],
        "primary_position": "1B",
        "stats": {"espn": {}, "fangraphs": {}, "savant": {}},
    }
    path = _write_batters(tmp_path, [rec])
    cfg = {"qualified": {"rate_pa_per_game": 1.5, "team_games_percentile": 0.8}}
    assert compute_qualified_pa(path, cfg) == 0


# ----- io/savant_ranks.py --------------------------------------------


def test_savant_pct_rnks_skips_bool_values():
    """Booleans pass ``isinstance(x, int)`` in Python so the loader needs
    an explicit bool reject to avoid ranking True/False as numbers."""
    from mtbl_valuations.io.savant_ranks import _is_rankable

    assert _is_rankable("active", True) is False
    assert _is_rankable("active", False) is False


def test_savant_pct_rnks_single_population_returns_half():
    """A 1-element distribution can't produce a meaningful percentile —
    fall back to 0.5."""
    from mtbl_valuations.io.savant_ranks import _percentile_rank

    assert _percentile_rank([5.0], 5.0) == 0.5


def test_savant_pct_rnks_skips_non_dict_savant_block():
    """``stats.savant`` can be a list (e.g. pitch_arsenal blob), None, or
    anything else upstream chooses — only dict-shaped blocks rank."""
    from mtbl_valuations.io.savant_ranks import inject_savant_pct_rnks

    batters = [
        {
            "id_espn": "1",
            "stats": {"savant": ["not", "a", "dict"]},
        }
    ]
    pitchers: list[dict] = []
    # Must not raise.
    inject_savant_pct_rnks(batters, pitchers, {"1"}, set())


def test_savant_pct_rnks_skips_field_with_no_population():
    """A field only present on records outside the ranking population
    has no values to rank against → skipped."""
    from mtbl_valuations.io.savant_ranks import inject_savant_pct_rnks

    batters = [
        {
            "id_espn": "in_pop",
            "stats": {"savant": {"all": {"K_pct": 25.0}}},
        },
        {
            "id_espn": "not_in_pop",
            "stats": {"savant": {"all": {"unique_field": 42.0}}},
        },
    ]
    # population only includes the player without ``unique_field``.
    inject_savant_pct_rnks(batters, [], {"in_pop"}, set())
    # ``unique_field`` has no in-population values → no pct_rnk injected.
    second_savant = batters[1]["stats"]["savant"]["all"]
    assert "unique_field_pct_rnk" not in second_savant


# ----- io/synthetic.py -----------------------------------------------


def test_synthetic_percentile_rank_returns_half_on_empty():
    """``_percentile_rank`` with no population can't rank — falls back to 0.5."""
    from mtbl_valuations.io.synthetic import _percentile_rank

    assert _percentile_rank([], 1.0) == 0.5


def test_synthetic_blend_returns_zero_when_all_weights_zero():
    """``_blend`` with every weight zero short-circuits to 0.0 rather than
    div-by-zero on the weighted denominator."""
    from mtbl_valuations.io.synthetic import _blend

    assert _blend([(1.0, 0.0), (2.0, 0.0)]) == 0.0


def test_synthetic_blend_skips_none_present_with_zero_total_weight():
    """All-None inputs collapse to total_w=0 → 0.0 fallback."""
    from mtbl_valuations.io.synthetic import _blend

    assert _blend([(None, 0.5), (None, 0.5)]) == 0.0


def _syn_batter_record(*, pa: float, with_sprint: bool) -> dict:
    """Build a batter record that passes every synthetic-loader gate
    except the ones the test wants to exercise."""
    savant_all = {
        "PA": 400,
        "xOBP": 0.330,
        "xSLG": 0.420,
        "xwOBA": 0.330,
    }
    savant: dict = {"all": savant_all, "home_runs": {"xHR": 18.0}}
    if with_sprint:
        savant["sprint_speed"] = {"sprint_speed": 27.5}
    proj = {
        "PA": pa,
        "AB": pa - 50,
        "R": 80,
        "HR": 20,
        "RBI": 70,
        "SBN": 5,
        "OBP": 0.330,
        "SLG": 0.420,
        "wRC+": 110,
    }
    return {
        "id_espn": "9001",
        "name": "Syn Batter",
        "pro_team": "TST",
        "eligible_slots": ["1B"],
        "primary_position": "1B",
        "stats": {"fangraphs": {"projs_updated": proj}, "savant": savant, "espn": {}},
    }


def _syn_pitcher_record(*, with_xwoba: bool, with_whiff: bool) -> dict:
    """Pitcher record exercising the WHIP / K/9 league-baseline fallbacks."""
    exp: dict = {"PA": 200, "xERA": 3.50}
    if with_xwoba:
        exp["xwOBA"] = 0.300
    savant_all: dict = {}
    if with_whiff:
        savant_all["swing_miss_pct"] = 12.0
    proj = {
        "PA": 600,
        "IP": 150.0,
        "OUTS": 450,
        "ERA": 3.50,
        "WHIP": 1.20,
        "K/9": 9.0,
        "FIP": 3.40,
        "GS": 25,
        "QS": 12,
        "SV": 0,
        "HLD": 0,
    }
    return {
        "id_espn": "9002",
        "name": "Syn Pitcher",
        "pro_team": "TST",
        "eligible_slots": ["SP"],
        "primary_position": "SP",
        "stats": {
            "fangraphs": {"projs_updated": proj},
            "savant": {"expected_statistics": exp, "all": savant_all},
            "espn": {},
        },
    }


def test_synthetic_batters_skip_zero_scaffold_pa(tmp_path: Path):
    """A batter with savant data but scaffold projection PA=0 is skipped
    (the synthesized stats are scaled by proj_pa — zero would yield zero
    counting stats and produce a stub that doesn't belong in the pool)."""
    from mtbl_valuations.io.synthetic import load_batters_synthetic

    rec = _syn_batter_record(pa=0.0, with_sprint=True)
    path = _write_batters(tmp_path, [rec])
    out = load_batters_synthetic(path, {}, qualified_pa=100)
    assert out == []


def test_synthetic_batters_fall_back_to_proj_sbn_without_sprint(tmp_path: Path):
    """When a hitter has no sprint_speed observation, SBN is left at the
    scaffold projection value (the speed-percentile modulation is skipped)."""
    from mtbl_valuations.io.synthetic import load_batters_synthetic

    rec_with = _syn_batter_record(pa=500, with_sprint=True)
    rec_with["id_espn"] = "with_sprint"
    rec_without = _syn_batter_record(pa=500, with_sprint=False)
    rec_without["id_espn"] = "without_sprint"
    path = _write_batters(tmp_path, [rec_with, rec_without])
    out = load_batters_synthetic(path, {}, qualified_pa=100)
    by_id = {h.player.id: h for h in out}
    # Without sprint data, SBN equals the raw scaffold value (5.0).
    assert by_id["without_sprint"].stats.sbn == pytest.approx(5.0)


def test_synthetic_pitchers_fall_back_to_league_whip_without_xwoba(tmp_path: Path):
    """When ``expected_statistics.xwOBA`` is missing, synthetic WHIP falls
    back to the league baseline (no ratio scaling possible)."""
    from mtbl_valuations.io.synthetic import load_pitchers_synthetic

    # First record provides xwOBA so league baseline is populated; second
    # record (the one we assert on) lacks it to trigger the fallback.
    seed = _syn_pitcher_record(with_xwoba=True, with_whiff=True)
    seed["id_espn"] = "seed"
    target = _syn_pitcher_record(with_xwoba=False, with_whiff=False)
    target["id_espn"] = "target"
    path = _write_pitchers(tmp_path, [seed, target])
    out = load_pitchers_synthetic(path, {}, qualified_pa=100)
    by_id = {p.player.id: p for p in out}
    # The seed defines lg_whip = its WHIP projection (1.20); the target
    # without xwOBA should match exactly.
    assert by_id["target"].stats.whip == pytest.approx(by_id["seed"].stats.whip * 1.0)  # both = lg_whip when xwoba ratio == 1
    # And without whiff, K/9 falls back to league baseline too.
    assert by_id["target"].stats.k9 == pytest.approx(9.0)  # lg_k9 from single seed
