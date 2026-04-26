# score.py — v5 scoring with optional per-user weight override

from weights import WEIGHTS, THRESHOLDS, BUCKET_DISPLAY

RISK_TAGS = [
    'R1_Slow', 'R2_Repetitive', 'R3a_CharacterDisconnect', 'R3b_VibeClash',
    'R4_HighConcept', 'R5_Dense', 'R6_WeakWriting', 'R7_SeriesFatigue',
    'R8_LowPayoff', 'R9_UnconvincingRelationship', 'R10_UnderdevelopedConcept',
    'R11_LowSubstance', 'R12_PoorCohesion', 'R13_EmptyIntensity',
    'R14_LowFantasyPayoff', 'R15_FlatExecution',
    # New tags (calibration-era additions)
    'R16_HeavyWorldBuilding', 'R17_RomanceOverPlot',
    'R18_DisturbingContent',  'R19_EnsembleOverload',
]


def _merge_weights(user_weights: dict | None) -> tuple[dict, dict]:
    """Merge per-user calibration weights with global defaults.

    Returns (W, thresholds) where W is the effective weight dict.
    Per-user values override globals; anything missing falls back to WEIGHTS.
    """
    if not user_weights:
        return dict(WEIGHTS), dict(THRESHOLDS)

    W = dict(WEIGHTS)   # start from global defaults

    cw = user_weights.get('component_weights', {})
    if cw.get('w_pred5'):    W['w_pred5']    = float(cw['w_pred5'])
    if cw.get('w_author'):   W['w_author']   = float(cw['w_author'])
    if cw.get('w_momentum'): W['w_momentum'] = float(cw['w_momentum'])

    for tag, val in user_weights.get('reward_weights', {}).items():
        W[tag] = float(val)

    for tag, val in user_weights.get('risk_weights', {}).items():
        W[tag] = float(val)

    # Thresholds stay global for now (UserSettings can override later)
    return W, dict(THRESHOLDS)


def score_book(book: dict, tags: dict, user_weights: dict | None = None) -> dict:
    """
    Args:
        book: dict with keys:
            pred5               float 0-1   Predicted 5-star probability
            author_avg          float 0-5   Reader's avg rating for author (0=unknown)
            momentum            int   0-2   Author recency signal
            gr_avg              float       Goodreads average rating
            critical_reception  int   0-3
        tags: dict with R/P/V tag keys (0/1, P3/P4 graded 0/0.5/1.0)
        user_weights: optional dict from UserSettings.algorithm_weights (parsed JSON).
                      If None, falls back to global weights.py values.

    Returns:
        dict with risk_score, reward_score, master_score, bucket, verdict, pct_match
    """
    W, thresholds = _merge_weights(user_weights)

    pred5      = float(book.get('pred5', 0))
    author_avg = float(book.get('author_avg', 0))
    momentum   = float(book.get('momentum', 0))
    gr_avg     = float(book.get('gr_avg', 0))
    crit       = int(book.get('critical_reception', 0))

    # Base score
    author_signal   = (author_avg / 5.0) if author_avg > 0 else pred5
    momentum_signal = (momentum / 2.0)   if momentum  > 0 else 0.5
    base = (W['w_pred5']    * pred5
          + W['w_author']   * author_signal
          + W['w_momentum'] * momentum_signal)

    # Risk penalty (multiplicative)
    risk = sum(W.get(tag, 0) * float(tags.get(tag, 0)) for tag in RISK_TAGS)
    penalized = base * (1 - W['risk_mult'] * risk)

    # Reward boost — P3/P4 are graded (0, 0.5, 1.0)
    p3_val = float(tags.get('P3_Emotional', 0))
    p4_val = float(tags.get('P4_Clever', 0))
    if p3_val <= 0.5:
        p4_val *= 0.5

    reward = (
        W.get('P1_Distinctive', 0) * float(tags.get('P1_Distinctive', 0))
      + W.get('P2_Propulsive',  0) * float(tags.get('P2_Propulsive',  0))
      + W.get('P3_Emotional',   0) * p3_val
      + W.get('P4_Clever',      0) * p4_val
      + W.get('P5_Structure',   0) * float(tags.get('P5_Structure',   0))
      + W.get('P6_Voice',       0) * float(tags.get('P6_Voice',       0))
      + W.get('P7_Lyrical',     0) * float(tags.get('P7_Lyrical',     0))
      + W.get('P8_MorallyComplex', 0) * float(tags.get('P8_MorallyComplex', 0))
      + W.get('P9_Humor',       0) * float(tags.get('P9_Humor',       0))
    )
    boost = W['reward_mult'] * reward

    # Critical reception bonus
    crit_boost = (crit / 3.0) * W['crit_max'] if crit > 0 else 0.0

    # Crowd divergence bonus
    div_bonus = (
        W['div_boost']
        if author_avg > 0 and (author_avg - gr_avg) > W['div_thresh']
        else 0.0
    )

    # Interaction penalties (direct subtraction)
    interaction = 0.0
    if tags.get('R4_HighConcept') and tags.get('R12_PoorCohesion'):
        interaction += W['int_r4_r12']
    if tags.get('R1_Slow') and (tags.get('R8_LowPayoff') or tags.get('R14_LowFantasyPayoff')):
        interaction += W['int_r1_payoff']

    # Combine, clip, round
    raw   = penalized + boost + crit_boost + div_bonus - interaction
    score = round(max(0.0, min(1.0, raw)), 4)

    # Bucket
    if   score >= thresholds['Strong Keep']: bucket = 'Strong Keep'
    elif score >= thresholds['Keep']:        bucket = 'Keep'
    elif score >= thresholds['Maybe']:       bucket = 'Maybe'
    else:                                    bucket = 'Cut'

    return {
        'risk_score':   round(risk, 4),
        'reward_score': round(reward, 4),
        'master_score': score,
        'bucket':       bucket,
        'verdict':      BUCKET_DISPLAY[bucket],
        'pct_match':    round(score * 100),
    }
