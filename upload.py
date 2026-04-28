"""upload.py — Library upload and algorithm calibration router.

Two endpoints:
  POST /library/upload    — Accept Goodreads CSV, store books in UserBook table.
  POST /library/calibrate — Analyse stored books via Claude, derive per-user
                            weights, aggregate AuthorProfile rows, flip
                            library_built = True.

The two-step split lets the client show a progress screen between upload
(fast, local) and calibration (slow, Claude API call).
"""
from __future__ import annotations

import csv
import io
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

import anthropic
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import AuthorProfile, User, UserBook, UserSettings

router = APIRouter(prefix='/library', tags=['library'])

# ── Tag catalogue ─────────────────────────────────────────────────────────────
# Mirrors weights.py — keep in sync if tags change.

REWARD_TAGS = [
    'P1_Distinctive', 'P2_Propulsive', 'P3_Emotional', 'P4_Clever',
    'P5_Structure',   'P6_Voice',      'P7_Satisfying',
]

RISK_TAGS = [
    'R1_Slow',    'R2_Repetitive',      'R3_VibeClash',  'R4_HighConcept',
    'R5_InaccessibleProse', 'R6_WeakWriting', 'R7_SeriesFatigue',
    'R8_TooLong', 'R9_ContentWarnings', 'R10_TranslationQuality', 'R11_DatedContent',
]

_REWARD_DESC = {
    'P1_Distinctive': 'Feels genuinely original — unlike most books in its genre',
    'P2_Propulsive':  'Hard to put down, compulsive reading experience',
    'P3_Emotional':   'Creates lasting emotional impact',
    'P4_Clever':      'Smart ideas or structure that feel earned',
    'P5_Structure':   'Unconventional structure that enhances the story',
    'P6_Voice':       'Distinct, singular narrative voice',
    'P7_Satisfying':  'Delivers a payoff that feels earned and worth the buildup',
}

_RISK_DESC = {
    'R1_Slow':                'Slow pacing that drags without payoff',
    'R2_Repetitive':          'Retreads familiar ground from earlier entries',
    'R3_VibeClash':           'Tone or characters don\'t connect with the reader',
    'R4_HighConcept':         'Ambitious premise with uneven execution',
    'R5_InaccessibleProse':   'Writing feels difficult to engage with or slows you down',
    'R6_WeakWriting':         'Flat prose or dialogue',
    'R7_SeriesFatigue':       'Quality decline in later series entries',
    'R8_TooLong':             'Notably long in a way reviewers cite as a problem',
    'R9_ContentWarnings':     'Contains disturbing content — violence, trauma, explicit material',
    'R10_TranslationQuality': 'Translation noted as stilted or creating distance',
    'R11_DatedContent':       'Content or attitudes feel dated by contemporary standards',
}

# Default weights — mirrors weights.py; used as the example JSON in the prompt.
_DEFAULT_REWARD_WEIGHTS = {
    'P1_Distinctive': 0.12, 'P2_Propulsive': 0.15, 'P3_Emotional': 0.22,
    'P4_Clever':      0.10, 'P5_Structure':  0.08, 'P6_Voice':     0.10,
    'P7_Satisfying':  0.23,
}
_DEFAULT_RISK_WEIGHTS = {
    'R1_Slow':                0.09, 'R2_Repetitive':          0.11,
    'R3_VibeClash':           0.07, 'R4_HighConcept':         0.13,
    'R5_InaccessibleProse':   0.07, 'R6_WeakWriting':         0.23,
    'R7_SeriesFatigue':       0.12, 'R8_TooLong':             0.00,
    'R9_ContentWarnings':     0.00, 'R10_TranslationQuality': 0.00,
    'R11_DatedContent':       0.00,
}


# ── CSV parsing ───────────────────────────────────────────────────────────────

