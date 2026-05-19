# MTBL Valuations
MTBL Valuations after transform pipeline.  This script adds the valuations to the MTBL data.

## Usage

```bash
mtbl-valuations hydrate [OPTIONS]
```

By default the engine reads `batters_matched.json`, `pitchers_matched.json`, and
the league summary from `/Users/Shared/BaseballHQ/resources/transform/` (the
*published* transform output — note this is distinct from the upstream
`Player_Universe_Trx/.temp/` scratch directory). Override any input with
`--batters-file`, `--pitchers-file`, `--league-file`, or `--budget-config`.

### Logging & verbosity

The engine logs under the `mtbl_valuations` logger namespace. Verbosity is off by
default (warnings only) and is raised with repeatable `-v` flags or set
explicitly with `--log-level`:

| Flag            | Level   | What you see                                                             |
| --------------- | ------- | ------------------------------------------------------------------------ |
| *(none)*        | WARNING | Quiet — only problems. Pipeline phase progress still prints to stdout.    |
| `-v`            | INFO    | High-level notes, e.g. `Skipped 44 batters with no Fangraphs projections`. |
| `-vv`           | DEBUG   | Per-record detail — every player skipped for missing projections, with `id_espn`. |
| `--log-level X` | X       | Set `WARNING`/`INFO`/`DEBUG` explicitly. Overrides `-v` when both given.  |

```bash
mtbl-valuations hydrate -v        # INFO: why is the player count different from last night?
mtbl-valuations hydrate -vv       # DEBUG: exactly which players were dropped
mtbl-valuations hydrate --log-level DEBUG
```

Use `-vv` when the loaded player count looks off: upstream emits prospects and
inactive-roster players with `projections: null`, and the loader skips them —
DEBUG names each one.

## Outputs

`mtbl-valuations hydrate` runs the valuation engine once per **valuation
source** (`preseason`, `updated`, `ros`, `synthetic`, `current` — five total)
and writes two kinds of artifacts under `--output-dir`:

```
<output-dir>/
├── hitters.json                 ← merged across all 5 sources
├── pitchers.json                ← merged across all 5 sources
├── preseason/
│   └── position_summary.csv     ← pool-level aggregates for this source
├── updated/
│   └── position_summary.csv
├── ros/
│   └── position_summary.csv
├── synthetic/
│   └── position_summary.csv
└── current/
    └── position_summary.csv
```

### `hitters.json` / `pitchers.json` (top-level, merged)

The canonical per-player output. Each record matches the upstream
`batters_matched.json` / `pitchers_matched.json` schema with two enrichments:

1. **`stats.savant.*` is enriched with `<field>_pct_rnk` columns** — percentile
   ranks for every numeric Savant field, computed against the **current**
   source's settled rostered + replacement-level player universe. Direction-aware:
   `K_pct`, `swing_miss_pct`, ERA, WHIP, xwOBA-against, etc. are inverted so
   `pct_rnk` always means *good performance for that role* regardless of stat
   polarity.
2. **`valuations` is a dict keyed by source label** — `{preseason, updated,
   ros, synthetic, current}`. Each source-block carries `primary_position`,
   `tier`, `total_z`, `total_dollars`, `z_scores` (per category), and
   `dollar_values` (per category).

This is the single artifact downstream consumers should read. Per-source CSVs
and per-source JSONs are intentionally **not** written because every per-player
field they used to expose is already in this merged JSON — see the rationale
below.

### `<source>/position_summary.csv` (per-source, pool-level)

The only artifact written *per-source*. Pool-level aggregates that aren't
carried in the per-player JSON. One row per (position, role) pool, with
columns:

