# Inkling Algorithm Handoff
# Version: 5 | Date: 2026-04-20 | Status: Production

---

## CRITICAL: READ THIS BEFORE MAKING ANY CHANGES

This document is the single source of truth for the Inkling scoring algorithm.
Every decision recorded here was made deliberately. Before changing anything,
read the relevant section and understand WHY it exists.

Prior Claude sessions have accidentally removed features that were load-bearing.
If you are a new Claude instance, do not simplify, consolidate, or remove tags,
weights, or terms without the user explicitly confirming it after reading the
rationale here.

---

## 1. What the algorithm does

Scores any book 0–1 based on a reader's personal Goodreads history. Higher score
= stronger match for that reader's taste. Maps to three user-facing verdicts:
- Strong Inkling (score ≥ 0.75)
- On the Fence (score 0.60–0.74)
- Hard Pass (score < 0.60)

Used as a point-of-decision tool — reader scans a book in a bookstore, gets
instant personalized match score with explanation.

---

## 2. Inputs

### 2a. Book-level (fetched at query time)
| Field | Source | Notes |
|---|---|---|
| pred5 | Computed (see §2c) | Predicted 5★ probability |
| author_avg | Reader's Goodreads history | 0 = unknown author |
| momentum | Reader's Goodreads history | 0–2, recency of author engagement |
| gr_avg | Goodreads / Google Books | Crowd average rating |
| critical_reception | AI tag | 0–3 scale |

### 2b. AI-generated tags (all 0/1 binary unless noted)
Called via Anthropic API (claude-sonnet-4-6, max_tokens=2048) for every new book lookup.

**Risk tags (R) — penalize score when present:**
| Tag | Weight | Meaning |
|---|---|---|
| R1_Slow | 0.09 | Slow or meandering pacing |
| R2_Repetitive | 0.11 | Repetitive sequel / diminishing series quality |
| R3_VibeClash | 0.07 | Tone mismatch for this reader's taste |
| R4_HighConcept | 0.13 | Great premise, weak execution risk |
| R5_InaccessibleProse | 0.07 | Dense or difficult prose |
| R6_WeakWriting | 0.23 | Flat, forgettable writing (DOMINANT signal) |
| R7_SeriesFatigue | 0.12 | Book 4+ in a numbered series |
| R8_TooLong | 0.00 | Zeroed — no signal on current data. Keep in tag set. |
| R9_ContentWarnings | 0.00 | Zeroed — reader likes dark content. Keep in tag set. |
| R10_TranslationQuality | 0.00 | Zeroed — n=1, no signal yet. Keep in tag set. |
| R11_DatedContent | 0.00 | Zeroed — no signal on current data. Keep in tag set. |

DO NOT REMOVE R8–R11 from the tag set or tagging prompt. They are zeroed
because there is insufficient data today, not because they are wrong.
Future users may have signal on these tags.

**Reward tags (P) — boost score when present:**
| Tag | Weight | Meaning |
|---|---|---|
| P1_Distinctive | 0.12 | Genuinely original concept or approach |
| P2_Propulsive | 0.15 | Hard to put down, page-turning experience (NOT the same as plot-driven) |
| P3_Emotional | 0.22 | Emotionally resonant (+0.371 corr with rating) |
| P4_Clever | 0.10 | Author did something smart/interesting/unique (NOT the same as funny) |
| P5_Structure | 0.08 | Unconventional structure |
| P6_Voice | 0.10 | Strong distinctive narrative voice |
| P7_Satisfying | 0.23 | Satisfying payoff / ending (+0.491 corr — strongest predictor) |

**Vibe tags (V) — DISPLAY ONLY, do not affect score:**
| Tag | Meaning | Note |
|---|---|---|
| V2_Dark | Dark or disturbing tone | DIFFERENT from R9_ContentWarnings (tone ≠ content) |
| V4_PlotDriven | Structured around plot mechanics | DIFFERENT from P2_Propulsive (structure ≠ page-turn feel) |
| V5_Atmospheric | Strong sense of place | Overfires (~70%) — display but don't weight heavily |
| V6_Funny | Makes you laugh | DIFFERENT from P4_Clever (humor ≠ intelligence) |
| V7_Unsettling | Unsettling or creepy tone | — |
| V8_Philosophical | Explores big ideas | — |
| V9_Heartbreaking | Emotionally devastating | — |
| V10_Cozy | Warm, comforting tone | — |

WHY NOT SCORED: Author-adjusted lifts collapse to near zero. Vibes correlate
with rating only because beloved authors happen to write in certain vibes —
not because the vibe itself predicts quality. V tags are taste/mood descriptors,
not quality predictors. Score them when Inkling has multi-user data.

