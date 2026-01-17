"""Session-scoped cached fixtures for expensive convergence operations."""

from __future__ import annotations

import hashlib
import pickle
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from mtbl_valuations.engine.iteration import iterate_to_convergence
from mtbl_valuations.engine.pools import dedupe_multi_position_players

if TYPE_CHECKING:
    from mtbl_valuations.domain.models import PositionPool

# Cache directory for phase results
CACHE_DIR = Path(__file__).parent / ".cache" / "phases"


def _cache_key(*inputs: Any) -> str:
    """
    Generate cache key from inputs.

    Args:
        *inputs: Variable number of inputs to hash (usually file contents)

    Returns:
        12-character hex digest for cache filename
    """
    content = str(inputs).encode()
    return hashlib.sha256(content).hexdigest()[:12]


@pytest.fixture(scope="session")
def use_test_cache(request: pytest.FixtureRequest) -> bool:
    """
    Control flag for enabling cache.

    Checks for --no-cache command-line flag.

    Args:
        request: Pytest fixture request

    Returns:
        True to enable caching (default), False if --no-cache flag is set
    """
    # Check if --no-cache flag was provided
    no_cache = request.config.getoption("--no-cache")
    return not no_cache


@pytest.fixture(scope="session")
def converged_hitter_pools(
    regular_hitter_pools: dict[str, PositionPool],
    budget_config: dict[str, Any],
    league_settings: dict[str, Any],
    batters_file: Path,
    league_file: Path,
    budget_config_file: Path,
    use_test_cache: bool,
) -> dict[str, PositionPool]:
    """
    Phase 3: Cached converged hitter pools (pre-dedupe).

    Expensive operation: iterate_to_convergence() with up to 10 iterations.
    Cache key includes batters, league settings, and budget config.

    Args:
        regular_hitter_pools: Built position pools from regular_hitter_pools fixture
        budget_config: Budget configuration
        league_settings: League settings
        batters_file: Path to batters fixture file (for cache key)
        league_file: Path to league fixture file (for cache key)
        budget_config_file: Path to budget config file (for cache key)
        use_test_cache: Whether to use caching

    Returns:
        Converged hitter pools with valuation data
    """
    if not use_test_cache:
        return iterate_to_convergence(
            regular_hitter_pools,
            budget_config,
            league_settings,
            track_z_per_pool=True,
        )

    # Generate cache key from input files
    key = _cache_key(
        batters_file.read_text(),
        league_file.read_text(),
        budget_config_file.read_text(),
        "phase3_converged_hitters",
    )

    cache_file = CACHE_DIR / f"phase3_{key}.pkl"

    # Try to load from cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Cache corrupted - fall through to recompute
            pass

    # Not cached or corrupted - run expensive operation
    result = iterate_to_convergence(
        regular_hitter_pools,
        budget_config,
        league_settings,
        track_z_per_pool=True,
    )

    # Save to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(result, f)

    return result


@pytest.fixture(scope="session")
def converged_hitter_pools_deduped(
    converged_hitter_pools: dict[str, PositionPool],
    budget_config: dict[str, Any],
    league_settings: dict[str, Any],
    batters_file: Path,
    league_file: Path,
    budget_config_file: Path,
    use_test_cache: bool,
) -> dict[str, PositionPool]:
    """
    Phase 3b: Cached post-dedupe hitter pools.

    Expensive operation: dedupe_multi_position_players() + re-iteration.
    Cache key includes batters, league settings, and budget config.

    Args:
        converged_hitter_pools: Converged pools from phase 3
        budget_config: Budget configuration
        league_settings: League settings
        batters_file: Path to batters fixture file (for cache key)
        league_file: Path to league fixture file (for cache key)
        budget_config_file: Path to budget config file (for cache key)
        use_test_cache: Whether to use caching

    Returns:
        Final single-position hitter pools after deduplication
    """
    if not use_test_cache:
        deduped, _ = dedupe_multi_position_players(converged_hitter_pools)
        return iterate_to_convergence(
            deduped,
            budget_config,
            league_settings,
            track_z_per_pool=True,
        )

    # Generate cache key from input files
    key = _cache_key(
        batters_file.read_text(),
        league_file.read_text(),
        budget_config_file.read_text(),
        "phase3b_deduped_hitters",
    )

    cache_file = CACHE_DIR / f"phase3b_{key}.pkl"

    # Try to load from cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Cache corrupted - fall through to recompute
            pass

    # Not cached or corrupted - run expensive operation
    deduped, _ = dedupe_multi_position_players(converged_hitter_pools)
    result = iterate_to_convergence(
        deduped,
        budget_config,
        league_settings,
        track_z_per_pool=True,
    )

    # Save to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(result, f)

    return result
