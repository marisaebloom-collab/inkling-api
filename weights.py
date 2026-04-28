from __future__ import annotations
# weights.py — v5
# Calibrated against 529 read+rated books (800 books total), 2026-04-20
#
# Changes from v4:
#   - R8_TooLong, R9_ContentWarnings, R10_TranslationQuality, R11_DatedContent
#     explicitly zeroed (no signal on current data; tags retained for future users)
#   - Trope lift term added (T_ tags, author-adjusted shrunk lifts, library-size gated)
#   - V, G0, G1 tags confirmed display-only (no score contribution)
#
# Validation on 529 rated books + 17 DNFs as 1★:
#   5★ recall:           90.7%
#   Strong Keep 4+ prec: 97.0%
#   Strong Keep 5★ prec: 73.9%
#   Cut has 5★:           3.0% (4 books)

# ── Tag name lists ────────────────────────────────────────────────────────────

RISK_TAGS = [
    'R1_Slow', 'R2_Repetitive', 'R3_VibeClash', 'R4_HighConcept',
    'R5_InaccessibleProse', 'R6_WeakWriting', 'R7_SeriesFatigue',
    'R8_TooLong', 'R9_ContentWarnings', 'R10_TranslationQuality', 'R11_DatedContent',
]
REWARD_TAGS = [
    'P1_Distinctive', 'P2_Propulsive', 'P3_Emotional', 'P4_Clever',
    'P5_Structure', 'P6_Voice', 'P7_Satisfying',
]
VIBE_TAGS = [
    'V2_Dark', 'V4_PlotDriven', 'V5_Atmospheric',
    'V6_Funny', 'V7_Unsettling', 'V8_Philosophical',
    'V9_Heartbreaking', 'V10_Cozy',
]
# G0 and G1 tags are display-only — not listed here but present in tag output
ALL_TAGS = RISK_TAGS + REWARD_TAGS + VIBE_TAGS

# ── Risk weights ──────────────────────────────────────────────────────────────

RISK_WEIGHTS = {
    'R1_Slow':                0.09,
    'R2_Repetitive':          0.11,
    'R3_VibeClash':           0.07,
    'R4_HighConcept':         0.13,
    'R5_InaccessibleProse':   0.07,
    'R6_WeakWriting':         0.23,  # Dominant signal (-0.439 corr with rating)
    'R7_SeriesFatigue':       0.12,
    'R8_TooLong':             0.00,  # Zeroed — no signal on current data. Keep in tag set.
    'R9_ContentWarnings':     0.00,  # Zeroed — reader likes dark content. Keep in tag set.
    'R10_TranslationQuality': 0.00,  # Zeroed — n=1, no signal yet. Keep in tag set.
    'R11_DatedContent':       0.00,  # Zeroed — no signal on current data. Keep in tag set.
}

# ── Reward weights ────────────────────────────────────────────────────────────

REWARD_WEIGHTS = {
    'P1_Distinctive': 0.12,
    'P2_Propulsive':  0.15,
    'P3_Emotional':   0.22,  # +0.371 corr
    'P4_Clever':      0.10,
    'P5_Structure':   0.08,
    'P6_Voice':       0.10,
    'P7_Satisfying':  0.23,  # +0.491 corr — strongest single predictor
}

# ── Multipliers ───────────────────────────────────────────────────────────────

RISK_MULT   = 0.65
REWARD_MULT = 0.30
CRIT_MAX    = 0.12
DIV_BOOST   = 0.08
DIV_THRESH  = 0.50

# ── Trope lift term (v5 new) ──────────────────────────────────────────────────
# Author-adjusted Bayesian-shrunk lifts computed from reader's rated history.
# shrunk_lift[t] = (n / (n + K)) * mean(rating_i - author_avg_i | trope_t present)
# DNFs counted as 1★. Recompute when user has rated significantly more books.

TROPE_SHRINKAGE_K  = 15
TROPE_NORMALIZER   = 1.5   # clips raw sum to [-1, +1]
TROPE_MULT_FULL    = 0.10  # applied when library >= 300 rated books
TROPE_MULT_MID     = 0.06  # 200–299 rated books
TROPE_MULT_LOW     = 0.03  # 100–199 rated books
TROPE_MULT_NONE    = 0.00  # < 100 rated books — display only, no score

def get_trope_multiplier(n_rated: int) -> float:
    if n_rated >= 300: return TROPE_MULT_FULL
    if n_rated >= 200: return TROPE_MULT_MID
    if n_rated >= 100: return TROPE_MULT_LOW
    return TROPE_MULT_NONE