def _normalize_author(name: str) -> str:
    """Convert 'Last, First' → 'First Last'; leave 'First Last' unchanged."""
    name = name.strip()
    if ',' in name:
        last, first = [p.strip() for p in name.split(',', 1)]
        return f'{first} {last}'
    return name


def _parse_year(date_str: str) -> int | None:
    """Extract year from Goodreads date string (YYYY/MM/DD or YYYY-MM-DD)."""
    if not date_str:
        return None
    try:
        return int(date_str.replace('-', '/').split('/')[0])
    except (ValueError, IndexError):
        return None


def parse_goodreads_csv(content: bytes) -> list[dict]:
    """Parse a Goodreads export CSV into a list of normalised book dicts."""
    text   = content.decode('utf-8-sig')          # strip UTF-8 BOM if present
    reader = csv.DictReader(io.StringIO(text))
    books  = []

    for row in reader:
        title  = (row.get('Title') or '').strip()
        author = _normalize_author(row.get('Author') or row.get('Author l-f') or '')
        if not title or not author:
            continue

        shelf = (row.get('Exclusive Shelf') or '').strip()
        try:
            rating = float((row.get('My Rating') or '0').strip())
        except ValueError:
            rating = 0.0

        isbn = (
            (row.get('ISBN13') or row.get('ISBN') or '')
            .strip()
            .strip('="')          # Goodreads wraps ISBNs in ="..." in some exports
        )

        books.append({
            'title':       title,
            'author':      author,
            'user_rating': rating if rating > 0 else None,
            'date_read':   _parse_year(row.get('Date Read', '')),
            'isbn':        isbn or None,
            'shelf':       shelf,
        })

    return books


# ── AuthorProfile aggregation ─────────────────────────────────────────────────

def _aggregate_authors(books: list[dict]) -> list[dict]:
    """Aggregate book-level records into per-author profile dicts."""
    bucket: dict[str, dict] = defaultdict(lambda: {'ratings': [], 'years': []})

    for b in books:
        if b.get('shelf') != 'read' or not b.get('user_rating'):
            continue
        a = b['author']
        bucket[a]['ratings'].append(b['user_rating'])
        if b.get('date_read'):
            bucket[a]['years'].append(b['date_read'])

    result = []
    for author, data in bucket.items():
        r = data['ratings']
        if not r:
            continue
        result.append({
            'author_name':            author,
            'books_read':             len(r),
            'avg_rating':             round(sum(r) / len(r), 3),
            'best_rating':            int(max(r)),
            'rate_4plus':             round(sum(1 for x in r if x >= 4) / len(r), 3),
            'rate_5star':             round(sum(1 for x in r if x >= 5) / len(r), 3),
            'most_recent_year_read':  max(data['years']) if data['years'] else None,
        })
    return result


# ── Calibration ───────────────────────────────────────────────────────────────

def _get_rated_by_tier(books: list[dict]) -> tuple[dict[int, list[dict]], list[dict]]:
    """Return rated books grouped by star rating (1–5) plus a separate DNF list."""
    tiers: dict[int, list[dict]] = {1: [], 2: [], 3: [], 4: [], 5: []}
    dnfs: list[dict] = []

    for b in books:
        shelf = b.get('shelf', '')
        if shelf == 'did-not-finish':
            dnfs.append(b)
        elif shelf == 'read' and b.get('user_rating'):
            star = int(b['user_rating'])
            if star in tiers:
                tiers[star].append(b)

    return tiers, dnfs


