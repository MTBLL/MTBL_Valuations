# Chasing a Ghost: What Fantasy Auction Values Taught Us About Replacement Level

*How a $12 player who should have cost nothing exposed a flaw in the way we
price baseball production — and what fixing it revealed about scarcity,
markets, and the difference between "bad" and "replaceable."*

---

## The $12 Player Who Should Have Been Free

Every winter, fantasy baseball managers sit down to an auction. Each team
gets the same budget — call it $260 — and spends it building a roster.
Every dollar you spend on a shortstop is a dollar you can't spend on a
closer. The whole exercise is a tiny, self-contained economy, and like any
economy it runs on one quietly powerful idea: **replacement level**.

Replacement level is the talent you can get for free. It's the player
sitting on the waiver wire, the guy nobody drafted, the 30th-best second
baseman in a league that only needs 11 of them. He is, by definition,
*replaceable* — and so, in a well-built valuation system, he should be
worth right around **$0**. Not because he's worthless as a baseball
player, but because you never have to *pay* for him. He's already free.

So when our valuation engine printed a dollar value for Konnor Griffin — a
genuine prospect, but on this particular run a replacement-level
shortstop — and the number came back **$12**, something was wrong. Not
"slightly off." Wrong. A free player was being quoted at 5% of an entire
team's budget.

This is the story of why that happened, and the surprisingly deep rabbit
hole we fell down trying to fix it. It turns out the bug wasn't a typo. It
was a philosophical mistake baked into the math — the model was chasing a
player who does not exist.

---

## A Quick Primer: How a Fantasy Dollar Gets Made

Stick with us for ninety seconds of plumbing; it makes everything after it
click.

You can't price a baseball player directly. Nobody knows what "27 home
runs" is worth in dollars. So valuation systems do it in three moves:

**1. Convert raw stats into z-scores.** A z-score just measures how far
above the pack a player is, in standard "steps" (statisticians call the
step a *standard deviation*). If shortstops average 15 home runs and the
typical spread is 7, then a 29-homer shortstop is **+2** — two steps above
the crowd. The beauty of this is that it puts home runs, stolen bases, and
on-base percentage on one common scale, even though their raw numbers look
nothing alike.

**2. Decide where "zero" is.** Here's the subtle part. A z-score is always
measured *against* something. Above the league average? Above the worst
starter? Above replacement level? That reference point — the **baseline** —
is the single most important decision in the whole system, because it
decides who counts as "producing value" and who's just "showing up."

**3. Convert z-scores into dollars.** Each scoring category gets a slice of
the league's total budget. Divide that slice by the total z-scores in the
category, and you get an exchange rate — call it **$/Z**, dollars per
z-unit. Multiply each player's z-score by the rate, and you've got a price.

Hold onto that last one, because it has a sharp edge. The exchange rate is
`budget ÷ total z`. If the total z in a category is *small*, the rate gets
*huge* — and tiny differences between players explode into giant dollar
gaps. A category where the rostered players barely out-produce the baseline
is a category where the pricing math becomes a powder keg. We'll come back
to that.

---

## The Ghost in the Machine

So where was the baseline — the all-important "zero" — set?

The system measured each player against **the worst rostered player in
each category**. Sensible-sounding, right? The worst guy good enough to be
drafted is a fair definition of "the edge of replacement level."

Except it wasn't *one* player. It was the worst rostered player *in each
category, taken separately*.

Picture the eleven shortstops good enough to roster. One of them is a
plodding slugger who never steals — he's the worst in steals. A different
one is a speedy slap hitter with no pop — he's the worst in home runs. A
third can't take a walk — worst in on-base percentage. The system stitched
the worst steals number, the worst home-run number, and the worst on-base
number into a single baseline.

**That composite is not a real player.** No human being is simultaneously
the slowest, the weakest, and the most allergic to walks. We built a
Frankenstein — a phantom worse, in every individual category, than anyone
who actually exists.

