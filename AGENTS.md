# MTBL Valuations - Agent Instructions

## Running Tests

### Basic Test Execution

Run all tests:
```bash
python -m pytest tests/ -v
```

Run tests in a specific file:
```bash
python -m pytest tests/engine/test_iteration.py -v
```

Run a specific test:
```bash
python -m pytest tests/engine/test_iteration.py::TestIteration::test_iteration_to_convergence -v
```

### Test Caching System

The test suite uses a phase-based caching system to speed up tests by caching expensive convergence operations.

#### Using the Cache (Default)

By default, tests use cached convergence results:
```bash
python -m pytest tests/ -v
```

The cache is stored in `tests/.cache/phases/` and persists across test runs.

#### Bypassing the Cache

To run tests without using cached results:
```bash
python -m pytest tests/ -v --no-cache
```

This is useful when:
- You want to verify fresh computation results
- You're debugging convergence logic
- You want to measure actual performance without caching

#### Clearing the Cache

To manually clear all cached results:
```bash
rm -rf tests/.cache/phases/*.pkl
```

The cache will automatically regenerate on the next test run.

#### Cache Invalidation

The cache automatically invalidates when input data changes. Cache keys include:
- Batters fixture data
- League settings
- Budget configuration

If you modify any of these files, the cache will miss and regenerate automatically.

### Performance Measurement

View test execution times:
```bash
python -m pytest tests/ -v --durations=10
```

Compare with and without cache:
```bash
# Without cache (slower, fresh computation)
python -m pytest tests/engine/test_iteration.py --no-cache --durations=5

# With cache (faster, uses cached results)
python -m pytest tests/engine/test_iteration.py --durations=5
```

### Test Coverage

Run tests with coverage reporting:
```bash
python -m pytest tests/ --cov=mtbl_valuations --cov-report=term-missing
```

### Common Test Scenarios

#### Development Workflow
```bash
# Run specific test file while developing
python -m pytest tests/engine/test_pools.py -v

# Run with detailed output for debugging
python -m pytest tests/engine/test_pools.py -vv

# Run with print statements visible
python -m pytest tests/engine/test_pools.py -v -s
```

#### CI/CD Pipeline
```bash
# Run all tests with coverage (typical CI setup)
python -m pytest tests/ --cov=mtbl_valuations --cov-report=xml
```

#### Performance Testing
```bash
# Clear cache and measure fresh performance
rm -rf tests/.cache/phases/*.pkl
python -m pytest tests/engine/test_iteration.py --durations=10

# Run again to measure cached performance
python -m pytest tests/engine/test_iteration.py --durations=10
```

### Cached Fixtures

The following fixtures are cached at session scope:

- `converged_hitter_pools` - Caches Phase 3b: `iterate_to_convergence()` results (pre-dedupe)
  - Input: Position pools built from regular hitters
  - Expensive operation: Up to 10 convergence iterations with per-pool Z tracking
  - Output: Converged pools with valuations and replacement tier data

- `converged_hitter_pools_deduped` - Caches Phase 3c: Post-dedupe convergence results
  - Input: Converged pools from Phase 3b
  - Expensive operations: `dedupe_multi_position_players()` + re-convergence
  - Output: Final single-position hitter pools

Note: Phases 1-3a (load data, split by role, build pools) run via session-scoped fixtures but are not disk-cached since they're relatively fast operations.

### Troubleshooting

**Tests are slower than expected:**
- Check if cache exists: `ls -lh tests/.cache/phases/`
- Verify caching is enabled (no `--no-cache` flag)
- Clear and regenerate cache if corrupted

**Cache not invalidating:**
- Verify input files have actually changed
- Manually clear cache: `rm -rf tests/.cache/phases/*.pkl`

**Scope errors:**
- All fixtures used by cached fixtures must be session-scoped
- Check `tests/conftest.py` and `tests/engine/conftest.py` for proper scoping
