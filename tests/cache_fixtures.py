"""Session-scoped cached fixtures for expensive convergence operations."""

from __future__ import annotations

import hashlib
import pickle
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from mtbl_valuations.engine.budget import (
    allocate_pool_budget,
    allocate_position_budgets,
    calc_pool_dollars_per_z,
)
from mtbl_valuations.engine.iteration import iterate_to_convergence
from mtbl_valuations.engine.pools import (
    build_pitcher_pool,
    build_util_pool,
    dedupe_multi_position_players,
)
from mtbl_valuations.engine.valuation import distribute_player_dollars

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
    Phase 3b: Cached converged hitter pools (pre-dedupe).

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
    batters_file: Path,
    league_file: Path,
    budget_config_file: Path,
    use_test_cache: bool,
) -> tuple[dict[str, PositionPool], int]:
    """
    Phase 3c: Cached post-dedupe hitter pools.

    Expensive operation: dedupe_multi_position_players() + re-iteration.
    Cache key includes batters, league settings, and budget config.

    Args:
        converged_hitter_pools: Converged pools from phase 3
        budget_config: Budget configuration
        batters_file: Path to batters fixture file (for cache key)
        league_file: Path to league fixture file (for cache key)
        budget_config_file: Path to budget config file (for cache key)
        use_test_cache: Whether to use caching

    Returns:
        Final single-position hitter pools after deduplication
    """
    if not use_test_cache:
        return dedupe_multi_position_players(
            converged_hitter_pools,
            budget_config["replacement_tier_pct"],
            budget_config["min_replacement_tier_size"],
        )

    # Generate cache key from input files
    key = _cache_key(
        batters_file.read_text(),
        league_file.read_text(),
        budget_config_file.read_text(),
        "phase3b_deduped_hitters",
    )

    cache_file = CACHE_DIR / f"phase3c_{key}.pkl"

    # Try to load from cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Cache corrupted - fall through to recompute
            pass

    # Not cached or corrupted - run expensive operation
    deduped, num_dedupes = dedupe_multi_position_players(
        converged_hitter_pools,
        budget_config["replacement_tier_pct"],
        budget_config["min_replacement_tier_size"],
    )

    # Save to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    result = (deduped, num_dedupes)
    with open(cache_file, "wb") as f:
        pickle.dump(result, f)

    return result


@pytest.fixture(scope="session")
def hitter_pools_deduped_converged(
    converged_hitter_pools_deduped,
    budget_config,
    league_settings,
    batters_file: Path,
    league_file: Path,
    budget_config_file: Path,
    use_test_cache: bool,
) -> dict[str, PositionPool]:
    deduped, _ = converged_hitter_pools_deduped
    if not use_test_cache:
        return iterate_to_convergence(deduped, budget_config, league_settings)

    # Generate cache key from input files
    key = _cache_key(
        batters_file.read_text(),
        league_file.read_text(),
        budget_config_file.read_text(),
        "phase3c_deduped_hitters",
    )

    cache_file = CACHE_DIR / f"phase3d_{key}.pkl"

    # Try to load from cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Cache corrupted - fall through to recompute
            pass

    # Not cached or corrupted - run expensive operation
    result = iterate_to_convergence(deduped, budget_config, league_settings)

    # Save to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(result, f)

    return result


@pytest.fixture(scope="session")
def util_pool_phase4a(
    hitter_pools_deduped_converged: dict[str, PositionPool],
    dh_and_regular_hitters,
    budget_config: dict[str, Any],
    league_settings: dict[str, Any],
    batters_file: Path,
    league_file: Path,
    budget_config_file: Path,
    use_test_cache: bool,
) -> PositionPool:
    """
    Phase 4a: Cached UTIL pool built from replacement-tier players + pure DHs.

    Expensive operation: build_util_pool() which collects and sorts players.
    Cache key includes batters, league settings, and budget config.

    Args:
        hitter_pools_deduped_converged: Converged pools after deduplication
        dh_and_regular_hitters: Tuple of (pure_dh_players, regular_hitters)
        budget_config: Budget configuration
        league_settings: League settings
        batters_file: Path to batters fixture file (for cache key)
        league_file: Path to league fixture file (for cache key)
        budget_config_file: Path to budget config file (for cache key)
        use_test_cache: Whether to use caching

    Returns:
        UTIL position pool
    """
    pure_dh_players, _ = dh_and_regular_hitters
    rlp_tier_pct = budget_config["replacement_tier_pct"]
    min_rlp_tier_size = budget_config["min_replacement_tier_size"]

    if not use_test_cache:
        return build_util_pool(
            hitter_pools_deduped_converged,
            pure_dh_players,
            league_settings["roster_slots"],
            league_settings["num_teams"],
            rlp_tier_pct,
            min_rlp_tier_size,
        )

    # Generate cache key from input files
    key = _cache_key(
        batters_file.read_text(),
        league_file.read_text(),
        budget_config_file.read_text(),
        "phase4a_util_pool",
    )

    cache_file = CACHE_DIR / f"phase4a_{key}.pkl"

    # Try to load from cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Cache corrupted - fall through to recompute
            pass

    # Not cached or corrupted - run expensive operation
    result = build_util_pool(
        hitter_pools_deduped_converged,
        pure_dh_players,
        league_settings["roster_slots"],
        league_settings["num_teams"],
        rlp_tier_pct,
        min_rlp_tier_size,
    )

    # Save to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(result, f)

    return result


