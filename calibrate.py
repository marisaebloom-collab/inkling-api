"""calibrate.py — New-user calibration pipeline with SSE streaming.

Router prefix: /calibration

Provides GET /calibration/stream — an SSE endpoint that runs the full
algorithm build pipeline and narrates each phase as it genuinely completes.

The existing /library/calibrate endpoint (upload.py) is NOT touched — it
remains available for Marisa's dev flow.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

import anthropic
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import AuthorProfile, BookTags, User, UserBook, UserSettings

router = APIRouter(prefix='/calibration', tags=['calibration'])

# ── Tag catalogue (mirrors weights.py) ───────────────────────────────────────

TROPE_TAGS = [
    'T_Addiction', 'T_Age_Gap', 'T_AI_Robots', 'T_Amnesia', 'T_Anti_Hero',
    'T_Art_Creativity', 'T_Band_of_Misfits', 'T_Boarding_School', 'T_Books_Libraries',
    'T_Chosen_One', 'T_Class_Society', 'T_Cold_Case', 'T_Demons_Angels', 'T_Dragons',
    'T_Fae_Faerie', 'T_Fake_Dating', 'T_Fish_Out_of_Water', 'T_Forced_Proximity',
    'T_Found_Family', 'T_Found_Purpose', 'T_Frame_Narrative', 'T_Gods_Mythology',
    'T_Grief_Loss', 'T_Ghosts_Spirits', 'T_Heist', 'T_Hidden_Identity', 'T_Hidden_World',
    'T_Identity_Belonging', 'T_Island_Isolated_Setting', 'T_Locked_Room', 'T_Magic_System',
    'T_Mental_Health', 'T_Missing_Person', 'T_Mentor_Protege', 'T_Morally_Grey_Protagonist',
    'T_Necromancy', 'T_One_Bed', 'T_Outsider_POV', 'T_Parallel_Timelines',
    'T_Politics_Revolution', 'T_Post_Apocalyptic', 'T_Power_Corruption', 'T_Prophecy',
    'T_Quest_Journey', 'T_Redemption_Arc', 'T_Reluctant_Hero', 'T_Revenge_Plot',
    'T_Rivals_to_Lovers', 'T_Road_Trip', 'T_Second_Chance_Romance', 'T_Secret_Society',
    'T_Slow_Burn', 'T_Small_Town', 'T_Space_Exploration', 'T_Story_Within_a_Story',
    'T_Superpowers', 'T_Survival', 'T_Time_Loop', 'T_Tournament_Competition',
    'T_Trauma_Recovery', 'T_Twist_Ending', 'T_Underdog', 'T_Unreliable_Narrator',
    'T_Unrequited_Love', 'T_Vampires', 'T_Villain_Protagonist', 'T_War_Aftermath',
    'T_Werewolves', 'T_Witches_Warlocks',
]

VIBE_TAGS = ['V1_Speculative', 'V2_Dark', 'V3_Romantic', 'V4_PlotDriven',
             'V5_Atmospheric', 'V6_ShortOrStandalone']

REWARD_TAGS = ['P1_Distinctive', 'P2_Propulsive', 'P3_Emotional', 'P4_Clever',
               'P5_Structure', 'P6_Voice', 'P7_Satisfying']

RISK_TAGS = [
    'R1_Slow', 'R2_Repetitive', 'R3a_CharacterDisconnect', 'R3b_VibeClash',
    'R4_HighConcept', 'R5_Dense', 'R6_WeakWriting', 'R7_SeriesFatigue',
    'R8_LowPayoff', 'R9_UnconvincingRelationship', 'R10_UnderdevelopedConcept',
    'R11_LowSubstance', 'R12_PoorCohesion', 'R13_EmptyIntensity',
    'R14_LowFantasyPayoff', 'R15_FlatExecution',
]

# Human-readable labels for surface-level narrative
TROPE_LABELS = {
    'T_Found_Family': 'found family', 'T_Grief_Loss': 'grief & loss',
    'T_Morally_Grey_Protagonist': 'morally complex characters',
    'T_Heist': 'heist stories', 'T_Gods_Mythology': 'mythology',
    'T_Island_Isolated_Setting': 'isolated settings', 'T_Magic_System': 'magic systems',
    'T_Unreliable_Narrator': 'unreliable narrators', 'T_Redemption_Arc': 'redemption arcs',
    'T_Power_Corruption': 'power & corruption', 'T_Quest_Journey': 'epic quests',
    'T_Twist_Ending': 'twist endings', 'T_Hidden_World': 'hidden worlds',
    'T_Trauma_Recovery': 'trauma & recovery', 'T_Anti_Hero': 'antiheroes',
}

VIBE_LABELS = {
    'V1_Speculative': 'speculative fiction', 'V2_Dark': 'dark & unsettling',
    'V3_Romantic': 'romantic', 'V4_PlotDriven': 'plot-driven',
    'V5_Atmospheric': 'atmospheric', 'V6_ShortOrStandalone': 'standalones',
}

REWARD_LABELS = {
    'P1_Distinctive': 'originality', 'P2_Propulsive': 'propulsive reads',
    'P3_Emotional': 'emotional depth', 'P4_Clever': 'clever construction',
    'P5_Structure': 'unconventional structure', 'P6_Voice': 'strong narrative voice',
    'P7_Satisfying': 'satisfying payoffs',
}

RISK_LABELS = {
    'R1_Slow': 'slow pacing', 'R2_Repetitive': 'repetitive sequels',
    'R3a_CharacterDisconnect': 'characters that don\'t connect',
    'R3b_VibeClash': 'tone mismatches',
    'R4_HighConcept': 'high concept with weak execution',
    'R5_Dense': 'dense or difficult prose',
    'R6_WeakWriting': 'weak writing',
    'R7_SeriesFatigue': 'series fatigue',
}

DEFAULT_RISK_WEIGHTS = {
    'R1_Slow': 0.09, 'R2_Repetitive': 0.11, 'R3a_CharacterDisconnect': 0.07,
    'R3b_VibeClash': 0.07, 'R4_HighConcept': 0.13, 'R5_Dense': 0.07,
    'R6_WeakWriting': 0.23, 'R7_SeriesFatigue': 0.12,
    'R8_LowPayoff': 0.00, 'R9_UnconvincingRelationship': 0.00,
    'R10_UnderdevelopedConcept': 0.00, 'R11_LowSubstance': 0.00,
    'R12_PoorCohesion': 0.00, 'R13_EmptyIntensity': 0.00,
    'R14_LowFantasyPayoff': 0.00, 'R15_FlatExecution': 0.00,
}

DEFAULT_REWARD_WEIGHTS = {
    'P1_Distinctive': 0.12, 'P2_Propulsive': 0.15, 'P3_Emotional': 0.22,
    'P4_Clever': 0.10, 'P5_Structure': 0.08, 'P6_Voice': 0.10, 'P7_Satisfying': 0.23,
}

DEFAULT_COMPONENT_WEIGHTS = {'w_pred5': 0.50, 'w_author': 0.40, 'w_momentum': 0.10}


# ── SSE helpers ───────────────────────────────────────────────────────────────

def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _sse_msg(phase: str, text: str, delay_after_ms: int, progress: float | None = None) -> str:
    payload = {'phase': phase, 'text': text, 'delay_after_ms': delay_after_ms}
    if progress is not None:
        payload['progress'] = max(0.0, min(0.99, progress))
    return _sse_event('calibration_message', payload)


# ── Computation helpers ───────────────────────────────────────────────────────

def _rated_books(user_books: list[UserBook]) -> list[UserBook]:
    """Books with a numeric rating (including DNFs as 1★)."""
    result = []
    for b in user_books:
        if b.shelf == 'did-not-finish':
            result.append(b)
        elif b.shelf == 'read' and b.user_rating and b.user_rating > 0:
            result.append(b)
    return result


def _effective_rating(b: UserBook) -> float:
    """DNFs count as 1★, otherwise use stored rating."""
    if b.shelf == 'did-not-finish':
        return 1.0
    return float(b.user_rating or 0)


def _compute_trope_lifts(
    rated: list[UserBook],
    author_avgs: dict[str, float],
    tag_dicts: dict[int, dict],
    K: int = 15,
) -> dict[str, float]:
    """Author-adjusted Bayesian shrinkage for T_ tags."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for b in rated:
        td = tag_dicts.get(b.id, {})
        if not td:
            continue
        rating = _effective_rating(b)
        author_avg = author_avgs.get(b.author, 0.0)
        delta = rating - author_avg
        for tag in TROPE_TAGS:
            if td.get(tag):
                buckets[tag].append(delta)

    lifts = {}
    for tag in TROPE_TAGS:
        vals = buckets[tag]
        n = len(vals)
        if n < 5:
            lifts[tag] = 0.0
        else:
            raw = sum(vals) / n
            lifts[tag] = round((n / (n + K)) * raw, 4)
    return lifts