DO NOT conflate these with their correlated R/P tags. They measure different things:
- V2_Dark ↔ R9_ContentWarnings: Dark = tone. ContentWarnings = specific difficult content.
- V4_PlotDriven ↔ P2_Propulsive: PlotDriven = structural type. Propulsive = reading feel.
- V6_Funny ↔ P4_Clever: Funny = makes you laugh. Clever = author did something smart.

**Genre tags (G0, G1) — DISPLAY ONLY, do not affect score:**
G0: top-level genre (Fantasy, Horror, Literary Fiction, etc.)
G1: subgenre (Dark Fantasy, Psychological Thriller, Gothic, etc.)

WHY NOT SCORED: Genre preference is overwhelmingly captured by author signal.
Adding G0/G1 to the score double-counts what author_avg already measures.
Useful for display, filtering, and the "Genres" section on the result screen.

**Trope tags (T) — SCORED via lift table (see §3c):**
69 binary trope tags. See TROPE_LIFTS in weights.py for current lift values.

### 2c. Predicted 5★ probability (pred5)
For books in the user's existing library: stored from logistic regression fit
on their rated books (author history + Goodreads avg as features).

For new books (not in library): computed at query time via:
```python
def pred5(gr_avg, author_avg, author_5star_rate, author_known):
    gr_norm = max(0, min(1, (gr_avg - 2.5) / 2.5))
    if author_known:
        return 0.55 * gr_norm + 0.45 * author_5star_rate
    return gr_norm * 0.85
```

---

## 3. Scoring formula

### 3a. Base score
```
author_signal   = author_avg / 5.0  (if known)  else pred5
momentum_signal = momentum / 2.0    (if > 0)     else 0.5
base = 0.50 × pred5 + 0.40 × author_signal + 0.10 × momentum_signal
```

### 3b. Risk, reward, bonuses
```
risk_score   = Σ (R_weight × R_tag) for R1–R11
reward_score = Σ (P_weight × P_tag) for P1–P7

penalized  = base × (1 − 0.65 × risk_score)
boost      = 0.30 × reward_score
crit_boost = (critical_reception / 3) × 0.12   (if crit > 0)
div_bonus  = 0.08  (if author_avg − gr_avg > 0.5 and author known)
```

### 3c. Trope lift term (v5 new)
```
raw_trope  = Σ TROPE_LIFTS[t] for all T_ tags present in book
normalized = clip(raw_trope / 1.5, −1.0, +1.0)
trope_adj  = get_trope_multiplier(n_rated) × normalized
```

Library-size gate (protects new users from noisy lifts):
| User's rated books | Multiplier |
|---|---|
| < 100 | 0.00 (display only) |
| 100–199 | 0.03 |
| 200–299 | 0.06 |
| ≥ 300 | 0.10 |

Lift computation method: author-adjusted Bayesian shrinkage (k=15).
```
raw_lift[t] = mean(rating_i − author_avg_i) for all books where trope t = 1
shrunk_lift[t] = (n / (n + 15)) × raw_lift[t]
```
DNFs count as 1★ in lift computation.
Recompute when user has rated ~50+ new books since last computation.

### 3d. Final score
```
raw   = penalized + boost + crit_boost + div_bonus + trope_adj
score = round(clip(raw, 0.0, 1.0), 4)
```

### 3e. One-line summary
```
score = CLIP(0,1,
  (0.50×pred5 + 0.40×(author_avg÷5 or pred5) + 0.10×(momentum÷2 or 0.5))
  × (1 − 0.65 × risk_sum)
  + 0.30 × reward_sum
  + (crit÷3 × 0.12)
  + (0.08 if author_avg − gr_avg > 0.5)
  + trope_multiplier × clip(trope_raw_sum ÷ 1.5, −1, +1)
)
```

---

## 4. Bucket thresholds and display

| Score | Internal bucket | User-facing verdict |
|---|---|---|
| ≥ 0.90 | Strong Keep | Strong Inkling |
| 0.75–0.89 | Keep | Strong Inkling |
| 0.60–0.74 | Maybe | On the Fence |
| < 0.60 | Cut | Hard Pass |

---

## 5. UI sections (result screen)

Saturation encodes strength tier for rewards and risks.
All tiers are always visible (no tap-to-expand on Soft — opacity differentiates instead).

**Rewards** (P tags)
- Sorted by weight descending. Tier determines chip opacity:
  - Strong (weight ≥ 0.20): full opacity — P3_Emotional, P7_Satisfying
  - Moderate (0.12–0.19): 75% opacity — P1_Distinctive, P2_Propulsive
  - Soft (< 0.12): 50% opacity — P4_Clever, P5_Structure, P6_Voice
- Positive-lift tropes (lift ≥ 0.02) appear as smaller teal chips below the reward chips

**Risks** (R tags, only non-zero weights shown)
- Sorted by weight descending. Tier determines chip opacity:
  - Strong (weight ≥ 0.18): full opacity — R6_WeakWriting
  - Moderate (0.10–0.17): 75% opacity — R2_Repetitive, R4_HighConcept, R7_SeriesFatigue
  - Soft (< 0.10): 50% opacity — R1_Slow, R3_VibeClash, R5_InaccessibleProse