And that's the bug, in one sentence: **a replacement-level player gets
compared to a ghost who is worse than he is at everything.** Konnor Griffin
isn't a star, but he beats the phantom in all six categories — because the
phantom is impossible. Every category he "wins" pours dollars onto his
price. Six small phantom victories, stacked up, became $12.

It gets worse. Because the phantom is so far below everyone, *every*
player's z-scores were inflated — rostered stars included. The system was,
in effect, measuring everyone's height from the bottom of a hole it had
dug itself. The budget still balanced (the exchange rate quietly shrank to
compensate), so nothing looked broken on the surface. But the whole price
ladder had a phantom-sized step welded onto its base.

---

## Fix #1: Stop Measuring Against a Crowd of Three

Before chasing the phantom itself, we found a smaller problem feeding it.

The "replacement tier" — the band of players just below the rostered cut,
the ones who define the edge — was only **three players deep**. Three. A
three-player average is statistical quicksand: one fluky speedster landing
in that tiny group can drag the whole steals baseline halfway across the
field.

We widened it. The replacement tier is now **half the size of the rostered
group** — six players for an eleven-man position, seventeen for a deep one
like the outfield. A baseline averaged over fifteen players barely twitches
when one player moves; a baseline averaged over three lurches.

This didn't fix the price of replacement players on its own — and that
surprised us at first. (Widening the sample actually nudged a few values
*up*, because it pulled the baseline slightly lower.) But it did something
necessary: it gave us a **stable foundation** to rebuild on. You can't do
careful surgery on a number that jumps every time you blink.

---

## Fix #2: An Archetype, Not a Phantom

Here's the heart of it.

The phantom's crime was being impossible — a stitched-together worst-case
that no real player matches. The fix is almost embarrassingly intuitive:
**measure players against a realistic replacement player instead of an
impossible one.**

Not the worst-in-each-category Frankenstein. An **archetype** — the
*average* of that wide replacement tier. One coherent, realistic stat line
that says: "this is what freely-available talent at this position actually
looks like." A replacement-level second baseman. A replacement-level
catcher. Real, average, attainable.

Measure everyone against *that*, and the meaning of a dollar snaps back
into focus. A player who produces exactly like the replacement archetype
scores zero — and is worth zero. Exactly as he should be. He's free; the
model finally agrees.

This is the difference between asking "are you better than a creature who
is the worst at everything?" (everyone is — useless question) and "are you
better than the actual guy on the waiver wire?" (now we're talking).

---

## Fix #3: Letting Value Go Negative

The archetype helped, but replacement players still floated around $10
instead of $0. One more habit had to go.

The system had a reflex: any below-baseline production got rounded **up to
zero**. The intention was kind — don't let a player post negative dollars
in a category. But the kindness was the problem.

Think about an average replacement player. By definition his production
zig-zags around the archetype — a little above in three categories, a
little below in three. If you keep the "above" parts and *erase* the
"below" parts by rounding them to zero, his total comes out **positive**.
You've quietly handed him the upside of his variance and refunded the
downside. Do that to every replacement player and you've rebuilt the
floor you just tore down — lower, but still a floor.

So we let the negatives stand. If a player is below replacement level in
on-base percentage, that's **negative value**, and it should count against
him. A replacement-level player's small surpluses and small deficits now
cancel out the way they always should have — and he lands where he
belongs, at roughly **zero**.

This also quietly fixed something managers will recognize as *true to
life*: sometimes a replacement-level player is genuinely better than a
rostered player in one specific category. That's not an error to be
smoothed away — it's **information**. If you're bleeding stolen bases every
week, the model should be willing to tell you "the best steals left are
down in the replacement tier — go reach for one." A valuation that flattens
that signal is lying to you to look tidy.

---

## The Stubborn Exceptions: Speed, and Positions That Can't Do a Thing

Two problems refused to die, and both are genuinely interesting.

**Speed is weird.** Stolen bases don't behave like home runs. Most of the
league hovers near zero steals — plenty of legitimately good hitters won't
swipe a bag all season — while a handful of burners run wild. Worse, those
burners often *aren't* good enough overall to get rostered, so they pile up
in the replacement tier and poison its average. The "replacement-level
steals" number gets dragged sky-high by a few guys who do nothing else.