# Pre-computed lifts for Marisa's library (529 training books, 2026-04-20)
# Recompute via pipeline.py when library grows significantly (>50 new rated books)
TROPE_LIFTS = {
    'T_Addiction':               -0.0067,
    'T_Age_Gap':                 +0.0631,
    'T_AI_Robots':               -0.0000,
    'T_Amnesia':                 +0.0714,
    'T_Anti_Hero':                0.0000,
    'T_Art_Creativity':          +0.0132,
    'T_Band_of_Misfits':         +0.0323,
    'T_Boarding_School':         +0.0231,
    'T_Books_Libraries':         +0.0475,
    'T_Chosen_One':              +0.0019,
    'T_Class_Society':           +0.0098,
    'T_Cold_Case':                0.0000,
    'T_Demons_Angels':           +0.1078,
    'T_Dragons':                 +0.0145,
    'T_Fae_Faerie':              +0.0465,
    'T_Fake_Dating':              0.0000,
    'T_Fish_Out_of_Water':       +0.0095,
    'T_Forced_Proximity':        +0.1389,
    'T_Found_Family':            -0.0129,
    'T_Found_Purpose':           +0.0713,
    'T_Frame_Narrative':         -0.0021,
    'T_Gods_Mythology':          +0.0036,
    'T_Grief_Loss':              +0.0276,
    'T_Ghosts_Spirits':          -0.0278,
    'T_Heist':                   +0.0417,
    'T_Hidden_Identity':         +0.0717,
    'T_Hidden_World':            +0.0096,
    'T_Identity_Belonging':      +0.0101,
    'T_Island_Isolated_Setting': +0.1875,
    'T_Locked_Room':              0.0000,
    'T_Magic_System':            +0.0290,
    'T_Mental_Health':           +0.0052,
    'T_Missing_Person':          -0.0073,
    'T_Mentor_Protege':          +0.0357,
    'T_Morally_Grey_Protagonist':-0.0190,
    'T_Necromancy':              -0.0568,
    'T_One_Bed':                  0.0000,
    'T_Outsider_POV':            +0.0520,
    'T_Parallel_Timelines':      +0.0268,
    'T_Politics_Revolution':     -0.0085,
    'T_Post_Apocalyptic':        -0.0094,
    'T_Power_Corruption':        +0.0544,
    'T_Prophecy':                +0.0390,
    'T_Quest_Journey':           +0.0210,
    'T_Redemption_Arc':          +0.0170,
    'T_Reluctant_Hero':          +0.0945,
    'T_Revenge_Plot':            +0.0094,
    'T_Rivals_to_Lovers':        -0.0400,
    'T_Road_Trip':               -0.0556,
    'T_Second_Chance_Romance':   -0.0114,
    'T_Secret_Society':          +0.0303,
    'T_Slow_Burn':               +0.0017,
    'T_Small_Town':              +0.0094,
    'T_Space_Exploration':       -0.0217,
    'T_Story_Within_a_Story':    -0.0195,
    'T_Superpowers':             +0.0039,
    'T_Survival':                -0.0844,
    'T_Time_Loop':               +0.0044,
    'T_Tournament_Competition':  +0.0119,
    'T_Trauma_Recovery':         +0.0166,
    'T_Twist_Ending':            +0.0667,
    'T_Underdog':                +0.0833,
    'T_Unreliable_Narrator':     +0.0124,
    'T_Unrequited_Love':         -0.0080,
    'T_Vampires':                +0.0400,
    'T_Villain_Protagonist':     +0.0435,
    'T_War_Aftermath':           -0.0783,
    'T_Werewolves':               0.0000,
    'T_Witches_Warlocks':        +0.0851,
}

# ── Tier thresholds (for UI display) ─────────────────────────────────────────
# Controls which tags show as Strong / Moderate / Soft in results screen.
# Strong + Moderate visible by default; Soft behind tap-to-expand.

RISK_TIERS = {
    'Strong':   lambda w: w >= 0.18,
    'Moderate': lambda w: 0.10 <= w < 0.18,
    'Soft':     lambda w: w < 0.10,
}
REWARD_TIERS = {
    'Strong':   lambda w: w >= 0.20,
    'Moderate': lambda w: 0.12 <= w < 0.20,
    'Soft':     lambda w: w < 0.12,
}
TROPE_TIERS = {
    'Strong':   lambda l: abs(l) >= 0.10,
    'Moderate': lambda l: 0.05 <= abs(l) < 0.10,
    'Soft':     lambda l: 0.02 <= abs(l) < 0.05,
    'Neutral':  lambda l: abs(l) < 0.02,
}

def risk_tier(tag: str, weights: dict | None = None) -> str:
    w = (weights or RISK_WEIGHTS).get(tag, 0)
    if w == 0: return 'Soft'
    if RISK_TIERS['Strong'](w): return 'Strong'
    if RISK_TIERS['Moderate'](w): return 'Moderate'
    return 'Soft'

def reward_tier(tag: str, weights: dict | None = None) -> str:
    w = (weights or REWARD_WEIGHTS).get(tag, 0)
    if REWARD_TIERS['Strong'](w): return 'Strong'
    if REWARD_TIERS['Moderate'](w): return 'Moderate'
    return 'Soft'

def trope_tier(tag: str) -> tuple:
    """Returns (tier, sign) where sign in 'positive' | 'neutral' | 'negative'."""
    l = TROPE_LIFTS.get(tag, 0.0)
    if TROPE_TIERS['Neutral'](l): return ('Neutral', 'neutral')
    sign = 'positive' if l > 0 else 'negative'
    if TROPE_TIERS['Strong'](l): return ('Strong', sign)
    if TROPE_TIERS['Moderate'](l): return ('Moderate', sign)
    return ('Soft', sign)

# ── Bucket thresholds ─────────────────────────────────────────────────────────

THRESHOLDS = {
    'Strong Keep': 0.90,
    'Keep':        0.75,
    'Maybe':       0.60,
}

BUCKET_DISPLAY = {
    'Strong Keep': 'Strong Inkling',
    'Keep':        'Strong Inkling',
    'Maybe':       'On the Fence',
    'Cut':         'Hard Pass',
}
