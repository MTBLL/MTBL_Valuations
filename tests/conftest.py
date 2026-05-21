"""Shared test fixtures and helpers."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import List

import pytest
from typing_extensions import Any

from mtbl_valuations.domain.models import (
    HitterPlayer,
    LeagueBudget,
    PitcherPlayer,
    Player,
)
from mtbl_valuations.config import TRANSFORM_OUTPUT_DIR
from mtbl_valuations.engine.budget import calc_league_budget
from mtbl_valuations.engine.pipeline import run_trp_valuation
from mtbl_valuations.io.loader import (
    load_batters,
    load_budget_config,
    load_league_settings,
    load_pitchers,
)

# Import cached fixtures
from tests.cache_fixtures import (
    converged_hitter_pools,
    converged_hitter_pools_deduped,
    converged_rp_pool,
    converged_sp_pool,
    hitter_pools_deduped_converged,
    hitter_pools_with_budgets_phase5,
    hitter_pools_with_util_pool_converged_phase4b,
    pitchers_with_dollars_phase8,
    rp_pool_phase6c,
    rp_pool_with_budget_phase7,
    sp_pool_phase6a,
    sp_pool_with_budget_phase7,
    use_test_cache,
    util_pool_phase4a,
)

# Make cached fixtures available
__all__ = [
    "converged_hitter_pools",
    "converged_hitter_pools_deduped",
    "converged_rp_pool",
    "converged_sp_pool",
    "hitter_pools_deduped_converged",
    "hitter_pools_with_budgets_phase5",
    "hitter_pools_with_util_pool_converged_phase4b",
    "pitchers_with_dollars_phase8",
    "rp_pool_phase6c",
    "rp_pool_with_budget_phase7",
    "sp_pool_phase6a",
    "sp_pool_with_budget_phase7",
    "use_test_cache",
    "util_pool_phase4a",
]


@pytest.fixture(scope="session")
def fixtures_dir():
    """Return path to fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def league_summary(league_file):
    """Load league summary fixture."""
    with open(league_file) as f:
        return json.load(f)


@pytest.fixture(scope="session")
def batters_file():
    """Path to the live transform batters file — real-scale data, so the
    tests exercise the same pool sizes the production pipeline sees."""
    return TRANSFORM_OUTPUT_DIR / "batters_matched.json"


@pytest.fixture(scope="session")
def pitchers_file():
    """Path to the live transform pitchers file (real-scale data)."""
    return TRANSFORM_OUTPUT_DIR / "pitchers_matched.json"


@pytest.fixture(scope="session")
def league_file():
    """Path to the live transform league summary (real-scale data)."""
    return TRANSFORM_OUTPUT_DIR / "league_10998_summary.json"


@pytest.fixture(scope="session")
def budget_config_file(tmp_path_factory):
    """Create a temporary budget config file for the session."""
    config = {
        "hitter_pitcher_split": [0.70, 0.30],
        "sp_rp_split": [0.50, 0.50],
        "hitter_category_weights": {
            "R": 0.125,
            "HR": 0.125,
            "RBI": 0.125,
            "SBN": 0.125,
            "OBP": 0.25,
            "SLG": 0.25,
        },
        "sp_category_weights": {
            "IP": 0.15,
            "ERA": 0.15,
            "WHIP": 0.15,
            "K/9": 0.40,
            "QS": 0.15,
        },
        "rp_category_weights": {
            "IP": 0.0,
            "ERA": 0.20,
            "WHIP": 0.20,
            "K/9": 0.40,
            "SVHD": 0.20,
        },
        "replacement_tier_pct": 0.5,
        "min_replacement_tier_size": 3,
        "rlp_archetype": {"trim_top_pct": 0.0, "sbn_global_mu": 1.0},
        # Match the production budget_config (repo-root budget_config.json).
        # max_iterations=5 is intentionally low enough that pools hit the
        # cap without natural convergence — the swap-pass + reconciliation
        # must keep budgets balanced anyway. Higher values mask the
        # dual-rostered / stale-primary regression.
        "max_iterations": 5,
        "convergence_threshold": 0,
        "bench_reserve_per_team": 5,
        "pa_weights": {
            "C": 500,
            "default": 600,
        },
    }

    # Use pytest's tmp_path_factory for session-scoped temp files
    tmpdir = tmp_path_factory.mktemp("config")
    path = tmpdir / "budget_config.json"

    with open(path, "w") as f:
        json.dump(config, f)

    return path


@pytest.fixture
def output_dir():
    """Create a temporary output directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(scope="session")
def run_trp_session(batters_file, pitchers_file, league_file, budget_config_file, tmp_path_factory):
    """
    Run TRP valuation once per session and cache the output directory.

    This fixture runs the full pipeline once and returns the output directory
    for all tests to use. Much faster than running the pipeline for each test.
    """
    # Create session-scoped temp directory
    output_dir = tmp_path_factory.mktemp("trp_output")

    # Run the pipeline
    run_trp_valuation(
        batters_file,
        pitchers_file,
        league_file,
        budget_config_file,
        output_dir,
    )

    # Load the position summary to understand pool structure
    position_summary_file = output_dir / "position_summary.csv"
    assert position_summary_file.exists(), "position_summary.csv should be created"

    return output_dir


@pytest.fixture
def run_trp(run_trp_session):
    """
    Alias for session-scoped run_trp fixture.

    Provides the output directory from a single pipeline run.
    Tests should only read from this directory, not modify it.
    """
    return run_trp_session


@pytest.fixture(scope="session")
def budget_config(budget_config_file):
    """Load budget config from file."""
    return load_budget_config(budget_config_file)


@pytest.fixture(scope="session")
def qualified_pa(batters_file, budget_config) -> float:
    """Sliding qualified-PA threshold for the current/synthetic sources."""
    from mtbl_valuations.io.qualified import compute_qualified_pa

    return compute_qualified_pa(batters_file, budget_config)


@pytest.fixture(scope="session")
def league_budget(league_file, budget_config) -> LeagueBudget:
    """Calculate league budget from league file."""
    league_settings = load_league_settings(league_file)
    return calc_league_budget(league_settings, budget_config)


@pytest.fixture(scope="session")
def league_settings(league_file) -> dict[str, Any]:
    """Load league settings from file."""
    return load_league_settings(league_file)


@pytest.fixture(scope="session")
def batters(batters_file) -> List[HitterPlayer]:
    """Load batters from file."""
    return load_batters(batters_file)


@pytest.fixture(scope="session")
def pitchers(pitchers_file) -> List[PitcherPlayer]:
    """Load pitchers from file."""
    return load_pitchers(pitchers_file)


@pytest.fixture(scope="session")
def players_from_hitters(batters) -> List[Player]:
    return [b.player for b in batters]


@pytest.fixture(scope="session")
def players_from_pitchers(pitchers) -> List[Player]:
    return [p.player for p in pitchers]


def pytest_addoption(parser):
    """Add custom pytest command-line options."""
    parser.addoption(
        "--no-cache",
        action="store_true",
        default=False,
        help="Disable test fixture caching for convergence operations",
    )
