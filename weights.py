# weights.py — all tunable constants in one place

WEIGHTS = {
    # Base score
    'w_pred5':    0.50,
    'w_author':   0.40,
    'w_momentum': 0.10,

    # Risk penalty
    'risk_mult': 0.70,
    'R1_Slow':        0.18,
    'R2_Repetitive':  0.12,
    'R3_VibeClash':   0.08,
    'R4_HighConcept': 0.05,
    'R5_Dense':       0.12,
    'R6_WeakWriting': 0.20,
    'R7_SeriesFatigue': 0.15,

    # Reward boost
    'reward_mult':   0.30,
    'P1_Distinctive': 0.25,
    'P2_Propulsive':  0.20,
    'P3_Emotional':   0.15,
    'P4_Clever':      0.15,
    'P5_Structure':   0.15,
    'P6_Voice':       0.10,

    # Bonuses
    'crit_max':   0.15,
    'div_boost':  0.08,
    'div_thresh': 0.5,
}

THRESHOLDS = {
    'Strong Keep': 0.90,
    'Keep':        0.75,
    'Maybe':       0.60,
    # below 0.60 = Cut
}

# Map app verdict names to internal bucket names
BUCKET_DISPLAY = {
    'Strong Keep': 'Strong Inkling',
    'Keep':        'Strong Inkling',
    'Maybe':       'On the Fence',
    'Cut':         'Pass on It',
}