@pytest.fixture(scope="session")
def hitter_pools_with_util_pool_converged_phase4b(
    hitter_pools_deduped_converged: dict[str, PositionPool],
    util_pool_phase4a: PositionPool,
    budget_config: dict[str, Any],
    league_settings: dict[str, Any],
    batters_file: Path,
    league_file: Path,
    budget_config_file: Path,
    use_test_cache: bool,
) -> dict[str, PositionPool]:
    """
    Phase 4b: Cached converged UTIL pool with composite RLP baseline.

    Expensive operation: iterate_to_convergence() on UTIL pool.
    Cache key includes batters, league settings, and budget config.

    Args:
        util_pool_phase4a: UTIL pool from Phase 4a
        budget_config: Budget configuration
        league_settings: League settings
        batters_file: Path to batters fixture file (for cache key)
        league_file: Path to league fixture file (for cache key)
        budget_config_file: Path to budget config file (for cache key)
        use_test_cache: Whether to use caching

    Returns:
        Converged UTIL pool
    """
    hitter_pools = hitter_pools_deduped_converged
    if not use_test_cache:
        results = iterate_to_convergence(
            {"UTIL": util_pool_phase4a},
            budget_config,
            league_settings,
        )["UTIL"]
        hitter_pools["UTIL"] = results
        return hitter_pools

    # Generate cache key from input files
    key = _cache_key(
        batters_file.read_text(),
        league_file.read_text(),
        budget_config_file.read_text(),
        "phase4b_util_pool_converged",
    )

    cache_file = CACHE_DIR / f"phase4b_{key}.pkl"

    # Try to load from cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Cache corrupted - fall through to recompute
            pass

    # Not cached or corrupted - run expensive operation
    results = iterate_to_convergence(
        {"UTIL": util_pool_phase4a},
        budget_config,
        league_settings,
    )["UTIL"]
    hitter_pools["UTIL"] = results

    # Save to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(hitter_pools, f)

    return hitter_pools


@pytest.fixture(scope="session")
def hitter_pools_with_budgets_phase5(
    hitter_pools_with_util_pool_converged_phase4b: dict[str, PositionPool],
    league_budget,
    budget_config: dict[str, Any],
    batters_file: Path,
    league_file: Path,
    budget_config_file: Path,
    use_test_cache: bool,
) -> dict[str, PositionPool]:
    """
    Phase 5: Cached hitter pools with budget allocation and dollar distribution.

    Expensive operations:
    - allocate_position_budgets() - distributes category budgets to positions
    - calc_pool_dollars_per_z() - calculates $/Z conversion rates
    - distribute_player_dollars() - assigns dollar values to each player

    Cache key includes batters, league settings, and budget config.

    Args:
        hitter_pools_with_util_pool_converged_phase4b: Complete hitter pools from Phase 4b
        league_budget: League budget calculation
        budget_config: Budget configuration
        batters_file: Path to batters fixture file (for cache key)
        league_file: Path to league fixture file (for cache key)
        budget_config_file: Path to budget config file (for cache key)
        use_test_cache: Whether to use caching

    Returns:
        Dictionary of all hitter pools with budgets and dollar values assigned
    """
    if not use_test_cache:
        pools = allocate_position_budgets(
            hitter_pools_with_util_pool_converged_phase4b,
            league_budget,
            budget_config,
        )
        pools = calc_pool_dollars_per_z(pools)

        # Distribute dollars to all hitter players
        for _, pool in pools.items():
            for player in pool.rostered_players + pool.replacement_players:
                dollar_values = distribute_player_dollars(player, pool)
                total_dollars = sum(dollar_values.values())
                player.valuation.dollar_values = dollar_values
                player.valuation.total_dollars = total_dollars

        return pools

    # Generate cache key from input files
    key = _cache_key(
        batters_file.read_text(),
        league_file.read_text(),
        budget_config_file.read_text(),
        "phase5_hitter_budgets",
    )

    cache_file = CACHE_DIR / f"phase5_{key}.pkl"

    # Try to load from cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Cache corrupted - fall through to recompute
            pass

    # Not cached or corrupted - run expensive operation
    pools = allocate_position_budgets(
        hitter_pools_with_util_pool_converged_phase4b,
        league_budget,
        budget_config,
    )
    pools = calc_pool_dollars_per_z(pools)

    # Distribute dollars to all hitter players
    for _, pool in pools.items():
        for player in pool.rostered_players + pool.replacement_players:
            dollar_values = distribute_player_dollars(player, pool)
            total_dollars = sum(dollar_values.values())
            player.valuation.dollar_values = dollar_values
            player.valuation.total_dollars = total_dollars

    # Save to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(pools, f)

    return pools


