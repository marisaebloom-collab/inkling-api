"""upload.py — Library upload and algorithm calibration router.

Endpoints:
  POST /library/validate          — Pre-check file before upload; detect columns.
  POST /library/upload            — Accept Goodreads CSV/xlsx, store in UserBook.
  GET  /library/calibrate-stream  — SSE: run calibration and stream progress messages.
  POST /library/calibrate         — Blocking calibration (retained as fallback).
  POST /library/dev-reset         — Dev-only: wipe library back to new-user state.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

import anthropic
import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from auth import ALGORITHM, SECRET_KEY
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


# ── Column detection ──────────────────────────────────────────────────────────

# Canonical field → accepted column name variants (case-insensitive, _ == space)
COLUMN_ALIASES: dict[str, list[str]] = {
    'Title': [
        'title', 'book title', 'book_title', 'name', 'book name',
    ],
    'Author': [
        'author', 'author name', 'author_name', 'writer', 'written by', 'by',
        'author l-f',  # Goodreads "Last, First" variant
    ],
    'Exclusive Shelf': [
        'exclusive shelf', 'exclusive_shelf', 'shelf', 'status',
        'reading status', 'read status', 'shelf name', 'bookshelves',
    ],
    'My Rating': [
        'my rating', 'my_rating', 'rating', 'stars', 'user rating',
        'user_rating', 'score', 'my stars',
    ],
    'Date Read': [
        'date read', 'date_read', 'finished', 'date finished',
        'completed', 'finish date', 'read date',
    ],
    'ISBN': [
        'isbn', 'isbn13', 'isbn_13', 'isbn 13', 'barcode', 'ean',
    ],
    'Average Rating': [
        'average rating', 'average_rating', 'avg rating', 'avg_rating',
        'goodreads average', 'goodreads avg', 'community rating',
    ],
}

REQUIRED_FIELDS  = ['Title', 'Author', 'My Rating']
OPTIONAL_FIELDS  = ['Exclusive Shelf', 'Date Read', 'ISBN', 'Average Rating']
GOODREADS_EXACT  = {'Title', 'Author', 'Exclusive Shelf', 'My Rating', 'Date Read',
                    'ISBN', 'ISBN13', 'Author l-f'}
READ_STATUS_VALUES = {'read', 'finished', 'complete', 'completed', 'done'}
DNF_STATUS_VALUES = {'did-not-finish', 'did not finish', 'dnf', 'abandoned'}


def _normalize_col(name: str) -> str:
    """Lowercase, strip whitespace, collapse underscores → spaces."""
    return name.strip().lower().replace('_', ' ')


def _detect_columns(columns: list[str]) -> tuple[dict[str, str], list[dict]]:
    """Map file columns to canonical field names.

    Returns:
        column_map  — { canonical_field: actual_column_name_in_file }
        ambiguous   — list of { field, candidates, best_guess } for fields that
                      had multiple matches or only a weak match.
    """
    col_set = set(columns)

    # Fast path: standard Goodreads export has exact canonical names.
    if {'Title', 'Author', 'Exclusive Shelf', 'My Rating'}.issubset(col_set):
        column_map: dict[str, str] = {}
        for field in REQUIRED_FIELDS + OPTIONAL_FIELDS:
            if field in col_set:
                column_map[field] = field
        # Also check ISBN13 (Goodreads variant)
        if 'ISBN13' in col_set and 'ISBN' not in column_map:
            column_map['ISBN'] = 'ISBN13'
        if 'Author l-f' in col_set and 'Author' not in column_map:
            column_map['Author'] = 'Author l-f'
        return column_map, []

    # Fuzzy path: normalize and match against alias lists.
    normalized: dict[str, str] = {_normalize_col(c): c for c in columns}
    column_map = {}
    ambiguous  = []

    for canonical, aliases in COLUMN_ALIASES.items():
        matches = [normalized[a] for a in aliases if a in normalized]
        if len(matches) == 1:
            column_map[canonical] = matches[0]
        elif len(matches) > 1:
            ambiguous.append({
                'field':      canonical,
                'candidates': matches,
                'best_guess': matches[0],
            })
        # len 0: no match — will surface as missing required field if applicable

    return column_map, ambiguous


# ── File parsing ──────────────────────────────────────────────────────────────

def _read_file_to_df(content: bytes, filename: str) -> pd.DataFrame:
    """Parse CSV or xlsx file bytes into a DataFrame."""
    fn = filename.lower()
    if fn.endswith('.csv'):
        try:
            return pd.read_csv(io.BytesIO(content), encoding='utf-8-sig', dtype=str,
                               keep_default_na=False)
        except UnicodeDecodeError:
            return pd.read_csv(io.BytesIO(content), encoding='latin-1', dtype=str,
                               keep_default_na=False)
    elif fn.endswith(('.xlsx', '.xls')):
        return pd.read_excel(io.BytesIO(content), dtype=str)
    else:
        raise ValueError(f'Unsupported file type: {filename}')


def _df_to_books(df: pd.DataFrame, column_map: dict[str, str]) -> list[dict]:
    """Convert a DataFrame into normalised book dicts using the column map."""
    books = []
    for _, row in df.iterrows():
        title      = str(row.get(column_map.get('Title', 'Title'), '') or '').strip()
        author_raw = str(row.get(column_map.get('Author', 'Author'), '') or '').strip()
        author     = _normalize_author(author_raw)
        if not title or not author:
            continue

        shelf_col = column_map.get('Exclusive Shelf')
        raw_shelf = row.get(shelf_col, '') if shelf_col else ''
        shelf = _normalize_shelf_value(raw_shelf, default='read' if not shelf_col else '')

        try:
            rating = float(row.get(column_map.get('My Rating', 'My Rating'), 0) or 0)
        except (ValueError, TypeError):
            rating = 0.0

        try:
            gr_avg = float(row.get(column_map.get('Average Rating', 'Average Rating'), 0) or 0)
        except (ValueError, TypeError):
            gr_avg = 0.0

        isbn_col  = column_map.get('ISBN', 'ISBN')
        date_col  = column_map.get('Date Read', 'Date Read')

        isbn = str(row.get(isbn_col, '') or '').strip().strip('="')
        isbn = isbn or None

        date_read = _parse_year(str(row.get(date_col, '') or ''))

        books.append({
            'title':       title,
            'author':      author,
            'user_rating': rating if rating > 0 else None,
            'date_read':   date_read,
            'isbn':        isbn,
            'shelf':       shelf,
            'gr_avg':      gr_avg if gr_avg > 0 else None,
        })
    return books


# ── CSV parsing (legacy path — kept for /upload fallback) ────────────────────

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


def _normalize_shelf_value(value: str, default: str = '') -> str:
    shelf = str(value or '').strip().lower()
    if not shelf:
        return default
    shelf = shelf.replace('_', '-')
    if shelf in READ_STATUS_VALUES:
        return 'read'
    if shelf in DNF_STATUS_VALUES:
        return 'did-not-finish'
    return shelf


def _count_usable_rows(df: pd.DataFrame, column_map: dict[str, str]) -> tuple[int, int]:
    shelf_col = column_map.get('Exclusive Shelf')
    rating_col = column_map.get('My Rating')
    read_count = 0
    rated_count = 0

    for _, row in df.iterrows():
        shelf = _normalize_shelf_value(
            row.get(shelf_col, '') if shelf_col else '',
            default='read' if not shelf_col else ''
        )
        try:
            rating = float(row.get(rating_col, 0) or 0)
        except (ValueError, TypeError):
            rating = 0.0

        if shelf == 'read':
            read_count += 1
        if shelf in {'read', 'did-not-finish'} and rating > 0:
            rated_count += 1

    return read_count, rated_count


def parse_goodreads_csv(content: bytes) -> list[dict]:
    """Parse a Goodreads export CSV into a list of normalised book dicts."""
    text   = content.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    books  = []

    for row in reader:
        title  = (row.get('Title') or '').strip()
        author = _normalize_author(row.get('Author') or row.get('Author l-f') or '')
        if not title or not author:
            continue

        shelf = _normalize_shelf_value(row.get('Exclusive Shelf') or '', default='read')
        try:
            rating = float((row.get('My Rating') or '0').strip())
        except ValueError:
            rating = 0.0

        isbn = (
            (row.get('ISBN13') or row.get('ISBN') or '')
            .strip()
            .strip('="')
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
        model      = 'claude-opus-4-5',
        max_tokens = 2048,
        messages   = [{'role': 'user', 'content': prompt}],
    )

    raw   = message.content[0].text.strip()
    start = raw.find('{')
    end   = raw.rfind('}') + 1
    if start == -1 or end == 0:
        raise ValueError('Calibration response contained no JSON')

    weights = json.loads(raw[start:end])

    for key in ('component_weights', 'reward_weights', 'risk_weights', 'taste_summary'):
        if key not in weights:
            raise ValueError(f'Calibration response missing key: {key}')

    return weights


def _store_calibration_results(user_id: int, book_dicts: list[dict],
                                weights: dict, db: Session) -> list[dict]:
    """Write AuthorProfile rows and UserSettings.algorithm_weights; set library_built."""
    author_rows = _aggregate_authors(book_dicts)

    db.query(AuthorProfile).filter(AuthorProfile.user_id == user_id).delete()
    db.flush()
    for a in author_rows:
        db.add(AuthorProfile(
            user_id               = user_id,
            author_name           = a['author_name'],
            books_read            = a['books_read'],
            avg_rating            = a['avg_rating'],
            best_rating           = a['best_rating'],
            rate_4plus            = a['rate_4plus'],
            rate_5star            = a['rate_5star'],
            most_recent_year_read = a['most_recent_year_read'],
        ))

    settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if not settings:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
    settings.algorithm_weights = json.dumps(weights)

    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.library_built = True

    db.commit()
    return author_rows


# ── SSE helper ────────────────────────────────────────────────────────────────

def _sse(data: str, event: str | None = None) -> str:
    if event:
        return f'event: {event}\ndata: {data}\n\n'
    return f'data: {data}\n\n'


async def _calibrate_stream_generator(user_id: int, db: Session):
    """Async generator that streams calibration progress as SSE events."""

    # ── Load books ────────────────────────────────────────────────────────────
    user_books = db.query(UserBook).filter(UserBook.user_id == user_id).all()
    if not user_books:
        yield _sse(json.dumps({'error': 'No books found — upload first'}), event='error')
        return

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

    read_books = [b for b in book_dicts if b.get('shelf') == 'read']
    read_count = len(read_books)

    # ── Pre-calibration messages (real data) ──────────────────────────────────
    yield _sse('Reading your library…')
    await asyncio.sleep(1.0)

    author_rows = _aggregate_authors(book_dicts)
    loved_count = sum(1 for a in author_rows if a['avg_rating'] >= 4.0)

    yield _sse('Looking for repeat favorite authors…')
    await asyncio.sleep(1.0)

    yield _sse('Looking for repeat favorite authors…')
    await asyncio.sleep(0.7)

    top_authors = sorted(author_rows, key=lambda a: a['books_read'], reverse=True)[:2]
    for a in top_authors:
        yield _sse('Finding the books that left a mark…')
        await asyncio.sleep(1.2)

    yield _sse('Noting your taste patterns…')
    await asyncio.sleep(0.8)

    yield _sse('Checking what you loved and what lost you…')
    await asyncio.sleep(0.8)

    # ── Start Claude calibration in background thread ─────────────────────────
    try:
        calib_task = asyncio.create_task(
            asyncio.to_thread(_run_calibration, book_dicts)
        )
    except ValueError as e:
        yield _sse(json.dumps({'error': str(e)}), event='error')
        return

    # Interleave synthetic messages while Claude thinks
    mid_messages = [
        'Walking the shelves of your library…',
        'Illuminating the aisles of the stacks…',
        'Almost there — finishing your reading profile',
    ]
    for msg in mid_messages:
        if calib_task.done():
            break
        yield _sse(msg)
        await asyncio.sleep(2.5)

    # ── Await calibration result ──────────────────────────────────────────────
    try:
        weights = await calib_task
    except ValueError as e:
        yield _sse(json.dumps({'error': str(e)}), event='error')
        return
    except Exception as e:
        yield _sse(json.dumps({'error': f'Calibration failed: {e}'}), event='error')
        return

    if not calib_task.done() or 'Almost there' not in mid_messages[-1]:
        # Ensure completion message shown if loop exited early
        yield _sse('Almost there — finishing your reading profile')
        await asyncio.sleep(0.8)

    # ── Store results to DB ───────────────────────────────────────────────────
    try:
        _store_calibration_results(user_id, book_dicts, weights, db)
    except Exception as e:
        yield _sse(json.dumps({'error': f'Failed to save profile: {e}'}), event='error')
        return

    yield _sse('Your reading profile is ready')
    await asyncio.sleep(0.5)

    taste = weights.get('taste_summary', '')
    yield _sse(json.dumps({'taste_summary': taste}), event='done')


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post('/dev-reset', include_in_schema=False)
def dev_reset_library(
    current_user: User    = Depends(__import__('auth').get_current_user),
    db:           Session = Depends(get_db),
):
    """Dev-only: wipe this user's library and algorithm back to new-user state."""
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