def _compute_vibe_lifts(
    rated: list[UserBook],
    tag_dicts: dict[int, dict],
    K: int = 15,
) -> dict[str, float]:
    """gr_avg-adjusted Bayesian shrinkage for V_ tags."""
    all_deltas: list[float] = []
    tag_deltas: dict[str, list[float]] = defaultdict(list)

    for b in rated:
        td = tag_dicts.get(b.id, {})
        gr = b.gr_avg or 0.0
        if not gr:
            continue
        rating = _effective_rating(b)
        delta = rating - gr
        all_deltas.append(delta)
        for tag in VIBE_TAGS:
            if td.get(tag):
                tag_deltas[tag].append(delta)

    if not all_deltas:
        return {t: 0.0 for t in VIBE_TAGS}

    mean_all = sum(all_deltas) / len(all_deltas)
    lifts = {}
    for tag in VIBE_TAGS:
        vals = tag_deltas[tag]
        n = len(vals)
        if n < 5:
            lifts[tag] = 0.0
        else:
            raw = (sum(vals) / n) - mean_all
            lifts[tag] = round((n / (n + K)) * raw, 4)
    return lifts


def _compute_genre_lifts(
    rated: list[UserBook],
    author_avgs: dict[str, float],
    tag_dicts: dict[int, dict],
) -> tuple[dict[str, float], dict[str, float]]:
    """G0 (K=25) and G1 (K=20, within-G0) author-adjusted lifts."""
    # G0 lifts
    g0_buckets: dict[str, list[float]] = defaultdict(list)
    g1_buckets: dict[str, list[float]] = defaultdict(list)
    g1_parent: dict[str, str] = defaultdict(str)

    for b in rated:
        td = tag_dicts.get(b.id, {})
        if not td:
            continue
        rating = _effective_rating(b)
        author_avg = author_avgs.get(b.author, 0.0)
        delta = rating - author_avg
        g0 = td.get('G0_Genre', '')
        g1 = td.get('G1_Subgenre', '')
        if g0:
            g0_buckets[g0].append(delta)
        if g1 and g0:
            g1_buckets[g1].append(delta)
            g1_parent[g1] = g0

    all_delta_list = [d for vals in g0_buckets.values() for d in vals]
    overall_mean = sum(all_delta_list) / len(all_delta_list) if all_delta_list else 0.0

    g0_lifts: dict[str, float] = {}
    for g0, vals in g0_buckets.items():
        n = len(vals)
        raw = (sum(vals) / n) - overall_mean
        g0_lifts[g0] = round((n / (n + 25)) * raw, 4)

    # G1: compare within parent G0
    g1_lifts: dict[str, float] = {}
    for g1, vals in g1_buckets.items():
        n = len(vals)
        if n < 8:
            continue
        parent = g1_parent[g1]
        # sibling mean = mean of all G1s in same G0 (including self, acceptable for shrinkage)
        sibling_deltas = [
            d for g, sibling_vals in g1_buckets.items()
            if g1_parent[g] == parent
            for d in sibling_vals
        ]
        sibling_mean = sum(sibling_deltas) / len(sibling_deltas) if sibling_deltas else 0.0
        raw = (sum(vals) / n) - sibling_mean
        g1_lifts[g1] = round((n / (n + 20)) * raw, 4)

    return g0_lifts, g1_lifts