- Negative-lift tropes (lift ≤ −0.02) appear as smaller rose chips below the risk chips

**Vibes** (V tags — descriptive only, no scoring)
- All shown in one neutral mauve color. No tier weighting.

**Additional Tropes & Themes** (T tags with |lift| < 0.02)
- Shown in a separate muted section below Risks, if any neutral-lift tropes are present.
- Teal/rose coloring does not apply — neutral gray chips only.
- Trope tier thresholds: Strong |lift| ≥ 0.10, Moderate 0.05–0.09, Soft 0.02–0.04, Neutral < 0.02

**Genres** (G0/G1 — descriptive only, no scoring)
- Flat display. G0 = top-level, G1 = subgenre.

---

## 6. Calibration confidence indicator

Shown on Profile page (prominent) and Result screen (subtle dot, tap to expand).

| User's rated books | Confidence level | Notes |
|---|---|---|
| < 100 | Low | Tropes not scored |
| 100–199 | Building | Tropes at 30% strength |
| 200–299 | Good | Tropes at 60% strength |
| ≥ 300 | Full | All terms active |

Copy direction: "Based on your X rated books, your Inkling is calibrated to Y."
Avoid: claiming specific accuracy percentages. Keep it relative to the thresholds.

---

## 7. Validation results (v5, 2026-04-20)

Training set: 529 rated books + 17 DNFs as 1★ = 546 total.

| Metric | v5 (current) | Prior baseline |
|---|---|---|
| 5★ recall | 90.7% | 89.2% |
| Strong Keep 4+ precision | 97.0% | 98.1% |
| Strong Keep 5★ precision | 73.9% | 75.0% |
| Cut contains 5★ | 3.0% (4 books) | 1.7% (2 books) |

Note: the 4 books in Cut with 5★ ratings — investigate these. They likely have
strong author signals that are overridden by heavy risk tags (R6_WeakWriting
or R4_HighConcept). Worth reviewing manually.

---

## 8. What was tried and rejected

**R8–R11 at non-zero weights:** No independent signal on current data. Retained
in tag set and tagging prompt for forward compatibility with future users.

**V tags in scoring:** Author-adjusted lifts collapse to ~zero. Vibes are taste
descriptors, not quality predictors. Revisit when multi-user data available.

**G0/G1 in scoring:** Genre preference is captured by author signal. Adding
these would double-count. Keep as display-only.

**Full trope multiplier for all users:** Small libraries produce noisy lifts that
actively mislead (~2/10 top tropes point wrong direction for 50-book libraries).
Library-size gate is essential for correctness.

**R9_ContentWarnings as active risk:** This reader likes dark content.
R9 fires positively for her. Zeroed because it's a net positive, not a neutral.

---

## 9. Known issues and future work

1. **Trope lifts need recomputation as library grows.** Current lifts are from
   529 books (2026-04-20). Rerun the lift pipeline when user has 50+ new ratings.

2. **4 five-star books in Cut** — should be investigated. Possibly systematic
   misfires on R6_WeakWriting or R4_HighConcept. Query `UserBook` for books
   with pred5 > 0.8 and low master_score to surface them.

3. **V5_Atmospheric fires on 70% of books** — the tagging prompt may be too loose.
   Consider tightening the definition: "atmospheric" should mean the setting is
   itself a character, not just "well-described."

4. **Multi-user lift computation** — when Inkling has multiple users, V tag lifts
   become meaningful (cross-user variation in vibe preference). Revisit scoring
   contribution at that point.

5. **TBR books have no rating** — the algorithm can score them (using gr_avg + author
   signal) but can't validate predictions on them. 235 TBR books in current library.

---

## 10. File structure

```
/
├── weights.py              ← all tunable constants (THIS IS THE SOURCE OF TRUTH)
├── score.py                ← score_book() function
├── main.py                 ← FastAPI server; get_tags(), format_tags(), all endpoints
├── upload.py               ← /library/upload; CSV parsing, calibration via Claude
├── auth.py                 ← JWT auth, register/login/apple endpoints
├── database.py             ← SQLAlchemy engine, get_db() dependency
├── models.py               ← User, UserBook, ScanResult, UserSettings ORM models
├── library.py              ← load_library(), find_book() (CSV fast-path)
├── scraper.py              ← author table builder from Goodreads CSV
├── inkling_mobile.html     ← complete frontend (~8.4MB, embedded videos)
└── algorithm_handoff.md   ← this file
```

---

## 11. How to update this document

Update this file whenever:
- Any weight changes
- Any tag is added, removed, or zeroed
- Validation is rerun
- A design decision is made about what to score vs. display
- Something is tried and rejected (add to §8)

Do NOT let this file fall out of sync with weights.py. They should always agree.