@router.post('/validate')
async def validate_library(file: UploadFile = File(...)):
    """Pre-check a file before upload. Returns validation result + column detection.

    Does NOT require auth — called before the user has confirmed anything.
    Does NOT store any data.
    """
    filename = (file.filename or '').lower()

    # 1. File type check
    if not any(filename.endswith(ext) for ext in ('.csv', '.xlsx', '.xls')):
        return {
            'valid':      False,
            'error_code': 'wrong_file_type',
            'read_count': 0,
        }

    content = await file.read()

    # 2. Readability check
    try:
        df = _read_file_to_df(content, filename)
    except Exception:
        return {
            'valid':      False,
            'error_code': 'unreadable',
            'read_count': 0,
        }

    # 3. Book data present
    if len(df) == 0:
        return {
            'valid':      False,
            'error_code': 'no_book_data',
            'read_count': 0,
        }

    # 4. Column detection
    column_map, ambiguous = _detect_columns(list(df.columns))

    missing_required = [
        f for f in REQUIRED_FIELDS
        if f not in column_map and not any(a['field'] == f for a in ambiguous)
    ]
    if missing_required:
        return {
            'valid':      False,
            'error_code': 'missing_columns',
            'missing':    missing_required,
            'required':   REQUIRED_FIELDS,
            'columns':    list(df.columns),
            'read_count': 0,
            'rated_count': 0,
        }

    # 5. Usable rated books present
    read_count, rated_count = _count_usable_rows(df, column_map)

    if rated_count == 0:
        return {
            'valid':      False,
            'error_code': 'no_rated_books',
            'read_count': 0,
            'rated_count': 0,
        }

    return {
        'valid':      True,
        'read_count': read_count,
        'rated_count': rated_count,
        'assumed_all_read': 'Exclusive Shelf' not in column_map,
        'column_map': column_map,
        'ambiguous':  ambiguous,
    }