@pytest.fixture(scope="session")
def sp_pool_phase6a(
    starters,
    league_settings: dict[str, Any],
    budget_config: dict[str, Any],
    pitchers_file: Path,
    league_file: Path,
    budget_config_file: Path,
    use_test_cache: bool,
) -> dict[str, PositionPool]:
    """
    Phase 6a: Cached SP (starting pitcher) pool.

    Expensive operation: build_pitcher_pool() which builds rostered and replacement tiers.
    Cache key includes pitchers, league settings, and budget config.

    Args:
        starters: List of starting pitcher Player objects
        league_settings: League settings
        budget_config: Budget configuration
        pitchers_file: Path to pitchers fixture file (for cache key)
        league_file: Path to league fixture file (for cache key)
        budget_config_file: Path to budget config file (for cache key)
        use_test_cache: Whether to use caching

    Returns:
        Dictionary with single key "SP" containing the SP PositionPool
    """
    rlp_tier_pct = budget_config["replacement_tier_pct"]
    min_rlp_tier_size = budget_config["min_replacement_tier_size"]

    if not use_test_cache:
        return {
            "SP": build_pitcher_pool(
                starters,
                league_settings["roster_slots"],
                league_settings["num_teams"],
                "SP",
                rlp_tier_pct,
                min_rlp_tier_size,
            )
        }

    # Generate cache key from input files
    key = _cache_key(
        pitchers_file.read_text(),
        league_file.read_text(),
        budget_config_file.read_text(),
        "phase6a_sp_pool",
    )

    cache_file = CACHE_DIR / f"phase6a_{key}.pkl"

    # Try to load from cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Cache corrupted - fall through to recompute
            pass

    # Not cached or corrupted - run expensive operation
    result = {
        "SP": build_pitcher_pool(
            starters,
            league_settings["roster_slots"],
            league_settings["num_teams"],
            "SP",
            rlp_tier_pct,
            min_rlp_tier_size,
        )
    }

    # Save to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(result, f)

    return result


@pytest.fixture(scope="session")
def converged_sp_pool(
    sp_pool_phase6a,
    league_settings: dict[str, Any],
    budget_config: dict[str, Any],
    pitchers_file: Path,
    league_file: Path,
    budget_config_file: Path,
    use_test_cache: bool,
) -> dict[str, PositionPool]:
    if not use_test_cache:
        return iterate_to_convergence(sp_pool_phase6a, budget_config, league_settings)
    # Generate cache key from input files
    key = _cache_key(
        pitchers_file.read_text(),
        league_file.read_text(),
        budget_config_file.read_text(),
        "phase6b_converged_sp_pool",
    )

    cache_file = CACHE_DIR / f"phase6b_converged_sp_{key}.pkl"

    # Try to load from cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Cache corrupted - fall through to recompute
            pass

    # Not cached or corrupted - run expensive operation
    result = iterate_to_convergence(sp_pool_phase6a, budget_config, league_settings)

    # Save to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(result, f)

    return result