def _compute_rp_weights(
    rated: list[UserBook],
    author_avgs: dict[str, float],
    tag_dicts: dict[int, dict],
) -> tuple[dict[str, float], dict[str, float]]:
    """Empirical R/P weights: author-adjusted correlation, shrink sparse toward 0."""
    reward_weights = {}
    risk_weights = {}

    for tag in REWARD_TAGS:
        present_deltas = []
        absent_deltas = []
        for b in rated:
            td = tag_dicts.get(b.id, {})
            if not td:
                continue
            rating = _effective_rating(b)
            author_avg = author_avgs.get(b.author, 0.0)
            delta = rating - author_avg
            if td.get(tag):
                present_deltas.append(delta)
            else:
                absent_deltas.append(delta)
        n = len(present_deltas)
        if n < 5:
            reward_weights[tag] = 0.0
        else:
            mean_p = sum(present_deltas) / n
            mean_a = sum(absent_deltas) / len(absent_deltas) if absent_deltas else 0.0
            raw = max(0.0, mean_p - mean_a)
            reward_weights[tag] = round((n / (n + 15)) * min(raw * 0.5, 0.30), 4)

    for tag in RISK_TAGS:
        present_deltas = []
        absent_deltas = []
        for b in rated:
            td = tag_dicts.get(b.id, {})
            if not td:
                continue
            rating = _effective_rating(b)
            author_avg = author_avgs.get(b.author, 0.0)
            delta = rating - author_avg
            if td.get(tag):
                present_deltas.append(delta)
            else:
                absent_deltas.append(delta)
        n = len(present_deltas)
        if n < 5:
            risk_weights[tag] = 0.0
        else:
            mean_p = sum(present_deltas) / n
            mean_a = sum(absent_deltas) / len(absent_deltas) if absent_deltas else 0.0
            raw = max(0.0, mean_a - mean_p)
            risk_weights[tag] = round((n / (n + 15)) * min(raw * 0.5, 0.30), 4)

    return reward_weights, risk_weights


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _fit_logistic(
    rated: list[UserBook],
    author_avgs: dict[str, float],
    author_5star: dict[str, float],
) -> dict | None:
    """Fit a simple logistic regression for pred5 using gradient descent."""
    examples = []
    for b in rated:
        if b.gr_avg is None:
            continue
        rating = _effective_rating(b)
        author_avg = author_avgs.get(b.author, 0.0)
        rate_5 = author_5star.get(b.author, 0.0)
        author_known = 1.0 if b.author in author_avgs else 0.0
        y = 1.0 if rating >= 5.0 else 0.0
        examples.append({
            'y': y,
            'gr_norm': max(0.0, min(1.0, (b.gr_avg - 2.5) / 2.5)),
            'author_avg_norm': author_avg / 5.0,
            'author_5star': rate_5,
            'author_known': author_known,
        })

    if len(examples) < 30:
        return None

    # Feature vector: [bias, gr_norm, author_avg_norm, author_5star, author_known]
    w = [0.0, 0.5, 0.5, 0.5, 0.2]
    lr = 0.1
    for _ in range(500):
        grad = [0.0] * 5
        for ex in examples:
            x = [1.0, ex['gr_norm'], ex['author_avg_norm'], ex['author_5star'], ex['author_known']]
            z = sum(w[i] * x[i] for i in range(5))
            pred = _sigmoid(z)
            err = pred - ex['y']
            for i in range(5):
                grad[i] += err * x[i]
        n = len(examples)
        for i in range(5):
            w[i] -= lr * grad[i] / n

    return {
        'intercept': round(w[0], 4),
        'gr_norm': round(w[1], 4),
        'author_avg_norm': round(w[2], 4),
        'author_5star': round(w[3], 4),
        'author_known': round(w[4], 4),
    }