@router.post('/upload', status_code=202)
async def upload_library(
    file:         UploadFile = File(...),
    current_user: User       = Depends(__import__('auth').get_current_user),
    db:           Session    = Depends(get_db),
):
    """Accept a Goodreads export CSV/xlsx and store all books in UserBook.

    Does NOT run calibration — open /library/calibrate-stream next.
    Returns counts so the UI can confirm what was received.
    """
    filename = (file.filename or '').lower()

    if not any(filename.endswith(ext) for ext in ('.csv', '.xlsx', '.xls')):
        raise HTTPException(400, 'File must be a .csv, .xlsx, or .xls export')

    content = await file.read()

    try:
        df = _read_file_to_df(content, filename)
    except Exception as e:
        raise HTTPException(400, f'Could not read file: {e}')

    if len(df) == 0:
        raise HTTPException(400, 'No books found in file — check the file format')

    column_map, ambiguous = _detect_columns(list(df.columns))

    # If required columns are still ambiguous after auto-detection, we can't proceed.
    missing_required = [
        f for f in REQUIRED_FIELDS
        if f not in column_map and not any(a['field'] == f for a in ambiguous)
    ]
    if missing_required:
        raise HTTPException(
            400,
            f'Could not find required columns: {", ".join(missing_required)}. '
            'Required fields are Title, Author, and Rating.'
        )

    # Use best-guess for ambiguous columns (user already confirmed via /validate flow).
    for item in ambiguous:
        if item['field'] not in column_map:
            column_map[item['field']] = item['best_guess']

    books = _df_to_books(df, column_map)

    if not books:
        raise HTTPException(400, 'No valid book records found — check the file format')

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
            gr_avg      = b.get('gr_avg'),
        ))

    db.commit()

    read_count  = sum(1 for b in books if b['shelf'] == 'read')
    rated_count = sum(1 for b in books if b['shelf'] in {'read', 'did-not-finish'} and b['user_rating'])

    return {
        'ok':          True,
        'total_books': len(books),
        'read':        read_count,
        'rated':       rated_count,
        'next':        'GET /library/calibrate-stream',
    }


