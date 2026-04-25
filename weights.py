# weights.py — v5 algorithm weights

WEIGHTS = {
    # Base score
    'w_pred5':    0.50,
    'w_author':   0.40,
    'w_momentum': 0.10,

    # Risk penalty (multiplicative × risk_mult against base)
    'risk_mult':                 0.70,
    'R1_Slow':                   0.03,
    'R2_Repetitive':             0.12,
    'R3a_CharacterDisconnect':   0.06,
    'R3b_VibeClash':             0.10,
    'R4_HighConcept':            0.05,
    'R5_Dense':                  0.12,
    'R6_WeakWriting':            0.10,
    'R7_SeriesFatigue':          0.15,
    'R8_LowPayoff':              0.09,
    'R9_UnconvincingRelationship': 0.09,
    'R10_UnderdevelopedConcept': 0.09,
    'R11_LowSubstance':          0.11,
    'R12_PoorCohesion':          0.09,
    'R13_EmptyIntensity':        0.08,
    'R14_LowFantasyPayoff':      0.09,
    'R15_FlatExecution':         0.07,

    # Reward boost (additive × reward_mult)
    'reward_mult':    0.30,
    'P1_Distinctive': 0.25,
    'P2_Propulsive':  0.20,
    'P3_Emotional':   0.15,  # graded 0 / 0.5 / 1.0
    'P4_Clever':      0.15,  # graded 0 / 0.5 / 1.0; halved if P3_Emotional ≤ 0.5
    'P5_Structure':   0.15,
    'P6_Voice':       0.10,

    # Critical acclaim bonus (scaled by crit level 0–3)
    'crit_max':   0.15,

    # Crowd divergence bonus
    'div_boost':  0.08,
    'div_thresh': 0.50,

    # Interaction penalties (direct subtraction from raw score)
    'int_r4_r12':    0.05,  # R4_HighConcept + R12_PoorCohesion → confusion stack
    'int_r1_payoff': 0.04,  # R1_Slow + (R8_LowPayoff or R14_LowFantasyPayoff)
}

THRESHOLDS = {
    'Strong Keep': 0.90,
    'Keep':        0.75,
    'Maybe':       0.60,
    # below 0.60 = Cut
}

BUCKET_DISPLAY = {
    'Strong Keep': 'Strong Inkling',
    'Keep':        'Strong Inkling',
    'Maybe':       'On the Fence',
    'Cut':         'Hard Pass',
}
