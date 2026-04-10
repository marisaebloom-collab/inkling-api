# score.py — core scoring function

from weights import WEIGHTS, THRESHOLDS, BUCKET_DISPLAY


def score_book(book: dict, tags: dict) -> dict:
    """
    Args:
        book: dict with keys:
            pred5             float 0-1   Predicted 5-star probability
            author_avg        float 0-5   Reader's avg rating for author (0=unknown)
            momentum          int   0-2   Author recency signal
            gr_avg            float       Goodreads average rating
            critical_reception int  0-3
        tags: dict with R1_Slow … R7_SeriesFatigue, P1_Distinctive … P6_Voice as 0/1 ints

    Returns:
        dict with risk_score, reward_score, master_score, bucket, verdict
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

    # Risk penalty
    risk = sum(
        W[tag] * float(tags.get(tag, 0))
        for tag in ['R1_Slow','R2_Repetitive','R3_VibeClash','R4_HighConcept',
                    'R5_Dense','R6_WeakWriting','R7_SeriesFatigue']
    )
    penalized = base * (1 - W['risk_mult'] * risk)

    # Reward boost
    reward = sum(
        W[tag] * float(tags.get(tag, 0))
        for tag in ['P1_Distinctive','P2_Propulsive','P3_Emotional',
                    'P4_Clever','P5_Structure','P6_Voice']
    )
    boost = W['reward_mult'] * reward

    # Critical reception bonus
    crit_boost = (crit / 3.0) * W['crit_max'] if crit > 0 else 0.0

    # Crowd divergence bonus
    div_bonus = (W['div_boost']
                 if author_avg > 0 and (author_avg - gr_avg) > W['div_thresh']
                 else 0.0)

    # Combine, clip, round
    raw   = penalized + boost + crit_boost + div_bonus
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