- `rostered_count`, `replacement_tier_count`
- `total_budget`, `budget_<cat>` (per-category dollar pots)
- `pool_total_z_<cat>` (sum of settled z's in the rostered tier)
- `dollars_per_z_<cat>` (the $/z conversion rate the engine settled on)
- `replacement_baseline_<cat>` (the RLP archetype's raw values)

Use this to inspect *how* the engine priced a pool — e.g. "why did SS get 12%
of the hitter budget?" or "what's the $/z for OBP after the swap-pass?".

### Why this layout

Earlier versions wrote a per-source `valuations.csv`, a per-source
`hitters.json` / `pitchers.json`, and per-position `<pos>_detailed.csv` files
in every source subdir — five copies of the same data, none of them carrying
the Savant pct_rnk enrichments (those only land in the merged JSON because the
ranking population is the canonical current-source rostered + RLP set). The
per-source CSVs were bit-equal slices of `valuations[source]`; the
per-position detailed CSVs duplicated `stats.fangraphs.*` raw stats +
`valuations[source].z_scores` already in the merged JSON. Net effect of
consolidation: ~175 MB of per-run output → ~42 MB, with no information loss.

## Ideation

let's ideate a bit on this. the end goal is to produce a multi part blog post for this framework. while this draft is succinct enough for a code repo and helps build the intuition needed to put the proper programmatic structures in place, it doesn't explain the whys sufficiently. For example, why, really, is it that catcher who hits 20 HRs not the same as the first basement who hits 20 HR? What mathematical or philosophical (logical) principle allows us to defy a universal truth that 20 == 20. 

Additionally, for the blog post it will be essential to share my inspiration, Keith Woolner's work on Value Over Replacement Player https://web.archive.org/web/20071013172219/http://stathead.com/bbeng/woolner/vorpdescnew.htm, and Zach Sanders's work on Fantasy Value Above Replacement https://fantasy.fangraphs.com/value-above-replacement-part-one/.  My ideas contribute to the conversation because Zach's work lacks logic for replacement level player generation, or how each stat group can be valued differently. -- these things would certain be captured in a longer format blog post.

Addressing your comments: i love your idea of 'building intuition' that's really what this is all about. So of the claims need elaboration, e.g. "you can't add HR to SB to OBP" -- well obviously...but the so what isn't evident always; it just kinda sounds like a fancy fact, but i'm just left saying 'ook...' after reading it. The notes on current swift behavior doesn't add to the conversation and is more of a note on my current incompleteness in my swift project in an earlier state when my ETL pipeline was less robust. this comment 'Projection Z-scores use projection numerators but may use non-projection standard deviations—this is a known inconsistency to address' is easily fixable as intuition would state that projection zscores should use projection SDs.

A note on the two tracks of valuations. Let me clarify: in the preseason, season projections are the only metrics driving draft valuations. In season, we can capture current season stats as well as Rest of Season (RoS) projections. Valuations should be derived for both data sets; this allows us to find over/under-performers and waiver/trade candidates extremely efficiently.

the last thing i was ruminating on last night was the RLP window bounds; the windowSize aspect is currently arbitrary and for simplicity may not cause the world to burn. but I was thinking of a more programmatic/dynamic way to generate the window size. when we provide an initial sort on a batter position group, we typically us wRC+ (sort desc.).  Well instead of using an arbitrary next 4 players, perhaps the window should be required to be within a certain SD (0.5, 1.0, etc) of the last valid (draft-able) wRC+. Let's say 12 teams, the 12th catcher has a wRC+ of 100, the 13th catcher has wRC+ of 95, the 14th wRC+ of 90, and the 15th a wRC+ of 75. In this scenario (we are assuming the wRC+ of 90 is within a statistically significant range of the worst draft-able catcher), the 15th best catcher is completely unusable even for the sake of using his stats to generate a RLP archetype so our window should dynamically adjust accordingly.  

p.s.s. when we generate values, we are not limited to the pure Hitter Categories: R, HR, RBI, SBN, OBP, SLG, but can also generate values using projected sabremetrics. This can help quiet the variability in projecting something like RBIs (which are to a large part out of the control of the batter -- he can't force the guys ahead of him to be in scoring position).  But generating values on stats like xwOBA which is partly derived from observable metrics (exit velo, launch angle, etc) helps quite down the error prone nature of projecting RBIs.  And for this very reason, each stat category does not need to shared an equal distribution of the draft budget allocation. If there are 5 categories, not all categories need receive 20% of the budget. highly speculative categories like RBIs or ERA should receive a smaller portion of the budget.