@pytest.fixture(scope="session")
def rp_pool_phase6c(
    relievers,
    league_settings: dict[str, Any],
    budget_config: dict[str, Any],
    pitchers_file: Path,
    league_file: Path,
    budget_config_file: Path,
    use_test_cache: bool,
) -> dict[str, PositionPool]:
    """
    Phase 6c: Cached RP (relief pitcher) pool.

    Expensive operation: build_pitcher_pool() which builds rostered and replacement tiers.
    Cache key includes pitchers, league settings, and budget config.

    Args:
        relievers: List of relief pitcher Player objects
        league_settings: League settings
        budget_config: Budget configuration
        pitchers_file: Path to pitchers fixture file (for cache key)
        league_file: Path to league fixture file (for cache key)
        budget_config_file: Path to budget config file (for cache key)
        use_test_cache: Whether to use caching

    Returns:
        Dictionary with single key "RP" containing the RP PositionPool
    """
    rlp_tier_pct = budget_config["replacement_tier_pct"]
    min_rlp_tier_size = budget_config["min_replacement_tier_size"]

    if not use_test_cache:
        return {
            "RP": build_pitcher_pool(
                relievers,
                league_settings["roster_slots"],
                league_settings["num_teams"],
                "RP",
                rlp_tier_pct,
                min_rlp_tier_size,
            )
        }

    # Generate cache key from input files
    key = _cache_key(
        pitchers_file.read_text(),
        league_file.read_text(),
        budget_config_file.read_text(),
        "phase6c_rp_pool",
    )

    cache_file = CACHE_DIR / f"phase6c_rp_{key}.pkl"

    # Try to load from cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Cache corrupted - fall through to recompute
            pass

    # Not cached or corrupted - run expensive operation
    result = {
        "RP": build_pitcher_pool(
            relievers,
            league_settings["roster_slots"],
            league_settings["num_teams"],
            "RP",
            rlp_tier_pct,
            min_rlp_tier_size,
        )
    }

    # Save to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(result, f)

    return result


@pytest.fixture(scope="session")
def converged_rp_pool(
    rp_pool_phase6c,
    league_settings: dict[str, Any],
    budget_config: dict[str, Any],
    pitchers_file: Path,
    league_file: Path,
    budget_config_file: Path,
    use_test_cache: bool,
) -> dict[str, PositionPool]:
    if not use_test_cache:
        return iterate_to_convergence(rp_pool_phase6c, budget_config, league_settings)

    # Generate cache key from input files
    key = _cache_key(
        pitchers_file.read_text(),
        league_file.read_text(),
        budget_config_file.read_text(),
        "phase6d_converged_rp_pool",
    )

    cache_file = CACHE_DIR / f"phase6d_converged_rp_{key}.pkl"

    # Try to load from cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Cache corrupted - fall through to recompute
            pass

    # Not cached or corrupted - run expensive operation
    result = iterate_to_convergence(rp_pool_phase6c, budget_config, league_settings)

    # Save to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(result, f)

    return result


@pytest.fixture(scope="session")
def sp_pool_with_budget_phase7(
    converged_sp_pool,
    league_budget,
    budget_config: dict[str, Any],
    pitchers_file: Path,
    league_file: Path,
    budget_config_file: Path,
    use_test_cache: bool,
) -> dict[str, PositionPool]:
    """
    Phase 7a: Cached SP pool with allocated budgets and $/Z rates.

    Applies budget allocation and calculates dollars per Z for SP pool.
    Cache key includes pitchers, league settings, and budget config.

    Args:
        converged_sp_pool: Converged SP pool from phase 6b
        league_budget: League budget object containing SP budget allocation
        budget_config: Budget configuration with SP category weights
        pitchers_file: Path to pitchers fixture file (for cache key)
        league_file: Path to league fixture file (for cache key)
        budget_config_file: Path to budget config file (for cache key)
        use_test_cache: Whether to use caching

    Returns:
        Dictionary with single key "SP" containing the SP PositionPool with budgets
    """
    if not use_test_cache:
        sp_pool = {
            "SP": allocate_pool_budget(
                converged_sp_pool["SP"],
                league_budget.sp_budget,
                budget_config["sp_category_weights"],
            )
        }
        return calc_pool_dollars_per_z(sp_pool)

    # Generate cache key from input files
    key = _cache_key(
        pitchers_file.read_text(),
        league_file.read_text(),
        budget_config_file.read_text(),
        "phase7a_sp_pool_with_budget",
    )

    cache_file = CACHE_DIR / f"phase7a_sp_budget_{key}.pkl"

    # Try to load from cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Cache corrupted - fall through to recompute
            pass

    # Not cached or corrupted - run budget allocation
    sp_pool = {
        "SP": allocate_pool_budget(
            converged_sp_pool["SP"],
            league_budget.sp_budget,
            budget_config["sp_category_weights"],
        )
    }
    result = calc_pool_dollars_per_z(sp_pool)

    # Save to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(result, f)

    return result


