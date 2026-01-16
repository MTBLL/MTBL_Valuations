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
from mtbl_valuations.engine.budget import calc_league_budget
from mtbl_valuations.engine.pipeline import run_trp_valuation
from mtbl_valuations.io.loader import (
    load_batters,
    load_budget_config,
    load_league_settings,
    load_pitchers,
)


@pytest.fixture
def fixtures_dir():
    """Return path to fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def league_summary(league_file):
    """Load league summary fixture."""
    with open(league_file) as f:
        return json.load(f)


@pytest.fixture
def batters_file(fixtures_dir):
    """Return path to batters fixture file."""
    return fixtures_dir / "batters_matched.json"


@pytest.fixture
def pitchers_file(fixtures_dir):
    """Return path to pitchers fixture file."""
    return fixtures_dir / "pitchers_matched.json"


@pytest.fixture
def league_file(fixtures_dir):
    """Return path to league summary fixture file."""
    return fixtures_dir / "league_10998_summary.json"


@pytest.fixture
def budget_config_file():
    """Create a temporary budget config file."""
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
            "IP": 0.15,
            "ERA": 0.15,
            "WHIP": 0.15,
            "K/9": 0.40,
            "SVHD": 0.15,
        },
        "replacement_tier_pct": 0.03,
        "min_replacement_tier_size": 3,
        "max_iterations": 10,
        "convergence_threshold": 0,
        "bench_reserve_per_team": 5,
        "pa_weights": {
            "C": 500,
            "default": 600,
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config, f)
        path = Path(f.name)

    yield path

    # Cleanup
    path.unlink()


@pytest.fixture
def output_dir():
    """Create a temporary output directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def run_trp(batters_file, pitchers_file, league_file, budget_config_file, output_dir):
    """
    Run TRP valuation and return pools.

    This fixture runs the full pipeline and parses the output to return
    the position pools for testing.
    """
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

    # For now, return output_dir so tests can inspect files
    # TODO: Parse output and reconstruct pools
    return output_dir


@pytest.fixture
def budget_config(budget_config_file):
    """Load budget config from file."""
    return load_budget_config(budget_config_file)


@pytest.fixture
def league_budget(league_file, budget_config) -> LeagueBudget:
    """Calculate league budget from league file."""
    league_settings = load_league_settings(league_file)
    return calc_league_budget(league_settings, budget_config)


@pytest.fixture
def league_settings(league_file) -> dict[str, Any]:
    """Load league settings from file."""
    return load_league_settings(league_file)


@pytest.fixture
def batters(batters_file) -> List[HitterPlayer]:
    """Load batters from file."""
    return load_batters(batters_file)


@pytest.fixture
def pitchers(pitchers_file) -> List[PitcherPlayer]:
    """Load pitchers from file."""
    return load_pitchers(pitchers_file)


@pytest.fixture
def players_from_hitters(batters) -> List[Player]:
    return [b.player for b in batters]


@pytest.fixture
def players_from_pitchers(pitchers) -> List[Player]:
    return [p.player for p in pitchers]