Our fix treats speed as the special case it is: **stolen bases get a flat,
league-wide baseline of one.** Steal more than about one base a year and
you're adding value; don't, and you aren't. Simple, stable, and honest
about how the category actually works. Positional scarcity — the fact that
a base-stealing catcher is rarer and more precious than a base-stealing
outfielder — is still respected, because it lives in a different part of
the math (how the budget is split between positions), not in the baseline.

**Some positions are simply below replacement at some skills.** First
basemen, as a group, do not steal bases. Catchers don't either. This isn't
noise — it's the structural truth of the position. And it creates a genuine
paradox for the math: if the rostered first basemen, as a group, produce
*fewer* steals than freely-available first basemen, then "steals above
replacement" for the position is *negative*, and the
budget-division step (`budget ÷ total z`) falls apart — you can't divide a
real budget across production that nets out below zero.

The honest answer is a **conditional baseline**. For the overwhelming
majority of position-category pairs — the well-behaved ones — the archetype
works perfectly and replacement players price near zero. For the rare
pockets where a position is genuinely sub-replacement at a skill, the
system detects it and falls back, *for that pocket only*, to the older
worst-rostered baseline — just enough to keep the budget math sane. It's a
small, surgical exception, not a global compromise. Roughly three or four
cells out of forty-two, per projection set.

---

## The Payoff

Here's the same shortstop, Konnor Griffin, before and after:

| | Replacement-tier price |
|---|---|
| **Original system** (the phantom) | **$13.74** |
| **Rebuilt system** (archetype + signed value + conditional baseline) | **≈ $0–3** |

And he's not alone. The whole replacement tier at shortstop used to sit in
a tight, suspicious band around **$12–14**. It now spreads naturally from
roughly **$8 down through $0 and into the negatives** — exactly the shape
you'd expect from "the best of the free guys" down to "you'd have to be
paid to roster him." The rostered stars, freed from standing on a
phantom-sized pedestal, spread out too: the elite shortstops now command
genuine premiums instead of being compressed toward the pack.

Every projection source the engine runs now balances to the exact league
budget, to the penny. And replacement-level players read as what they are:
**approximately free.**

---

## The Lesson: Replacement Level Is a Mirror, Not a Floor

If there's a takeaway beyond the spreadsheet, it's this.

The original system kept trying to find a stable "floor" — a worst-case
zero point to measure everyone against. And it kept reaching for the
*worst possible* floor, on the theory that a lower floor is a safer floor.
But the lowest imaginable floor turned out to be imaginary in the literal
sense: a player who doesn't exist. The model was chasing an equilibrium
that the universe of real baseball players simply refuses to contain.

The fix wasn't a better floor. It was abandoning the idea of a floor and
adopting a **mirror** — an archetype that reflects the actual,
average, attainable replacement player back at the system. Once you measure
against something real, "value" stops being a number you inflate and
becomes a number you can *trust*: the honest distance between a player and
the guy you'd get for nothing.

And the exceptions — speed, the positions that can't run — are a reminder
that a valuation model isn't a physics engine searching for one universal
constant. It's an economic one, full of local truths. First basemen don't
steal. Catchers don't run. A replacement-level player can still be the best
source of a scarce category left on the board. Good valuation doesn't
sand those truths down to look elegant. It prices them in — and hands you,
the manager, the information to act on them.

The replacement-level player should cost about nothing. Now he does. And
the road to that one boring, correct number ran straight through a ghost.

---

*Methodology notes: valuations are built per position pool, with category
budgets allocated by production share and converted via per-category $/Z
rates. The replacement tier is sized at 50% of rostered slots; the
replacement archetype is the tier's per-category mean; stolen bases use a
fixed league-wide baseline; below-archetype production is signed (negative
allowed, no clamp); and a conditional worst-rostered fallback engages only
for position-category pairs that net out below replacement level.*