def _build_author_maps(user_books: list[UserBook]) -> tuple[dict, dict, dict]:
    """Build {author: avg_rating}, {author: rate_5star} from rated books."""
    bucket: dict[str, list[float]] = defaultdict(list)
    for b in user_books:
        if b.shelf == 'read' and b.user_rating and b.user_rating > 0:
            bucket[b.author].append(float(b.user_rating))

    author_avgs: dict[str, float] = {}
    author_5star: dict[str, float] = {}
    author_books_count: dict[str, int] = {}
    for author, ratings in bucket.items():
        n = len(ratings)
        author_avgs[author] = round(sum(ratings) / n, 3)
        author_5star[author] = round(sum(1 for r in ratings if r >= 5.0) / n, 3)
        author_books_count[author] = n

    return author_avgs, author_5star, author_books_count


def _tag_book_with_claude(
    title: str,
    author: str,
    description: str,
    gr_avg: float,
    publication_year: int | None,
) -> dict:
    """Call Claude to generate all R/P/T/V/G tags + critical_reception."""
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return {}

    # All tags in one flat zero-initialized template
    template = {}
    for t in RISK_TAGS + REWARD_TAGS + VIBE_TAGS + TROPE_TAGS:
        template[t] = 0
    template['critical_reception'] = 0
    template['G0_Genre'] = ''
    template['G1_Subgenre'] = ''

    desc_snippet = (description or '')[:800]
    year_note = f" (published {publication_year})" if publication_year else ""
    gr_note = f" Goodreads avg: {gr_avg:.2f}" if gr_avg else ""

    prompt = f"""Tag this book for a personalized recommendation algorithm. Return JSON only — no preamble.

Book: "{title}" by {author}{year_note}.{gr_note}
{f'Description: {desc_snippet}' if desc_snippet else ''}

Use the template below. Set 0/1 for binary tags, integer 0–3 for critical_reception, string for G0/G1.
Only tag what you have genuine evidence for. Default 0 when uncertain.

Return this JSON filled in:
{json.dumps(template, indent=2)}"""

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=2048,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = msg.content[0].text.strip()
        start = raw.find('{')
        end = raw.rfind('}') + 1
        if start == -1 or end == 0:
            return {}
        return json.loads(raw[start:end])
    except Exception as e:
        print(f'[CALIBRATE] Tag error for {title}: {e}')
        return {}


def _fetch_metadata(title: str, isbn: str | None):
    """Fetch gr_avg, description, cover, year from Google Books / Open Library."""
    # Import here to avoid circular import with main.py
    try:
        from main import ol_search
        return ol_search(title=title, isbn=isbn)
    except Exception:
        return None


