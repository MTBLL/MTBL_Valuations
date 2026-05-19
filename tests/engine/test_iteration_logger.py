"""Tests for the IterationLogger module.

Hand-builds minimal PositionPool snapshots and asserts the logger writes
the expected files / records the expected warnings.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mtbl_valuations.domain.models import HitterStats, Player, PositionPool
from mtbl_valuations.engine.iteration_logger import (
    INSIGHTS,
    IterationLogger,
    current_logger,
    current_phase,
    parse_iter_log_level,
    pop_logger,
    push_logger,
    push_phase,
)


def _hitter(pid: str, name: str, r: float = 80.0, obp: float = 0.350) -> Player:
    return Player(
        id=pid,
        name=name,
        team="T",
        positions=["SS"],
        role="HITTER",
        stats=HitterStats(
            pa=600,
            ab=540,
            r=r,
            hr=20,
            rbi=70,
            sbn=10,
            obp=obp,
            slg=0.450,
        ),
    )


def _ss_pool(*, with_baseline_shift: bool = False, with_neg_dpz: bool = False,
             rlp_outprices: bool = False) -> PositionPool:
    """Build an SS pool with controllable warning triggers.

    - ``with_baseline_shift``: sets z_baseline_shift[R] > 1.0 (warning trigger)
    - ``with_neg_dpz``: sets one dollars_per_z entry < 0
    - ``rlp_outprices``: makes a replacement player's total_$ > lowest rostered
    """
    rost_a = _hitter("r1", "Star", r=100.0)
    rost_b = _hitter("r2", "Solid", r=85.0)
    rlp_a = _hitter("p1", "Bench", r=60.0)

    # Settle z + dollars on the players so log_iter / log_budget can read them.
    rost_a.valuation.normalized_z = {"R": 2.5, "OBP": 2.0}
    rost_a.valuation.total_z = 4.5
    rost_a.valuation.dollar_values = {"R": 10.0, "OBP": 8.0}
    rost_a.valuation.total_dollars = 18.0

    rost_b.valuation.normalized_z = {"R": 1.5, "OBP": 1.2}
    rost_b.valuation.total_z = 2.7
    rost_b.valuation.dollar_values = {"R": 5.0, "OBP": 4.0}
    # Trigger ``rlp_outprices_rostered`` by lowering this rostered player.
    rost_b.valuation.total_dollars = 4.0 if rlp_outprices else 9.0

    rlp_a.valuation.normalized_z = {"R": 0.2, "OBP": 0.1}
    rlp_a.valuation.total_z = 0.3
    rlp_a.valuation.dollar_values = {"R": 4.0, "OBP": 3.0}
    rlp_a.valuation.total_dollars = 7.0

    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.rostered_players = [rost_a, rost_b]
    pool.replacement_players = [rlp_a]
    pool.below_replacement = []
    pool.rostered_tier_stdevs = {"R": 10.0, "OBP": 0.020}
    pool.rlp_raw_avg = {"R": 60.0, "OBP": 0.310}
    pool.category_budgets = {"R": 20.0, "OBP": 25.0}
    pool.dollars_per_z = {
        "R": -0.5 if with_neg_dpz else 1.5,
        "OBP": 1.2,
    }
    pool.total_pool_z = {"R": 4.0, "OBP": 3.2}
    pool.production_share = {"R": 0.4, "OBP": 0.5}
    pool.z_baseline_shift = {
        "R": 1.5 if with_baseline_shift else 0.0,
        "OBP": 0.0,
    }
    return pool


@pytest.fixture
def logger(tmp_path: Path) -> IterationLogger:
    return IterationLogger(run_dir=tmp_path, source="updated", level=INSIGHTS)


# ----- parse_iter_log_level + ContextVar helpers ----------------------


def test_parse_iter_log_level_none_returns_none():
    assert parse_iter_log_level(None) is None


def test_parse_iter_log_level_named_levels():
    assert parse_iter_log_level("INSIGHTS") == INSIGHTS
    assert parse_iter_log_level("debug") == 10  # logging.DEBUG


def test_parse_iter_log_level_unknown_raises():
    with pytest.raises(KeyError):
        parse_iter_log_level("UNKNOWN")


def test_push_pop_logger_round_trip(tmp_path: Path):
    assert current_logger() is None
    lg = IterationLogger(run_dir=tmp_path, source="s", level=INSIGHTS)
    token = push_logger(lg)
    assert current_logger() is lg
    pop_logger(token)
    assert current_logger() is None


def test_push_phase_sets_contextvar():
    token = push_phase("phase3b-iter")
    try:
        assert current_phase() == "phase3b-iter"
    finally:
        # Reset via separate token import path
        from mtbl_valuations.engine.iteration_logger import _phase_var
        _phase_var.reset(token)


# ----- log_iter -------------------------------------------------------


def test_log_iter_skips_unrecognized_phase(logger: IterationLogger):
    pool = _ss_pool()
    logger.log_iter(pool, "not-a-real-phase", iteration=0, per_position=False,
                    categories=["R", "OBP"])
    # No file should be created for an unrecognized phase.
    assert not (logger.run_dir / "updated").exists() or not list(
        (logger.run_dir / "updated").iterdir()
    )


def test_log_iter_writes_banner_and_player_rows(logger: IterationLogger):
    pool = _ss_pool()
    logger.log_iter(pool, "phase3b-iter", iteration=0, per_position=False,
                    categories=["R", "OBP"])
    out = logger.run_dir / "updated" / "SS.log"
    assert out.exists()
    text = out.read_text()
    assert "PHASE: phase3b-iter" in text
    assert "POS: SS" in text
    assert "ITER: 0" in text
    assert "composition_hash:" in text
    # Player rows present
    assert "Star" in text
    assert "Solid" in text
    assert "Bench" in text
    # RLP block present
    assert "RLP / scale" in text


def test_log_iter_tracks_composition_change(logger: IterationLogger):
    pool = _ss_pool()
    logger.log_iter(pool, "phase3b-iter", iteration=0, per_position=False,
                    categories=["R", "OBP"])
    # Swap a player tier to change composition
    new_rost = pool.replacement_players[0]
    demoted = pool.rostered_players[-1]
    pool.rostered_players = [pool.rostered_players[0], new_rost]
    pool.replacement_players = [demoted]
    logger.log_iter(pool, "phase3b-iter", iteration=1, per_position=False,
                    categories=["R", "OBP"])
    text = (logger.run_dir / "updated" / "SS.log").read_text()
    assert "DIFFERENT" in text
    assert "tier moves vs prev iter" in text


# ----- log_budget -----------------------------------------------------


def test_log_budget_writes_table_and_player_dollars(logger: IterationLogger):
    pool = _ss_pool()
    logger.log_budget(pool, "phase5-budget", per_position=False,
                      categories=["R", "OBP"],
                      league_raw={"R": 200.0, "OBP": 0.700},
                      league_budget={"R": 50.0, "OBP": 60.0})
    out = logger.run_dir / "updated" / "SS.log"
    text = out.read_text()
    assert "PHASE: phase5-budget" in text
    assert "league_raw" in text
    assert "league_budget" in text
    assert "category budgets" in text
    assert "players (rostered + replacement, by $)" in text


def test_log_budget_omits_league_cols_when_none(logger: IterationLogger):
    pool = _ss_pool()
    logger.log_budget(pool, "phase8-budget", per_position=False,
                      categories=["R", "OBP"])
    text = (logger.run_dir / "updated" / "SS.log").read_text()
    assert "league_raw" not in text
    assert "league_budget" not in text


def test_log_budget_flags_baseline_shift(logger: IterationLogger):
    pool = _ss_pool(with_baseline_shift=True)
    logger.log_budget(pool, "phase5-budget", per_position=False,
                      categories=["R", "OBP"])
    kinds = [w["kind"] for w in logger.warnings]
    assert "baseline_shift" in kinds


def test_log_budget_flags_negative_dollars_per_z(logger: IterationLogger):
    pool = _ss_pool(with_neg_dpz=True)
    logger.log_budget(pool, "phase5-budget", per_position=False,
                      categories=["R", "OBP"])
    kinds = [w["kind"] for w in logger.warnings]
    assert "negative_dollars_per_z" in kinds


def test_log_budget_flags_rlp_outprices_rostered(logger: IterationLogger):
    pool = _ss_pool(rlp_outprices=True)
    logger.log_budget(pool, "phase5-budget", per_position=False,
                      categories=["R", "OBP"])
    kinds = [w["kind"] for w in logger.warnings]
    assert "rlp_outprices_rostered" in kinds


def test_log_budget_flags_nonpositive_rostered_dollars(logger: IterationLogger):
    pool = _ss_pool()
    # Force one rostered player to total_$ = 0 → triggers warning.
    pool.rostered_players[1].valuation.total_dollars = 0.0
    logger.log_budget(pool, "phase5-budget", per_position=False,
                      categories=["R", "OBP"])
    kinds = [w["kind"] for w in logger.warnings]
    assert "nonpositive_rostered_dollars" in kinds


# ----- log_converged --------------------------------------------------


def test_log_converged_records_convergence(logger: IterationLogger):
    logger.log_converged("phase3b-iter", "SS", iters_run=3, converged=True,
                        max_iters=10)
    assert len(logger.convergence) == 1
    assert logger.convergence[0]["converged"] is True
    assert not any(w["kind"] == "max_iter_reached" for w in logger.warnings)


def test_log_converged_flags_max_iter_reached(logger: IterationLogger):
    logger.log_converged("phase3b-iter", "SS", iters_run=10, converged=False,
                        max_iters=10)
    kinds = [w["kind"] for w in logger.warnings]
    assert "max_iter_reached" in kinds


def test_log_converged_flags_oscillation(logger: IterationLogger):
    logger.log_converged("phase3b-iter", "SS", iters_run=8, converged=False,
                        max_iters=10, oscillating=True, best_iter=3)
    kinds = [w["kind"] for w in logger.warnings]
    assert "oscillation_resolved" in kinds
    # An oscillation should NOT also produce max_iter_reached.
    assert "max_iter_reached" not in kinds


def test_log_converged_skips_unrecognized_phase(logger: IterationLogger):
    logger.log_converged("not-a-real-phase", "SS", iters_run=10, converged=False,
                        max_iters=10)
    assert logger.convergence == []
    assert logger.warnings == []


# ----- finalize_summary -----------------------------------------------


def test_finalize_summary_writes_file_with_no_warnings(logger: IterationLogger):
    logger.log_converged("phase3b-iter", "SS", iters_run=3, converged=True,
                        max_iters=10)
    logger.finalize_summary()
    summary = logger.run_dir / "updated_summary.log"
    assert summary.exists()
    text = summary.read_text()
    assert "CONVERGENCE" in text
    assert "WARNINGS (0)" in text
    assert "none" in text


def test_finalize_summary_writes_warnings(logger: IterationLogger):
    pool = _ss_pool(with_baseline_shift=True)
    logger.log_budget(pool, "phase5-budget", per_position=False,
                      categories=["R", "OBP"])
    logger.finalize_summary()
    text = (logger.run_dir / "updated_summary.log").read_text()
    assert "WARNINGS (1)" in text
    assert "baseline_shift" in text