@pytest.fixture(scope="session")
def rp_pool_with_budget_phase7(
    converged_rp_pool,
    league_budget,
    budget_config: dict[str, Any],
    pitchers_file: Path,
    league_file: Path,
    budget_config_file: Path,
    use_test_cache: bool,
) -> dict[str, PositionPool]:
    """
    Phase 7b: Cached RP pool with allocated budgets and $/Z rates.

    Applies budget allocation and calculates dollars per Z for RP pool.
    Cache key includes pitchers, league settings, and budget config.

    Args:
        converged_rp_pool: Converged RP pool from phase 6d
        league_budget: League budget object containing RP budget allocation
        budget_config: Budget configuration with RP category weights
        pitchers_file: Path to pitchers fixture file (for cache key)
        league_file: Path to league fixture file (for cache key)
        budget_config_file: Path to budget config file (for cache key)
        use_test_cache: Whether to use caching

    Returns:
        Dictionary with single key "RP" containing the RP PositionPool with budgets
    """
    if not use_test_cache:
        rp_pool = {
            "RP": allocate_pool_budget(
                converged_rp_pool["RP"],
                league_budget.rp_budget,
                budget_config["rp_category_weights"],
            )
        }
        return calc_pool_dollars_per_z(rp_pool)

    # Generate cache key from input files
    key = _cache_key(
        pitchers_file.read_text(),
        league_file.read_text(),
        budget_config_file.read_text(),
        "phase7b_rp_pool_with_budget",
    )

    cache_file = CACHE_DIR / f"phase7b_rp_budget_{key}.pkl"

    # Try to load from cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Cache corrupted - fall through to recompute
            pass

    # Not cached or corrupted - run budget allocation
    rp_pool = {
        "RP": allocate_pool_budget(
            converged_rp_pool["RP"],
            league_budget.rp_budget,
            budget_config["rp_category_weights"],
        )
    }
    result = calc_pool_dollars_per_z(rp_pool)

    # Save to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(result, f)

    return result


@pytest.fixture(scope="session")
def pitchers_with_dollars_phase8(
    sp_pool_with_budget_phase7,
    rp_pool_with_budget_phase7,
    pitchers_file: Path,
    league_file: Path,
    budget_config_file: Path,
    use_test_cache: bool,
) -> dict[str, PositionPool]:
    """
    Phase 8: Cached pitcher pools (SP and RP) with dollar values distributed.

    Distributes dollar values to individual pitchers based on their Z-scores
    and the pool's $/Z rates. Returns combined dictionary with both SP and RP.
    Cache key includes pitchers, league settings, and budget config.

    Args:
        sp_pool_with_budget_phase7: SP pool with allocated budgets from phase 7a
        rp_pool_with_budget_phase7: RP pool with allocated budgets from phase 7b
        pitchers_file: Path to pitchers fixture file (for cache key)
        league_file: Path to league fixture file (for cache key)
        budget_config_file: Path to budget config file (for cache key)
        use_test_cache: Whether to use caching

    Returns:
        Dictionary with keys "SP" and "RP" containing pools with dollar values
    """
    if not use_test_cache:
        pitchers = sp_pool_with_budget_phase7 | rp_pool_with_budget_phase7
        for _, pool in pitchers.items():
            for player in pool.rostered_players + pool.replacement_players:
                dollar_values = distribute_player_dollars(player, pool)
                total_dollars = sum(dollar_values.values())
                player.valuation.dollar_values = dollar_values
                player.valuation.total_dollars = total_dollars
                player.valuation.primary_position = pool.position
        return pitchers

    # Generate cache key from input files
    key = _cache_key(
        pitchers_file.read_text(),
        league_file.read_text(),
        budget_config_file.read_text(),
        "phase8_pitchers_with_dollars",
    )

    cache_file = CACHE_DIR / f"phase8_pitchers_{key}.pkl"

    # Try to load from cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Cache corrupted - fall through to recompute
            pass

    # Not cached or corrupted - run dollar distribution
    pitchers = sp_pool_with_budget_phase7 | rp_pool_with_budget_phase7
    for _, pool in pitchers.items():
        for player in pool.rostered_players + pool.replacement_players:
            dollar_values = distribute_player_dollars(player, pool)
            total_dollars = sum(dollar_values.values())
            player.valuation.dollar_values = dollar_values
            player.valuation.total_dollars = total_dollars
            player.valuation.primary_position = pool.position

    # Save to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(pitchers, f)

    return pitchers