def _build_calibration_prompt(tiers: dict[int, list[dict]], dnfs: list[dict]) -> str:
    def fmt_tier(books, label):
        if not books:
            return ''
        lines = '\n'.join(
            f'  - "{b["title"]}" by {b["author"]}'
            for b in books
        )
        return f'{label} ({len(books)} books):\n{lines}'

    sections = '\n\n'.join(filter(None, [
        fmt_tier(tiers[5], '5★ — LOVED'),
        fmt_tier(tiers[4], '4★ — Liked'),
        fmt_tier(tiers[3], '3★ — Mixed / Fine'),
        fmt_tier(tiers[2], '2★ — Disliked'),
        fmt_tier(tiers[1], '1★ — Strongly disliked'),
        fmt_tier(dnfs,     'Did Not Finish — abandoned before completing'),
    ]))

    total = sum(len(v) for v in tiers.values()) + len(dnfs)

    reward_list = '\n'.join(f'  {k}: {v}' for k, v in _REWARD_DESC.items())
    risk_list   = '\n'.join(f'  {k}: {v}' for k, v in _RISK_DESC.items())

    example = json.dumps({
        'component_weights': {'w_pred5': 0.50, 'w_author': 0.40, 'w_momentum': 0.10},
        'reward_weights':    _DEFAULT_REWARD_WEIGHTS,
        'risk_weights':      _DEFAULT_RISK_WEIGHTS,
        'taste_summary':     'Placeholder — replace with 1–2 sentence taste description.',
    }, indent=2)

    return f"""You are calibrating a personalized book recommendation algorithm for a specific reader. You have their complete rated reading history ({total} books across all rating tiers). Use your knowledge of these books to reason carefully about what qualities this reader consistently values and avoids.

COMPLETE RATED READING HISTORY:
{sections}

The 3★ books are intentionally included — they reveal what is *insufficient* for this reader, not just what they actively disliked. Did Not Finish books are strong negative signals — treat them as books that failed this reader before the halfway point.

REWARD TAGS — set higher weight if this quality strongly predicts a high rating for this reader:
{reward_list}

RISK TAGS — set higher weight if this quality strongly predicts a low rating for this reader.
Note: R8–R11 should be set to 0.00 unless this reader's history shows a clear pattern — these tags have no signal on most readers' data yet:
{risk_list}

COMPONENT WEIGHTS — how much to weight each base signal (must sum to 1.0):
  w_pred5:    predicted 5-star probability derived from Goodreads community data
  w_author:   reader's own historical ratings for this author
  w_momentum: how recently the reader has read this author

Analyse the full distribution carefully before assigning weights. Look for consistent patterns across the 5★ books, consistent failure modes across the 1–2★ and DNF books, and what separates the 4★ from the 5★. Return ONLY valid JSON matching this structure exactly:
{example}"""