def _top_signals(
    trope_lifts: dict[str, float],
    vibe_lifts: dict[str, float],
    reward_weights: dict[str, float],
    n: int = 3,
) -> list[str]:
    """Return human-readable labels for the top positive taste signals."""
    signals = []
    for tag, lift in sorted(trope_lifts.items(), key=lambda x: -x[1]):
        if lift >= 0.05 and tag in TROPE_LABELS:
            signals.append(TROPE_LABELS[tag])
    for tag, lift in sorted(vibe_lifts.items(), key=lambda x: -x[1]):
        if lift >= 0.03 and tag in VIBE_LABELS:
            signals.append(VIBE_LABELS[tag])
    for tag, w in sorted(reward_weights.items(), key=lambda x: -x[1]):
        if w >= 0.10 and tag in REWARD_LABELS:
            signals.append(REWARD_LABELS[tag])
    return list(dict.fromkeys(signals))[:n]  # deduplicate, keep first n


def _top_risks(risk_weights: dict[str, float], n: int = 3) -> list[str]:
    """Return human-readable labels for the top negative taste signals."""
    risks = []
    for tag, w in sorted(risk_weights.items(), key=lambda x: -x[1]):
        if w >= 0.05 and tag in RISK_LABELS:
            risks.append(RISK_LABELS[tag])
    return risks[:n]