@router.get('/calibrate-stream')
async def calibrate_stream(
    token: str     = Query(..., description='JWT access token (EventSource cannot set headers)'),
    db:    Session = Depends(get_db),
):
    """Stream calibration progress as Server-Sent Events.

    EventSource (used by the client) cannot set Authorization headers, so the
    JWT is passed as a query parameter instead.
    """
    # Validate token manually (can't use get_current_user Depends here)
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get('sub', 0))
    except (JWTError, ValueError):
        raise HTTPException(401, 'Invalid or expired token')

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(401, 'User not found')

    return StreamingResponse(
        _calibrate_stream_generator(user_id, db),
        media_type='text/event-stream',
        headers={
            'Cache-Control':               'no-cache',
            'X-Accel-Buffering':           'no',   # disable nginx buffering on Railway
            'Access-Control-Allow-Origin': '*',
        },
    )


@router.post('/calibrate')
def calibrate_library(
    current_user: User    = Depends(__import__('auth').get_current_user),
    db:           Session = Depends(get_db),
):
    """Blocking calibration endpoint — retained as a fallback.

    Prefer GET /library/calibrate-stream for the real-time onboarding experience.
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

    try:
        weights = _run_calibration(book_dicts)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f'Calibration failed: {e}')

    author_rows = _store_calibration_results(current_user.id, book_dicts, weights, db)

    return {
        'ok':              True,
        'authors_indexed': len(author_rows),
        'taste_summary':   weights.get('taste_summary', ''),
        'weights_stored':  True,
    }