def _run_calibration(books: list[dict]) -> dict:
    """Call Claude to derive per-user weights from the complete rated library."""
    tiers, dnfs = _get_rated_by_tier(books)
    total_high = len(tiers[4]) + len(tiers[5])

    if total_high < 5:
        raise ValueError(
            f'Not enough rated books to calibrate (need ≥5 books rated 4–5★, '
            f'found {total_high}). Add more ratings in Goodreads and re-export.'
        )

    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY not configured')

    prompt  = _build_calibration_prompt(tiers, dnfs)
    client  = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model      = 'claude-opus-4-5',   # Opus for calibration quality
        max_tokens = 2048,
        messages   = [{'role': 'user', 'content': prompt}],
    )

    raw   = message.content[0].text.strip()
    start = raw.find('{')
    end   = raw.rfind('}') + 1
    if start == -1 or end == 0:
        raise ValueError('Calibration response contained no JSON')

    weights = json.loads(raw[start:end])

    # Validate required top-level keys
    for key in ('component_weights', 'reward_weights', 'risk_weights', 'taste_summary'):
        if key not in weights:
            raise ValueError(f'Calibration response missing key: {key}')

    return weights


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post('/dev-reset', include_in_schema=False)
def dev_reset_library(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Dev-only: wipe this user's library and algorithm back to new-user state.

    Clears UserBook rows, AuthorProfile rows, algorithm_weights, and flips
    library_built = False. Used to re-run the onboarding flow for a test account
    without deleting the account itself.
    """
    from auth import DEV_MODE
    if not DEV_MODE:
        raise HTTPException(404, 'Not found')

    db.query(UserBook).filter(UserBook.user_id == current_user.id).delete()
    db.query(AuthorProfile).filter(AuthorProfile.user_id == current_user.id).delete()

    settings = db.query(UserSettings).filter(UserSettings.user_id == current_user.id).first()
    if settings:
        settings.algorithm_weights = None

    current_user.library_built = False
    db.commit()

    return {'ok': True, 'message': f'Library reset for {current_user.email}'}


@router.post('/upload', status_code=202)
async def upload_library(
    file:         UploadFile    = File(...),
    current_user: User          = Depends(get_current_user),
    db:           Session       = Depends(get_db),
):
    """Accept a Goodreads export CSV and store all books in UserBook.

    Does NOT run calibration — call POST /library/calibrate next.
    Returns counts so the UI can confirm what was received.
    """
    if not (file.filename or '').lower().endswith('.csv'):
        raise HTTPException(400, 'File must be a .csv Goodreads export')

    content = await file.read()
    try:
        books = parse_goodreads_csv(content)
    except Exception as e:
        raise HTTPException(400, f'Could not parse CSV: {e}')

    if not books:
        raise HTTPException(400, 'No books found in CSV — check the file format')

    # Replace any previous upload for this user
    db.query(UserBook).filter(UserBook.user_id == current_user.id).delete()
    db.flush()

    for b in books:
        db.add(UserBook(
            user_id     = current_user.id,
            title       = b['title'],
            author      = b['author'],
            user_rating = b['user_rating'],
            date_read   = b['date_read'],
            isbn        = b['isbn'],
            shelf       = b['shelf'],
        ))

    db.commit()

    read_count  = sum(1 for b in books if b['shelf'] == 'read')
    rated_count = sum(1 for b in books if b['user_rating'])

    return {
        'ok':          True,
        'total_books': len(books),
        'read':        read_count,
        'rated':       rated_count,
        'next':        'POST /library/calibrate',
    }


@router.post('/calibrate')
def calibrate_library(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Build the user's personalized algorithm from their stored books.

    Steps:
      1. Pull UserBook rows from DB
      2. Call Claude → derive per-user component + tag weights
      3. Aggregate UserBook → AuthorProfile rows
      4. Store weights on UserSettings
      5. Flip library_built = True
    """
    user_books = db.query(UserBook).filter(UserBook.user_id == current_user.id).all()
    if not user_books:
        raise HTTPException(400, 'No books found — call POST /library/upload first')

    book_dicts = [
        {
            'title':       b.title,
            'author':      b.author,
            'user_rating': b.user_rating,
            'date_read':   b.date_read,
            'shelf':       b.shelf,
        }
        for b in user_books
    ]

    # 1. Calibrate
    try:
        weights = _run_calibration(book_dicts)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f'Calibration failed: {e}')

    # 2. Aggregate → AuthorProfile
    author_rows = _aggregate_authors(book_dicts)
    db.query(AuthorProfile).filter(AuthorProfile.user_id == current_user.id).delete()
    db.flush()
    for a in author_rows:
        db.add(AuthorProfile(
            user_id               = current_user.id,
            author_name           = a['author_name'],
            books_read            = a['books_read'],
            avg_rating            = a['avg_rating'],
            best_rating           = a['best_rating'],
            rate_4plus            = a['rate_4plus'],
            rate_5star            = a['rate_5star'],
            most_recent_year_read = a['most_recent_year_read'],
        ))

    # 3. Store weights
    settings = db.query(UserSettings).filter(UserSettings.user_id == current_user.id).first()
    if not settings:
        settings = UserSettings(user_id=current_user.id)
        db.add(settings)
    settings.algorithm_weights = json.dumps(weights)

    # 4. Mark library built
    current_user.library_built = True

    db.commit()

    return {
        'ok':              True,
        'authors_indexed': len(author_rows),
        'taste_summary':   weights.get('taste_summary', ''),
        'weights_stored':  True,
    }
