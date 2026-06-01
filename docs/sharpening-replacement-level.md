# Sharpening the Curve: Three Levers on Replacement Level

*A follow-up to "Chasing a Ghost." Once replacement level reads ~$0, the
next question is the shape of everything above and below it — and which
knobs actually move it the right way. One of the three we tried turned out
to be a trap, and this is the record of why, so nobody re-discovers it the
hard way.*

---

## Where we started

The earlier rework got replacement level honest: the replacement archetype
became a trimmed mean of the RLP tier instead of a per-category phantom, and
replacement-level players started pricing near $0 the way they should.

But "near $0" left three loose ends:

1. **The replacement band was still too rich.** Plenty of clearly
   replaceable shortstops — your Trea Turners and Xander Bogaertses on a
   down year — were pricing at $5–8. Available, but not free.
2. **A few rostered players priced *negative***, and a few replacement
   players out-priced rostered ones. Tiers were assigned by `total_z` rank,
   but dollars came from `signed_z × $/Z`, and the two orderings can
   disagree at the margin.
3. **The studs-vs-scrubs separation was soft.** The whole point of an
   auction is that the elite cost real money and the fringe costs nothing;
   a flat-ish curve hides that.

We tried three levers, each behind a config flag so we could A/B against
live data rather than argue about it.

---

## Lever 1 — raise the baseline with the marginal starters (kept)

`replacement_model.mu_fringe_per_slot`

The replacement archetype was the average of the *bench* (the RLP tier).
But the honest replacement line for a position isn't the average bench guy
— it's closer to the **worst player you'd actually start**. So we fold the
weakest few *rostered* players into the baseline mean alongside the RLP
tier.

How many? Scaled by the position's **dedicated roster slots**, not pool
size. The intuition: a single-slot pool (SS, 11 rostered in an 11-team
league) has about **2** fringe starters worth blending. A 3-slot pool (OF)
has 3× the jobs, so **6**. A 2-slot pool (RP) gets **4**.

> **Why slots, not a flat percentage.** Our first cut used 25% of the
> rostered tier. That's 2 for shortstop but **11** for the 44-deep SP pool
> — and folding 11 starters spanning a wide quality band into the baseline
> made the SP curve *flatten in one projection source and sharpen in
> another*. Same config, opposite shape. Scaling by dedicated slots (SP =
> 3 real starting jobs, not the P-inflated 4) blends 6 instead of 11 and
> the SP curve became stable and consistent across sources. The lesson:
> the fringe is a property of how many starting **jobs** a position has,
> not how deep its waiver pile runs.

Effect, measured on the SS pool: replacement-band shortstops dropped from a
~$4 median to ~$0, the elite stretched up (Witt $45 → $53), and the deep
scrubs went more negative. Exactly the studs-and-scrubs sharpening we
wanted — and it reads *real positional scarcity*: shallow positions (SS,
which falls off a cliff after 11) compress hard; deep ones (OF, a smooth
33-player gradient) compress gently.

Picked by lowest `get_composite_metric` (wRC+ / −FIP) — a stable raw rank
with no chicken-and-egg against the z-scores being computed.

---

## Lever 2 — a final hard $-sort (kept)

`replacement_model.final_dollar_sort`

After everything settles, re-cut each pool strictly by per-pool dollars:
top `roster_slots` → ROSTERED, next band → REPLACEMENT, rest → BELOW. This
**guarantees** no replacement player out-values a rostered one — by
construction, not by hoping the swap-pass converged.

It runs *after* budget validation, and it does **not** re-allocate. Budgets
are cut on the z-settled tiers; this final ordering is intentionally
asympathetic to the budget, so rostered dollars no longer sum *exactly* to
the pool budget (~$8–13 of drift league-wide). That skew is accepted: the
invariant "rostered ≥ replacement, always" wins over perfect budget
conservation at the last step.

Effect: every pool's baseline had ROSTERED and REPLACEMENT **interleaved**
at the dollar boundary (an OF rostered at $6.26 sitting below a replacement
OF at $6.23). The $-sort fixes all of them uniformly — it's the one lever
that ports cleanly to every pool regardless of size. League-wide invariant
violations went from ~6 per source to **0**, and the handful of
*negative-dollar rostered* players disappeared.

---

## Lever 3 — widen the scale with the RLP tier (REJECTED)

`replacement_model.stdev_include_rlp` *(removed)*

The idea sounded reasonable: the per-category standard deviation is the z
*scale*, and maybe the "true noise" of the playable population includes the
replacement-level guys, not just the starters. So compute stdev over
rostered **+ RLP** instead of rostered alone.

It was a trap. We ran it on the SS pool with the other two levers on:

- **On a clean pool (updated-projection SS): it did nothing.** Every player
  moved by less than a dime.
- **On a noisy pool (current-season SS, full of injured stars dumped to the
  replacement tier): it did real damage.** The replacement-band median
  *inflated* from $2.6 to **$6.4**, the entire below-replacement tail lifted
  $8–19, and the studs *lost* $5–7. It flattened the curve — the exact
  opposite of the goal — and only on the pools where the RLP tier was
  dirtiest.

The mechanism: folding a noisy RLP tier into the per-category stdev widens
that stdev, which shrinks every z, which the `$/Z = budget / Σz`
renormalization then spreads back out as a *flatter* curve. The dirtier the
replacement tier, the worse the distortion — so the effect is
unpredictable from source to source. And because a uniform scale change
washes out in the budget normalization anyway, what *doesn't* wash out is a
per-category re-weighting nobody asked for.

There is no pool on which `stdev_include_rlp` helped. It was either a no-op
or actively harmful. **The scale belongs to the starters; the baseline is
where replacement-level information goes** (via Lever 1, robustly, with
trimming). Mixing replacement noise into the scale just reintroduces it
through the back door.

So we removed it — config key, code path, and all. This section is the
gravestone, so the idea doesn't get exhumed.

---

## Where it landed

```json
"replacement_model": {
  "mu_fringe_per_slot": 2,
  "final_dollar_sort": true
}
```

- Replacement-level players price near $0 (Lever 1), with compression that
  tracks real positional scarcity.
- No replacement player ever out-values a rostered one; no rostered player
  prices negative (Lever 2).
- The scale stays on the rostered tier, where it's stable (Lever 3, by its
  absence).

Validated across SS (1-slot), OF (3-slot), and SP (3-slot dedicated): the
same studs-up / replacement-toward-$0 / clean-boundary shape every time, in
both projection and current-season sources.
