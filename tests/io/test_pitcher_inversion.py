"""Regression guards for the pitcher inversion set in ``io/savant_ranks``.

These tests pin the membership and orientation of pitcher fields that were
displaying the wrong cell-fill direction prior to this fix. The bug pattern
is uniform: a field whose *raw* high value means "worse pitcher performance"
(contact-quality allowed, batter-side run-expectancy, or actual-minus-
expected diff) was not in ``_PITCHER_LOWER_BETTER``, so inversion did not
fire and high raw values displayed as red (interpreted by readers as "good"
when in fact they're "bad").

Spot-checked against Zac Gallen's 2026-05-22 record (cohort = qualified;
346 pitchers).
"""

from __future__ import annotations

from typing import Any

import pytest

from mtbl_valuations.io.savant_ranks import (
    _PITCHER_LOWER_BETTER,
    inject_savant_pct_rnks,
)


# Fields whose raw-high value = worse pitcher performance, grouped by source.
# Membership in _PITCHER_LOWER_BETTER is what makes the cell-fill gradient
# point the right direction in the UI tooltip / heatmap.
_NEW_INVERTED_PITCHER_FIELDS = [
    # actual - expected diff family (positive = batter outperformed expected
    # contact = bad for pitcher)
    "wOBAdiff", "xAVGdiff", "xOBPdiff", "xSLGdiff", "xERAdiff", "xHRdiff",
    # Expected counts + naming aliases
    "xHR", "barrels",
    # Batter-side run expectancy against the pitcher
    "run_exp",
    # HR quality allowed (more "no-doubt" HRs = worse contact suppression)
    "no_doubter_pct", "no_doubters",
    # Statcast sub-block contact quality (batters hitting harder/farther)
    "avg_ev", "max_ev", "ev50", "ev95_pct", "ev95_plus",
    "fbld_ev", "gb_ev",
    "max_distance", "sweetspot_pct",
]


@pytest.mark.parametrize("field", _NEW_INVERTED_PITCHER_FIELDS)
def test_pitcher_lower_better_includes_field(field: str) -> None:
    """Each listed field must be in ``_PITCHER_LOWER_BETTER`` so the inversion
    step fires and the cell-fill gradient shows the correct color direction.

    Membership guard — catches the case where someone removes an entry while
    refactoring the frozenset.
    """
    assert field in _PITCHER_LOWER_BETTER, (
        f"{field!r} dropped from _PITCHER_LOWER_BETTER — UI cell-fill will "
        f"point the wrong direction for this field."
    )


def test_pitcher_diff_field_inversion_orientation() -> None:
    """End-to-end orientation check for a representative *diff field.

    A pitcher with positive ``wOBAdiff`` (batter outperformed expected
    contact — bad for pitcher) should end up with a LOW post-inversion
    ``wOBAdiff_pct_rnk``. A pitcher with negative ``wOBAdiff`` (overperforming
    underlying contact — good for pitcher) should end up with a HIGH
    ``wOBAdiff_pct_rnk``.

    Guards against the inversion *mechanism* breaking even if the membership
    test above still passes.
    """
    pitchers: list[dict[str, Any]] = [
        {
            "id_espn": "high",  # worst (highest diff = batter outperformed most)
            "stats": {"savant": {"all": {"wOBAdiff": 0.050}}},
        },
        {
            "id_espn": "mid",
            "stats": {"savant": {"all": {"wOBAdiff": 0.000}}},
        },
        {
            "id_espn": "low",  # best (most negative diff = pitcher outperformed)
            "stats": {"savant": {"all": {"wOBAdiff": -0.050}}},
        },
    ]
    inject_savant_pct_rnks([], pitchers, set(), {"high", "mid", "low"})

    high_block = pitchers[0]["stats"]["savant"]["all"]
    low_block = pitchers[2]["stats"]["savant"]["all"]
    assert high_block["wOBAdiff_pct_rnk"] == 0.0
    assert low_block["wOBAdiff_pct_rnk"] == 1.0


def test_pitcher_contact_quality_inversion_orientation() -> None:
    """End-to-end orientation check for a statcast-block contact-quality
    field. A pitcher allowing the highest ``max_ev`` (hardest hit against
    him) should end up with a LOW ``max_ev_pct_rnk`` after inversion.

    The ``statcast`` sub-block historically used keys (``barrels``, ``avg_ev``,
    ``max_ev``, etc.) that didn't appear in the inversion set even though the
    ``all`` sub-block's analog keys (``barrels_total``, ``exit_velo``) did —
    this fixture pins the sub-block-aware naming.
    """
    pitchers: list[dict[str, Any]] = [
        {
            "id_espn": "soft",  # best (lowest max EV allowed)
            "stats": {"savant": {"statcast": {"max_ev": 100.0}}},
        },
        {
            "id_espn": "mid",
            "stats": {"savant": {"statcast": {"max_ev": 108.0}}},
        },
        {
            "id_espn": "crushed",  # worst (hardest hit against him)
            "stats": {"savant": {"statcast": {"max_ev": 115.0}}},
        },
    ]
    inject_savant_pct_rnks([], pitchers, set(), {"soft", "mid", "crushed"})

    assert pitchers[0]["stats"]["savant"]["statcast"]["max_ev_pct_rnk"] == 1.0
    assert pitchers[2]["stats"]["savant"]["statcast"]["max_ev_pct_rnk"] == 0.0
