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

### The double-rostering it opened — and how we closed it

Sorting each pool *independently* by dollars created one subtle hole. A
hitter eligible at both a base position and UTIL (every hitter is
UTIL-eligible) could now be promoted to ROSTERED **in both pools at once**
— a 1B who's also UTIL-rostered, picked up by the 1B sort on dollars while
still sitting in UTIL. Suddenly the same player occupies two roster slots.
The engine's existing duplicate cleanup runs earlier, inside the
convergence pass, so it never sees a duplicate the *final* sort creates.

The fix encodes a real auction rule: **a player has one fielding home.**
Keep the base rostering (that's his identity — a first baseman, not a
generic DH), vacate his UTIL slot, and promote UTIL's best available
bench bat — *next man up*. The detail that matters: we **remove** him from
UTIL rather than demote him to UTIL's replacement tier. He was UTIL's
highest-paid rostered player, so demoting him would leave him out-earning
the starter who took his place — re-breaking the very invariant the sort
just established. Pulling him out cleanly and promoting the top bench bat
keeps "rostered ≥ replacement" intact for free. The pass repeats until no
player is double-rostered anywhere.

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

## Why this is the most accurate version to date

Three threads of work converge here, and it's worth saying plainly why the
engine is now closer to *true* value than any prior version:

1. **Replacement level is honest *and* sharp.** "Chasing a Ghost" got it
   honest — replacement players stopped pricing at a phantom $12. This
   rework got it sharp — they price near $0, and the curve above them
   reflects real scarcity (a shortstop falls off a cliff after eleven; an
   outfielder glides down a long bench). The fringe blended into the
   baseline scales with how many *starting jobs* a position has, not how
   deep its waiver pile runs, so a position's replacement line means the
   same thing whether it's one slot or four.

2. **The tiers cannot lie anymore.** For the first time, the exported
   tiers are a strict dollar ordering: no replacement player out-earns a
   rostered one, no rostered player prices negative, and no player holds
   two roster slots. Those were all *possible* before — rare, but possible,
   and when they happened they quietly contradicted the dollar values
   sitting right next to them. The final $-sort plus the double-rostering
   reconcile close that gap by construction, not by luck.

3. **The roles and the data feeding it are right.** Pitchers are classified
   by what they actually do (starts vs. saves-and-holds), uniformly across
   every projection source; insufficient-sample arms are gated out instead
   of polluting a pool; and the per-position values export cleanly for both
   hitters and pitchers. Garbage in, garbage out — and a lot of upstream
   garbage got cleaned out along the way.

The one thing we *gave up* to get here is exact budget conservation: the
final sort drifts the rostered total by roughly $8–13 against a $2,805
league. That's a deliberate trade. Budgets are still cut on a conserved,
z-settled allocation; the final ordering then prioritizes a truth the
auction cares about more than the accounting — **you never pay a
replacement-level price for a rostered player, and you never roster the
same player twice.** For a tool whose entire job is to tell you what a
player is worth, getting the *ordering* unimpeachable was worth a few
dollars of slack in the sum.