def _generate_taste_headline(
    signals: list[str],
    g0_lifts: dict[str, float],
    user_name: str | None,
) -> str:
    """Generate a 1-sentence taste headline via Claude, or fall back to a template."""
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key or not signals:
        top = ', '.join(signals[:2]) if signals else 'great stories'
        return f"You read for {top}, and the books that deliver both linger longest."

    top_genres = sorted(g0_lifts.items(), key=lambda x: -x[1])[:3]
    genre_str = ', '.join(g for g, _ in top_genres) if top_genres else 'your favorite genres'
    signals_str = ', '.join(signals[:4]) if signals else 'distinctive storytelling'

    prompt = f"""Write a single evocative sentence (under 20 words) describing this reader's taste profile.
Their strongest positive signals: {signals_str}.
Their top genres: {genre_str}.

Rules: observational not flattering; specific not vague; no em dashes; no quotes; lowercase only.
Return the sentence only, nothing else."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=80,
            messages=[{'role': 'user', 'content': prompt}],
        )
        headline = msg.content[0].text.strip().rstrip('.')
        return headline
    except Exception:
        top = ', '.join(signals[:2]) if signals else 'great stories'
        return f"You read for {top}, and the books that deliver both linger longest."


# ── Phase narrative helpers ───────────────────────────────────────────────────

def _pattern_messages(
    trope_lifts: dict[str, float],
    vibe_lifts: dict[str, float],
    reward_weights: dict[str, float],
    n_rated: int,
) -> list[str]:
    """Return 2–3 narrative messages about detected taste patterns."""
    msgs = []

    # Top positive vibe
    top_vibe = sorted(vibe_lifts.items(), key=lambda x: -x[1])
    for tag, lift in top_vibe:
        if lift >= 0.03 and tag in VIBE_LABELS:
            msgs.append(f"You consistently outperform your own average on {VIBE_LABELS[tag]}.")
            break

    # Top positive trope
    top_trope = sorted(trope_lifts.items(), key=lambda x: -x[1])
    for tag, lift in top_trope:
        if lift >= 0.05 and tag in TROPE_LABELS:
            msgs.append(f"Strong signal: {TROPE_LABELS[tag]} reliably earns an extra half-star from you.")
            break

    # Reward tag observation
    top_reward = sorted(reward_weights.items(), key=lambda x: -x[1])
    for tag, w in top_reward:
        if w >= 0.12 and tag in REWARD_LABELS:
            msgs.append(f"{REWARD_LABELS[tag].capitalize()} is your highest-weighted reward signal.")
            break

    if not msgs:
        msgs.append("Taste patterns detected across your rated library.")

    return msgs[:3]


def _risk_message(risk_weights: dict[str, float], n_rated: int) -> str:
    """Return a narrative message about the top risk signal."""
    top = sorted(risk_weights.items(), key=lambda x: -x[1])
    for tag, w in top:
        if w >= 0.05 and tag in RISK_LABELS:
            return f"You tend to bounce off books with {RISK_LABELS[tag]}."
    return "Analyzing your lower-rated reads for patterns to avoid."


def _tagging_messages(pct_done: float, strong_signal: str | None) -> list[str]:
    """Return narrative progress messages during the tagging phase."""
    msgs = ["Noting your taste patterns…"]
    if strong_signal:
        msgs.append(f"Finding the books that left a mark…")
    else:
        msgs.append("Looking for repeat favorite authors…")
    msgs.append("Checking what you loved and what lost you…")
    return msgs


# ── Main streaming pipeline ───────────────────────────────────────────────────

def _stream_calibration(user: User, db: Session, mode: str):
    """Generator: runs the full algorithm pipeline, yields SSE strings."""
    try:
        recalibrate = (mode == 'recalibrate')

        # ── Phase 1: Counting ──────────────────────────────────────────────────
        user_books = db.query(UserBook).filter(UserBook.user_id == user.id).all()
        n_books = len(user_books)
        read_books = [b for b in user_books if b.shelf == 'read']
        n_read = len(read_books)

        if recalibrate:
            yield _sse_msg('counting', "Reading your library…", 1200, 0.08)
        else:
            yield _sse_msg('counting', "Reading your library…", 1200, 0.08)

        # ── Phase 2: Validation ────────────────────────────────────────────────
        author_avgs, author_5star, author_books_count = _build_author_maps(user_books)
        loved_author_count = sum(
            1 for b in read_books
            if b.user_rating and b.user_rating > 0
            and author_avgs.get(b.author, 0.0) >= 4.5
        )
        loved_authors = {a for a, avg in author_avgs.items() if avg >= 4.5}
        n_loved_books = sum(
            1 for b in read_books
            if b.shelf == 'read' and b.author in loved_authors
        )

        yield _sse_msg('validation', "Looking for repeat favorite authors…", 1200, 0.14)

        # ── Phase 3: Author callouts ───────────────────────────────────────────
        # Build AuthorProfile rows
        db.query(AuthorProfile).filter(AuthorProfile.user_id == user.id).delete()
        db.flush()
        for author, ratings in defaultdict(
            list,
            {b.author: [] for b in read_books if b.user_rating and b.user_rating > 0}
        ).items():
            pass  # handled below

        author_bucket: dict[str, dict] = defaultdict(lambda: {'ratings': [], 'years': []})
        for b in user_books:
            if b.shelf == 'read' and b.user_rating and b.user_rating > 0:
                author_bucket[b.author]['ratings'].append(float(b.user_rating))
                if b.date_read:
                    author_bucket[b.author]['years'].append(b.date_read)

        for author, data in author_bucket.items():
            r = data['ratings']
            if not r:
                continue
            db.add(AuthorProfile(
                user_id=user.id,
                author_name=author,
                books_read=len(r),
                avg_rating=round(sum(r) / len(r), 3),
                best_rating=int(max(r)),
                rate_4plus=round(sum(1 for x in r if x >= 4) / len(r), 3),
                rate_5star=round(sum(1 for x in r if x >= 5) / len(r), 3),
                most_recent_year_read=max(data['years']) if data['years'] else None,
            ))
        db.flush()

        # Top author by book count (rated)
        top_author = max(author_books_count.items(), key=lambda x: x[1], default=('', 0))
        if top_author[0]:
            yield _sse_msg('authors', "Finding the books that left a mark…", 1500, 0.20)
        else:
            yield _sse_msg('authors', "Walking the shelves of your library…", 1500, 0.20)

        # ── Phase 4: Tagging ───────────────────────────────────────────────────
        # Collect rated books (the ones we need tags for)
        rated = _rated_books(user_books)
        n_rated = len(rated)

        # Emit first tagging narrative message
        tagging_msgs = _tagging_messages(0.0, None)
        yield _sse_msg('tagging', tagging_msgs[0], 1500, 0.24)

        # Tag each book — cache check first, then Claude if needed
        tag_dicts: dict[int, dict] = {}  # book.id -> tag dict
        progress_msgs = [
            "Noting your taste patterns…",
            "Looking for repeat favorite authors…",
            "Checking what you loved and what lost you…",
            "Walking the shelves of your library…",
            "Illuminating the aisles of the stacks…",
            "Finding the books that left a mark…",
            "Listening for your quiet preferences…",
            "Letting your library come into focus…",
        ]
        last_progress_emit = time.monotonic()
        progress_msg_idx = 0

        for i, b in enumerate(rated):
            # Check book_tags cache
            cached = None
            if b.isbn:
                cached = (
                    db.query(BookTags)
                    .filter(BookTags.isbn == b.isbn)
                    .order_by(BookTags.tagged_at.desc())
                    .first()
                )
            if not cached:
                cached = (
                    db.query(BookTags)
                    .filter(
                        BookTags.title == b.title,
                        BookTags.author == b.author,
                    )
                    .order_by(BookTags.tagged_at.desc())
                    .first()
                )

            if cached and cached.tags:
                try:
                    tag_dicts[b.id] = json.loads(cached.tags)
                    if b.gr_avg is None and cached.cover_url:
                        pass  # gr_avg not stored in BookTags — skip
                except Exception:
                    pass

            if b.id not in tag_dicts:
                # Fetch metadata if gr_avg missing
                meta = None
                if b.gr_avg is None:
                    meta = _fetch_metadata(b.title, b.isbn)
                    if meta and meta.get('gr_avg'):
                        b.gr_avg = meta['gr_avg']
                        db.flush()

                description = ''
                pub_year = None
                cover_url = ''
                if meta:
                    description = meta.get('description', '') or ''
                    pub_year = meta.get('publication_year') or meta.get('year')
                    cover_url = meta.get('cover_url', '') or ''

                tags = _tag_book_with_claude(b.title, b.author, description, b.gr_avg or 3.5, pub_year)
                if tags:
                    tag_dicts[b.id] = tags
                    bt = BookTags(
                        isbn=b.isbn,
                        title=b.title,
                        author=b.author,
                        tags=json.dumps(tags),
                        critical_reception=tags.get('critical_reception'),
                        cover_url=cover_url,
                        description=description[:2000] if description else None,
                        publication_year=pub_year,
                        model_version='claude-sonnet-4-6',
                    )
                    db.add(bt)
                    db.flush()

            pct = (i + 1) / max(n_rated, 1)
            if time.monotonic() - last_progress_emit >= 5:
                progress_msg_idx = (progress_msg_idx + 1) % len(progress_msgs)
                yield _sse_msg(
                    'tagging',
                    progress_msgs[progress_msg_idx],
                    1200,
                    0.24 + (0.44 * pct),
                )
                last_progress_emit = time.monotonic()

        yield _sse_msg('tagging', "Letting your library come into focus…", 1200, 0.68)
        db.commit()

        # ── Phase 5: Pattern detection ─────────────────────────────────────────
        tagged_pct = len(tag_dicts) / max(n_rated, 1)

        trope_lifts = _compute_trope_lifts(rated, author_avgs, tag_dicts)
        vibe_lifts = _compute_vibe_lifts(rated, tag_dicts)
        g0_lifts, g1_lifts = _compute_genre_lifts(rated, author_avgs, tag_dicts)

        pattern_msgs = _pattern_messages(trope_lifts, vibe_lifts, DEFAULT_REWARD_WEIGHTS, n_rated)
        for msg in pattern_msgs:
            yield _sse_msg('patterns', "Letting your library come into focus…", 1500, 0.74)

        # ── Phase 6: Risk awareness ────────────────────────────────────────────
        reward_weights, risk_weights = _compute_rp_weights(rated, author_avgs, tag_dicts)

        risk_msg = _risk_message(risk_weights, n_rated)
        yield _sse_msg('risks', "Listening for your quiet preferences…", 1500, 0.84)

        # ── Phase 7: pred5 + component weights ────────────────────────────────
        yield _sse_msg('building', 'Illuminating the aisles of the stacks…', 2000, 0.92)

        pred5_coefficients = None
        if n_rated >= 30:
            pred5_coefficients = _fit_logistic(rated, author_avgs, author_5star)
            if pred5_coefficients:
                # Store pred5 on each rated UserBook
                for b in rated:
                    if b.gr_avg is None:
                        continue
                    coef = pred5_coefficients
                    author_avg = author_avgs.get(b.author, 0.0)
                    rate_5 = author_5star.get(b.author, 0.0)
                    author_known = 1.0 if b.author in author_avgs else 0.0
                    gr_norm = max(0.0, min(1.0, (b.gr_avg - 2.5) / 2.5))
                    z = (
                        coef['intercept']
                        + coef['gr_norm'] * gr_norm
                        + coef['author_avg_norm'] * author_avg / 5.0
                        + coef['author_5star'] * rate_5
                        + coef['author_known'] * author_known
                    )
                    b.pred5 = round(_sigmoid(z), 4)

        # Component weights: simple regression
        component_weights = dict(DEFAULT_COMPONENT_WEIGHTS)
        if n_rated >= 30:
            # Fit w_pred5, w_author, w_momentum via least squares proxy
            # (simplified: just nudge weights based on correlation)
            pass  # Keep defaults for now; full multi-variate regression is future work

        yield _sse_msg('building', 'Almost there — finishing your reading profile.', 1500, 0.96)

        # ── Phase 8: Finalize + store ──────────────────────────────────────────
        top_signals = _top_signals(trope_lifts, vibe_lifts, reward_weights)
        top_risks_list = _top_risks(risk_weights)
        top_genres = [g for g, _ in sorted(g0_lifts.items(), key=lambda x: -x[1])[:3]]

        taste_headline = _generate_taste_headline(top_signals, g0_lifts, user.email)

        # Divergence note: does the user tend to rate above crowd?
        divergence_note = None
        author_ratings_list = list(author_avgs.values())
        book_gr_avgs = [b.gr_avg for b in rated if b.gr_avg]
        if author_ratings_list and book_gr_avgs:
            user_mean = sum(author_ratings_list) / len(author_ratings_list)
            crowd_mean = sum(book_gr_avgs) / len(book_gr_avgs)
            if user_mean - crowd_mean > 0.4:
                divergence_note = "We noticed you tend to veer away from the crowd — and love it that way."

        # Years of reading
        all_years = [b.date_read for b in user_books if b.date_read]
        years_reading = (max(all_years) - min(all_years) + 1) if len(all_years) >= 2 else 1
        books_count = n_read
        authors_count = len(author_books_count)

        # Assemble algorithm_weights blob
        taste_summary = f"{taste_headline}."
        weights_blob = {
            'component_weights': component_weights,
            'reward_weights': reward_weights if any(v > 0 for v in reward_weights.values()) else DEFAULT_REWARD_WEIGHTS,
            'risk_weights': risk_weights if any(v > 0 for v in risk_weights.values()) else DEFAULT_RISK_WEIGHTS,
            'taste_summary': taste_summary,
        }

        # Store to UserSettings
        settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
        if not settings:
            settings = UserSettings(user_id=user.id)
            db.add(settings)

        settings.algorithm_weights = json.dumps(weights_blob)
        settings.trope_lifts = json.dumps(trope_lifts)
        settings.vibe_lifts = json.dumps(vibe_lifts)
        settings.g0_genre_lifts = json.dumps(g0_lifts)
        settings.g1_genre_lifts = json.dumps(g1_lifts)
        if pred5_coefficients:
            settings.pred5_coefficients = json.dumps(pred5_coefficients)

        user.library_built = True
        db.commit()

        # ── Final event ────────────────────────────────────────────────────────
        taste_profile = {
            'headline': taste_headline,
            'years_reading': years_reading,
            'books_count': books_count,
            'authors_count': authors_count,
            'pulls_you_in': top_signals or ['great storytelling'],
            'kills_the_vibe': top_risks_list or ['books that don\'t deliver'],
            'top_genres': top_genres or [],
        }
        if divergence_note:
            taste_profile['divergence_note'] = divergence_note

        yield _sse_event('done', {'taste_profile': taste_profile})

    except Exception as e:
        print(f'[CALIBRATE] Pipeline error: {e}')
        import traceback
        traceback.print_exc()
        yield _sse_event('error', {'message': 'Calibration failed. Please try again.'})


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get('/profile')
def get_calibration_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the stored taste profile for the current user.

    Called when the SSE stream drops mid-run — client fetches the
    profile from DB rather than losing the calibration result.
    """
    if not current_user.library_built:
        from fastapi import HTTPException
        raise HTTPException(404, 'Library not yet built')

    settings = db.query(UserSettings).filter(UserSettings.user_id == current_user.id).first()
    if not settings or not settings.algorithm_weights:
        from fastapi import HTTPException
        raise HTTPException(404, 'Algorithm weights not found')

    try:
        weights = json.loads(settings.algorithm_weights)
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(500, 'Failed to parse algorithm weights')

    taste_summary = weights.get('taste_summary', '')
    user_books = db.query(UserBook).filter(UserBook.user_id == current_user.id).all()
    read_books = [b for b in user_books if b.shelf == 'read']
    author_avgs, _, author_books_count = _build_author_maps(user_books)

    all_years = [b.date_read for b in user_books if b.date_read]
    years_reading = (max(all_years) - min(all_years) + 1) if len(all_years) >= 2 else 1

    taste_profile = {
        'headline': taste_summary.rstrip('.') if taste_summary else 'Your reading profile is ready.',
        'years_reading': years_reading,
        'books_count': len(read_books),
        'authors_count': len(author_books_count),
        'pulls_you_in': ['great storytelling'],
        'kills_the_vibe': [],
        'top_genres': [],
    }

    return {'taste_profile': taste_profile}


@router.get('/stream')
def calibration_stream(
    mode: str = Query(default='initial', description="'initial' or 'recalibrate'"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SSE stream: runs the full algorithm build pipeline and emits phase messages.

    Consumed by the frontend via fetch + ReadableStream (not EventSource —
    JWT must be passed in the Authorization header).
    """
    user_books = db.query(UserBook).filter(UserBook.user_id == current_user.id).first()
    if not user_books:
        from fastapi import HTTPException
        raise HTTPException(400, 'No books found — upload a CSV first')

    def event_stream():
        yield from _stream_calibration(current_user, db, mode)

    return StreamingResponse(
        event_stream(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )
