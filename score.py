# score.py — v5 scoring

from weights import WEIGHTS, THRESHOLDS, BUCKET_DISPLAY

RISK_TAGS = [
    'R1_Slow', 'R2_Repetitive', 'R3a_CharacterDisconnect', 'R3b_VibeClash',
    'R4_HighConcept', 'R5_Dense', 'R6_WeakWriting', 'R7_SeriesFatigue',
    'R8_LowPayoff', 'R9_UnconvincingRelationship', 'R10_UnderdevelopedConcept',
    'R11_LowSubstance', 'R12_PoorCohesion', 'R13_EmptyIntensity',
    'R14_LowFantasyPayoff', 'R15_FlatExecution',
]


def score_book(book: dict, tags: dict) -> dict:
    """
    Args:
        book: dict with keys:
            pred5               float 0-1   Predicted 5-star probability
            author_avg          float 0-5   Reader's avg rating for author (0=unknown)
            momentum            int   0-2   Author recency signal
            gr_avg              float       Goodreads average rating
            critical_reception  int   0-3
        tags: dict with R1_Slow–R15_FlatExecution (0/1), P1–P6 (P3/P4 graded 0/0.5/1.0)

    Returns:
        dict with risk_score, reward_score, master_score, bucket, verdict, pct_match
    """
    W = WEIGHTS

    pred5      = float(book.get('pred5', 0))
    author_avg = float(book.get('author_avg', 0))
    momentum   = float(book.get('momentum', 0))
    gr_avg     = float(book.get('gr_avg', 0))
    crit       = int(book.get('critical_reception', 0))

    # Base score
    author_signal   = (author_avg / 5.0) if author_avg > 0 else pred5
    momentum_signal = (momentum / 2.0)   if momentum  > 0 else 0.5
    base = (W['w_pred5'] * pred5
            + W['w_author']   * author_signal
            + W['w_momentum'] * momentum_signal)

    # Risk penalty (multiplicative)
    risk = sum(W[tag] * float(tags.get(tag, 0)) for tag in RISK_TAGS if tag in W)
    penalized = base * (1 - W['risk_mult'] * risk)

    # Reward boost — P3/P4 are graded (0, 0.5, 1.0)
    p3_val = float(tags.get('P3_Emotional', 0))
    p4_val = float(tags.get('P4_Clever', 0))
    if p3_val <= 0.5:
        p4_val *= 0.5

    reward = (
        W['P1_Distinctive'] * float(tags.get('P1_Distinctive', 0))
        + W['P2_Propulsive'] * float(tags.get('P2_Propulsive', 0))
        + W['P3_Emotional'] * p3_val
        + W['P4_Clever']    * p4_val
        + W['P5_Structure'] * float(tags.get('P5_Structure', 0))
        + W['P6_Voice']     * float(tags.get('P6_Voice', 0))
    )
    boost = W['reward_mult'] * reward

    # Critical reception bonus
    crit_boost = (crit / 3.0) * W['crit_max'] if crit > 0 else 0.0

    # Crowd divergence bonus
    div_bonus = (W['div_boost']
                 if author_avg > 0 and (author_avg - gr_avg) > W['div_thresh']
                 else 0.0)

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
    if   score >= THRESHOLDS['Strong Keep']: bucket = 'Strong Keep'
    elif score >= THRESHOLDS['Keep']:        bucket = 'Keep'
    elif score >= THRESHOLDS['Maybe']:       bucket = 'Maybe'
    else:                                    bucket = 'Cut'

    return {
        'risk_score':   round(risk, 4),
        'reward_score': round(reward, 4),
        'master_score': score,
        'bucket':       bucket,
        'verdict':      BUCKET_DISPLAY[bucket],
        'pct_match':    round(score * 100),
    }
