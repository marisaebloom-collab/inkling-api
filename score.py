# score.py — v5
from __future__ import annotations
import numpy as np
from weights import (
    RISK_WEIGHTS, REWARD_WEIGHTS,
    RISK_MULT, REWARD_MULT,
    CRIT_MAX, DIV_BOOST, DIV_THRESH,
    THRESHOLDS, BUCKET_DISPLAY,
    TROPE_LIFTS, TROPE_NORMALIZER,
    get_trope_multiplier,
    risk_tier, reward_tier, trope_tier,
)


def get_bucket(score: float) -> str:
    if score >= THRESHOLDS['Strong Keep']: return 'Strong Keep'
    if score >= THRESHOLDS['Keep']:        return 'Keep'
    if score >= THRESHOLDS['Maybe']:       return 'Maybe'
    return 'Cut'


def _merge_weights(user_weights: dict | None) -> tuple[dict, dict, dict]:
    """Merge per-user calibration weights with global defaults.

    Returns (risk_w, reward_w, comp_w). Per-user values override globals;
    unrecognised tags are ignored so stale calibration data can't break scoring.
    """
    risk_w   = dict(RISK_WEIGHTS)
    reward_w = dict(REWARD_WEIGHTS)
    comp_w   = {'w_pred5': 0.50, 'w_author': 0.40, 'w_momentum': 0.10}

    if not user_weights:
        return risk_w, reward_w, comp_w

    for tag, val in user_weights.get('risk_weights', {}).items():
        if tag in risk_w:
            risk_w[tag] = float(val)
    for tag, val in user_weights.get('reward_weights', {}).items():
        if tag in reward_w:
            reward_w[tag] = float(val)
    cw = user_weights.get('component_weights', {})
    if cw.get('w_pred5'):    comp_w['w_pred5']    = float(cw['w_pred5'])
    if cw.get('w_author'):   comp_w['w_author']   = float(cw['w_author'])
    if cw.get('w_momentum'): comp_w['w_momentum'] = float(cw['w_momentum'])

    return risk_w, reward_w, comp_w


def _trope_contribution(tags: dict, lifts: dict, n_rated: int) -> float:
    """
    Compute the trope score term.

    Sums the shrunk lifts for all T_ tags present in the book,
    normalizes to [-1, +1], then scales by the library-size multiplier.
    """
    raw = sum(lifts.get(t, 0.0) for t in lifts if tags.get(t, 0) == 1)
    normalized = float(np.clip(raw / TROPE_NORMALIZER, -1.0, 1.0))
    return get_trope_multiplier(n_rated) * normalized


def score_book(book: dict, tags: dict,
               n_rated: int = 300,
               trope_lifts: dict | None = None,
               user_weights: dict | None = None) -> dict:
    """
    Score a single book under v5.

    Args:
        book: dict with keys:
            pred5               float 0-1   Predicted 5★ probability
            author_avg          float 0-5   Reader's avg rating for author (0=unknown)
            momentum            int   0-2   Author recency signal
            gr_avg              float       Goodreads average rating
            critical_reception  int   0-3
        tags: dict with R1-R11, P1-P7, T_* as 0/1 ints.
              V, G0, G1 tags are accepted but do not affect score.
        n_rated: user's count of rated + DNF books (controls trope multiplier).
                 Defaults to 300 (full multiplier) for single-book lookups.
        trope_lifts: optional user-specific lift table. Defaults to TROPE_LIFTS.
        user_weights: optional dict from UserSettings.algorithm_weights (parsed JSON).
                      If None, falls back to global weights.py values.

    Returns:
        dict with:
            master_score    float 0-1
            bucket          'Strong Keep' | 'Keep' | 'Maybe' | 'Cut'
            verdict         'Strong Inkling' | 'On the Fence' | 'Hard Pass'
            pct_match       int 0-100
            risk_score      float
            reward_score    float
            trope_contrib   float
            risks           list of (tag, weight, tier) for present active risk tags
            rewards         list of (tag, weight, tier) for present reward tags
            tropes          list of (tag, lift, tier, sign) sorted by |lift| desc
    """
    if trope_lifts is None:
        trope_lifts = TROPE_LIFTS

    risk_w, reward_w, comp_w = _merge_weights(user_weights)

    pred5      = float(book.get('pred5', 0) or 0)
    author_avg = float(book.get('author_avg', 0) or 0)
    momentum   = float(book.get('momentum', 0) or 0)
    gr_avg     = float(book.get('gr_avg', 0) or 0)
    crit       = int(book.get('critical_reception', 0) or 0)

    # ── Base score ────────────────────────────────────────────────────────────
    author_signal   = (author_avg / 5.0) if author_avg > 0 else pred5
    momentum_signal = (momentum / 2.0)   if momentum  > 0 else 0.5
    base = (comp_w['w_pred5']    * pred5
          + comp_w['w_author']   * author_signal
          + comp_w['w_momentum'] * momentum_signal)

    # ── Risk penalty (multiplicative) ─────────────────────────────────────────
    risk_score = sum(risk_w.get(k, 0) * float(tags.get(k, 0) or 0)
                     for k in risk_w)
    penalized = base * (1 - RISK_MULT * risk_score)

    # ── Reward boost (additive) ───────────────────────────────────────────────
    reward_score = sum(reward_w.get(k, 0) * float(tags.get(k, 0) or 0)
                       for k in reward_w)
    boost = REWARD_MULT * reward_score

    # ── Critical reception boost ──────────────────────────────────────────────
    crit_boost = (crit / 3.0) * CRIT_MAX if crit > 0 else 0.0

    # ── Crowd divergence boost ────────────────────────────────────────────────
    div_bonus = (DIV_BOOST
                 if author_avg > 0 and (author_avg - gr_avg) > DIV_THRESH
                 else 0.0)

    # ── Trope lift term ───────────────────────────────────────────────────────
    trope_adj = _trope_contribution(tags, trope_lifts, n_rated)

    # ── Combine, clip, round ──────────────────────────────────────────────────
    raw    = penalized + boost + crit_boost + div_bonus + trope_adj
    score  = round(max(0.0, min(1.0, raw)), 4)
    bucket = get_bucket(score)

    # ── UI tag lists ──────────────────────────────────────────────────────────
    present_risks = sorted(
        [(t, risk_w[t], risk_tier(t, risk_w))
         for t in risk_w if tags.get(t, 0) == 1 and risk_w[t] > 0],
        key=lambda x: x[1], reverse=True
    )
    present_rewards = sorted(
        [(t, reward_w[t], reward_tier(t, reward_w))
         for t in reward_w if tags.get(t, 0) == 1],
        key=lambda x: x[1], reverse=True
    )
    present_tropes = sorted(
        [(t, trope_lifts[t], *trope_tier(t))
         for t in trope_lifts if tags.get(t, 0) == 1],
        key=lambda x: abs(x[1]), reverse=True
    )

    return {
        'master_score':  score,
        'bucket':        bucket,
        'verdict':       BUCKET_DISPLAY[bucket],
        'pct_match':     int(round(score * 100)),
        'risk_score':    round(risk_score, 4),
        'reward_score':  round(reward_score, 4),
        'trope_contrib': round(trope_adj, 4),
        'risks':         present_risks,
        'rewards':       present_rewards,
        'tropes':        present_tropes,
    }
