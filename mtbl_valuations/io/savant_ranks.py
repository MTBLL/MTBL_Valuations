"""Inject percentile-rank fields into the raw Savant nested objects.

Upstream publishes a handful of ``_pct_rnk`` fields against the full
Statcast population, which is skewed (skews toward MLB regulars but
includes a lot of fringe / partial-season players). The valuations
pipeline knows the *settled* rostered + replacement-level population —
the universe of fantasy-relevant players — so it's positioned to publish
per-field ``_pct_rnk`` values against a more useful baseline.

The pct_rnks are computed once per run using the **current** source's
rostered + RLP tiers as the ranking distribution (savant data itself is
observed, source-independent). Every player with savant data gets their
pct_rnks injected — players outside the population still land in the
population's distribution, just at low percentiles.

Output goes directly into the raw record's ``stats.savant.<sub>`` blocks
so it travels through ``write_merged_player_json`` unchanged. Existing
upstream ``_pct_rnk`` fields are overwritten with our values.
"""

from __future__ import annotations

import bisect
from typing import Any

# Non-stat fields that look numeric but aren't rankable.
_META_SKIP = frozenset(
    {
        "year",
        "team_id",
        "age",
        "position",
        "hr_type",
        "pitch_type",
        "pitch_name",
    }
)


def _is_rankable(key: str, value: Any) -> bool:
    """Decide whether a (key, value) pair in a savant sub-block should be
    ranked. Skips meta fields, existing pct_rnk fields, non-numeric
    values, and bools."""
    if key in _META_SKIP:
        return False
    if key.endswith("_pct_rnk"):
        return False
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return True


def _percentile_rank(sorted_values: list[float], value: float) -> float:
    """Fractional rank in ``[0.0, 1.0]`` of ``value`` within an ascending
    sorted population. Ties land on the lower-bound side (bisect_left).
    Population <= 1 collapses to 0.5 (no meaningful rank)."""
    n = len(sorted_values)
    if n <= 1:
        return 0.5
    idx = bisect.bisect_left(sorted_values, value)
    return idx / (n - 1)


def _enrich_records(
    records: list[dict[str, Any]],
    population_ids: set[str],
) -> int:
    """For every numeric field in each player's ``stats.savant.<sub>``
    blocks, compute a pct_rnk against the population (subset of records
    with id in ``population_ids``) and inject ``{field}_pct_rnk`` back
    into the same block.

    Returns the count of distinct (sub_block, field) pairs that were
    ranked — primarily for logging.
    """
    # 1) Collect per-field population values.
    field_values: dict[tuple[str, str], list[float]] = {}
    for record in records:
        rid = str(record.get("id_espn"))
        if rid not in population_ids:
            continue
        savant = record.get("stats", {}).get("savant") or {}
        if not isinstance(savant, dict):
            continue
        for sub_name, sub_block in savant.items():
            if not isinstance(sub_block, dict):
                continue  # skips arrays like pitch_arsenal
            for field, value in sub_block.items():
                if not _is_rankable(field, value):
                    continue
                field_values.setdefault((sub_name, field), []).append(float(value))

    for key in field_values:
        field_values[key].sort()

    # 2) Inject pct_rnks for every record that has savant data, regardless
    #    of population membership — non-population players still get a
    #    meaningful "where do I land vs the settled universe" number.
    for record in records:
        savant = record.get("stats", {}).get("savant") or {}
        if not isinstance(savant, dict):
            continue
        for sub_name, sub_block in savant.items():
            if not isinstance(sub_block, dict):
                continue
            ranks_to_add: dict[str, float] = {}
            for field, value in list(sub_block.items()):
                if not _is_rankable(field, value):
                    continue
                vals = field_values.get((sub_name, field))
                if not vals:
                    continue
                ranks_to_add[f"{field}_pct_rnk"] = round(
                    _percentile_rank(vals, float(value)), 3
                )
            if ranks_to_add:
                sub_block.update(ranks_to_add)

    return len(field_values)


def inject_savant_pct_rnks(
    batters_data: list[dict[str, Any]],
    pitchers_data: list[dict[str, Any]],
    hitter_population_ids: set[str],
    pitcher_population_ids: set[str],
) -> tuple[int, int]:
    """Top-level entry: enrich both batters and pitchers raw records.

    Args:
        batters_data / pitchers_data: Raw record lists as loaded from
            ``batters_matched.json`` / ``pitchers_matched.json``. Mutated
            in place.
        hitter_population_ids: ``id_espn`` strings for hitters whose
            settled rostered + RLP tier feeds the ranking population.
        pitcher_population_ids: Same for pitchers.

    Returns ``(hitter_fields_ranked, pitcher_fields_ranked)`` — the
    distinct field counts that ended up with pct_rnks. Useful for a
    one-line summary log.
    """
    h = _enrich_records(batters_data, hitter_population_ids)
    p = _enrich_records(pitchers_data, pitcher_population_ids)
    return h, p
