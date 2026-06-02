"""Specs for per-pitch percentile ranks in ``stats.savant.pitch_arsenal``.

``pitch_arsenal`` is a *list* block (one dict per pitch type), unlike the
scalar savant sub-blocks. Each field is ranked WITHIN its pitch type, and
orientation is perspective-relative: in the pitcher file the fields are
contact allowed (lower wOBA = better), while in the batter file the same
fields are the hitter's own results (higher wOBA = better).
"""

from __future__ import annotations

from typing import Any

from mtbl_valuations.io.savant_ranks import (
    _enrich_pitch_arsenal,
    inject_savant_pct_rnks,
)


def _arsenal_record(pid: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id_espn": pid, "stats": {"savant": {"pitch_arsenal": entries}}}


def test_arsenal_ranks_grouped_within_pitch_type() -> None:
    """A field is ranked only against the SAME pitch type — a slider's wOBA
    never lands in the fastball distribution."""
    pitchers = [
        _arsenal_record(
            "a",
            [
                {"pitch_type": "FF", "wOBA": 0.200},  # best FF
                {"pitch_type": "SL", "wOBA": 0.400},  # worst SL
            ],
        ),
        _arsenal_record(
            "b",
            [
                {"pitch_type": "FF", "wOBA": 0.400},  # worst FF
                {"pitch_type": "SL", "wOBA": 0.200},  # best SL
            ],
        ),
    ]
    inject_savant_pct_rnks([], pitchers, set(), {"a", "b"})

    a_ff, a_sl = pitchers[0]["stats"]["savant"]["pitch_arsenal"]
    b_ff, b_sl = pitchers[1]["stats"]["savant"]["pitch_arsenal"]
    # Pitcher wOBA is lower-better → the stingiest pitch grades highest.
    assert a_ff["wOBA_pct_rnk"] == 1.0  # best FF
    assert b_ff["wOBA_pct_rnk"] == 0.0  # worst FF
    assert a_sl["wOBA_pct_rnk"] == 0.0  # worst SL
    assert b_sl["wOBA_pct_rnk"] == 1.0  # best SL


def test_arsenal_pitcher_run_value_not_inverted() -> None:
    """``run_value`` is good-when-high from the pitcher's own perspective, so
    it is NOT in the lower-better set — high raw value grades high."""
    pitchers = [
        _arsenal_record("hi", [{"pitch_type": "FF", "run_value": 8}]),
        _arsenal_record("lo", [{"pitch_type": "FF", "run_value": -3}]),
    ]
    inject_savant_pct_rnks([], pitchers, set(), {"hi", "lo"})
    hi = pitchers[0]["stats"]["savant"]["pitch_arsenal"][0]
    lo = pitchers[1]["stats"]["savant"]["pitch_arsenal"][0]
    assert hi["run_value_pct_rnk"] == 1.0
    assert lo["run_value_pct_rnk"] == 0.0


def test_arsenal_batter_orientation_flips() -> None:
    """In the batter file the same fields invert: the hitter's own ``wOBA``
    is higher-better, while ``whiff_pct`` / ``put_away_pct`` are lower-better.
    """
    batters = [
        _arsenal_record(
            "masher",
            [{"pitch_type": "FF", "wOBA": 0.500, "whiff_pct": 10.0,
              "put_away_pct": 5.0}],
        ),
        _arsenal_record(
            "weak",
            [{"pitch_type": "FF", "wOBA": 0.200, "whiff_pct": 40.0,
              "put_away_pct": 30.0}],
        ),
    ]
    inject_savant_pct_rnks(batters, [], {"masher", "weak"}, set())
    masher = batters[0]["stats"]["savant"]["pitch_arsenal"][0]
    weak = batters[1]["stats"]["savant"]["pitch_arsenal"][0]
    # Higher wOBA = better hitter → top grade.
    assert masher["wOBA_pct_rnk"] == 1.0
    # More whiffs / put-aways = worse hitter → bottom grade (inverted).
    assert masher["whiff_pct_pct_rnk"] == 1.0
    assert masher["put_away_pct_pct_rnk"] == 1.0
    assert weak["whiff_pct_pct_rnk"] == 0.0
    assert weak["put_away_pct_pct_rnk"] == 0.0


def test_arsenal_meta_fields_not_ranked() -> None:
    """``pitch_type`` / ``pitch_name`` are meta and never get a pct_rnk."""
    pitchers = [_arsenal_record("a", [
        {"pitch_type": "FF", "pitch_name": "4-Seam", "wOBA": 0.3}])]
    inject_savant_pct_rnks([], pitchers, set(), {"a"})
    e = pitchers[0]["stats"]["savant"]["pitch_arsenal"][0]
    assert "pitch_type_pct_rnk" not in e
    assert "pitch_name_pct_rnk" not in e
    assert "wOBA_pct_rnk" in e


def test_arsenal_non_list_block_skipped() -> None:
    """A dict-shaped (or absent) pitch_arsenal must not raise — only list
    blocks are ranked."""
    pitchers = [
        {"id_espn": "a", "stats": {"savant": {"pitch_arsenal": {"oops": 1}}}},
        {"id_espn": "b", "stats": {"savant": {}}},
    ]
    # Must not raise; nothing to assert beyond clean execution.
    inject_savant_pct_rnks([], pitchers, set(), {"a", "b"})


def test_arsenal_non_dict_entry_and_missing_pitch_type_skipped() -> None:
    """List entries that aren't dicts, or dicts with no ``pitch_type``, are
    skipped on both the collection and injection passes."""
    pitchers = [
        _arsenal_record("a", [
            "not-a-dict",
            {"wOBA": 0.3},  # no pitch_type
            {"pitch_type": "FF", "wOBA": 0.3},
        ]),
    ]
    inject_savant_pct_rnks([], pitchers, set(), {"a"})
    entries = pitchers[0]["stats"]["savant"]["pitch_arsenal"]
    assert entries[0] == "not-a-dict"
    assert "wOBA_pct_rnk" not in entries[1]
    # The single well-formed entry has only itself in its FF distribution
    # (n=1 → 0.5 fallback), but it IS ranked.
    assert entries[2]["wOBA_pct_rnk"] == 0.5


def test_arsenal_field_with_no_population_skipped() -> None:
    """A field present only on an out-of-population pitch entry has no
    in-population values to rank against → no pct_rnk injected."""
    pitchers = [
        _arsenal_record("in", [{"pitch_type": "FF", "wOBA": 0.3}]),
        _arsenal_record("out", [{"pitch_type": "FF", "unique": 9.9}]),
    ]
    inject_savant_pct_rnks([], pitchers, {}, {"in"})
    out_entry = pitchers[1]["stats"]["savant"]["pitch_arsenal"][0]
    assert "unique_pct_rnk" not in out_entry


def test_arsenal_entry_with_no_rankable_fields_untouched() -> None:
    """An entry whose only keys are meta gets no pct_rnk dict merged in."""
    pitchers = [_arsenal_record("a", [{"pitch_type": "FF"}])]
    _enrich_pitch_arsenal(pitchers, {"a"}, frozenset())
    e = pitchers[0]["stats"]["savant"]["pitch_arsenal"][0]
    assert e == {"pitch_type": "FF"}
