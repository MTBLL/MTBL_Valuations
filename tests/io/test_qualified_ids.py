"""Tests for ``io.qualified.qualified_ids`` — the set-of-IDs helper that
mirrors the loading-time qualified-PA gate.

Covers the field-agnostic ``pa_field`` switch (PA for batters / TBF for
pitchers), the threshold semantics (``>=``, not ``>``), and the defensive
branches the loader-side gate already handles in ``io/current.py``.
"""

from __future__ import annotations

from typing import Any

from mtbl_valuations.io.qualified import qualified_ids


def _record(
    id_espn: str, pa_field: str = "PA", pa: float | None = 100.0
) -> dict[str, Any]:
    cs: dict[str, Any] = {}
    if pa is not None:
        cs[pa_field] = pa
    return {
        "id_espn": id_espn,
        "stats": {"espn": {"current_season": cs}},
    }


def test_qualified_ids_includes_at_and_above_threshold() -> None:
    """Players at or above ``qualified_pa`` are included; just-below is not.

    Threshold is inclusive on the lower bound (``>=``) — matches the loader
    semantics in ``current.py:62``.
    """
    records = [
        _record("above", pa=200),
        _record("at", pa=150),
        _record("below", pa=149.99),
    ]
    out = qualified_ids(records, qualified_pa=150.0, pa_field="PA")
    assert out == {"above", "at"}


def test_qualified_ids_pitcher_uses_tbf_field() -> None:
    """Pitchers gate on TBF, not PA — the helper is field-agnostic."""
    records = [
        _record("p1", pa_field="TBF", pa=75),
        _record("p2", pa_field="TBF", pa=40),
    ]
    out = qualified_ids(records, qualified_pa=50.0, pa_field="TBF")
    assert out == {"p1"}


def test_qualified_ids_missing_current_season_excluded() -> None:
    """A record without ``current_season`` can't qualify."""
    records: list[dict[str, Any]] = [
        {"id_espn": "missing_cs", "stats": {"espn": {}}},
        {"id_espn": "missing_espn", "stats": {}},
        {"id_espn": "none_cs", "stats": {"espn": {"current_season": None}}},
    ]
    out = qualified_ids(records, qualified_pa=1.0, pa_field="PA")
    assert out == set()


def test_qualified_ids_non_numeric_pa_treated_as_below_threshold() -> None:
    """String/None playing-time values shouldn't raise — coerce to 0 so they
    fall below any positive threshold without polluting the cohort."""
    records: list[dict[str, Any]] = [
        {
            "id_espn": "bad_str",
            "stats": {"espn": {"current_season": {"PA": "not a number"}}},
        },
        {
            "id_espn": "none_pa",
            "stats": {"espn": {"current_season": {"PA": None}}},
        },
        _record("good", pa=200),
    ]
    out = qualified_ids(records, qualified_pa=50.0, pa_field="PA")
    assert out == {"good"}


def test_qualified_ids_missing_id_espn_skipped() -> None:
    """A record without ``id_espn`` is silently dropped — there's no key to
    return for it."""
    records: list[dict[str, Any]] = [
        {"stats": {"espn": {"current_season": {"PA": 200}}}},  # no id_espn
        _record("has_id", pa=200),
    ]
    out = qualified_ids(records, qualified_pa=50.0, pa_field="PA")
    assert out == {"has_id"}


def test_qualified_ids_int_id_espn_coerced_to_str() -> None:
    """The cohort is stored as ``set[str]``; integer IDs get stringified so
    the downstream consumer (``_enrich_records``) can compare against
    ``str(record["id_espn"])`` consistently."""
    records: list[dict[str, Any]] = [
        {
            "id_espn": 42,
            "stats": {"espn": {"current_season": {"PA": 200}}},
        },
    ]
    out = qualified_ids(records, qualified_pa=50.0, pa_field="PA")
    assert out == {"42"}
