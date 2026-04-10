from __future__ import annotations
# main.py — FastAPI server

import os
import requests
from functools import lru_cache
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from library import load_library, find_book, ALL_TAGS, RISK_TAGS, REWARD_TAGS, VIBE_TAGS
from score import score_book
from weights import BUCKET_DISPLAY

# ── Config ──────────────────────────────────────────────────────────────────
CSV_PATH = os.environ.get('BOOKS_CSV', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'books.csv'))
OPEN_LIBRARY_URL = 'https://openlibrary.org'

app = FastAPI(title='Inkling API', version='1.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

# ── Load library once on startup ─────────────────────────────────────────────
print(f"Loading library from {CSV_PATH}…")
BOOKS, AUTHOR_TABLE = load_library(CSV_PATH)
print(f"Loaded {len(BOOKS)} books, {len(AUTHOR_TABLE)} authors")

# ── Helpers ──────────────────────────────────────────────────────────────────

def get_author_features(author: str) -> dict:
    """Return author signals from reader's history, or unknown defaults."""
    a = AUTHOR_TABLE.get(author)
    if not a:
        return {'author_avg': 0, 'momentum': 0, 'author_known': False}
    return {
        'author_avg':    a['avg_rating'],
        'momentum':      a['momentum'],
        'author_known':  True,
        'rate_5star':    a['rate_5star'],
    }


def ol_search(title: str = None, isbn: str = None) -> dict | None:
    """Fetch book metadata from Open Library."""
    try:
        if isbn:
            url = f"{OPEN_LIBRARY_URL}/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
            r = requests.get(url, timeout=5)
            data = r.json()
            key = f"ISBN:{isbn}"
            if key in data:
                d = data[key]
                return {
                    'title':      d.get('title', ''),
                    'author':     d.get('authors', [{}])[0].get('name', ''),
                    'gr_avg':     0,
                    'pages':      d.get('number_of_pages', 0),
                    'cover_url':  d.get('cover', {}).get('large', ''),
                    'ol_key':     d.get('key', ''),
                }
        if title:
            url = f"{OPEN_LIBRARY_URL}/search.json?title={requests.utils.quote(title)}&limit=1"
            r = requests.get(url, timeout=5)
            data = r.json()
            if data.get('docs'):
                d = data['docs'][0]
                return {
                    'title':     d.get('title', ''),
                    'author':    d.get('author_name', [''])[0],
                    'gr_avg':    round(d.get('ratings_average', 0), 2),
                    'pages':     d.get('number_of_pages_median', 0),
                    'cover_url': (f"https://covers.openlibrary.org/b/id/{d['cover_i']}-L.jpg"
                                  if d.get('cover_i') else ''),
                    'ol_key':    d.get('key', ''),
                }
    except Exception as e:
        print(f"Open Library error: {e}")
    return None


def build_result(book: dict, meta: dict = None) -> dict:
    """Score a library book and format the full response."""
    tags = {tag: book.get(tag, 0) for tag in ALL_TAGS}

    scored = score_book({
        'pred5':               book['pred5'],
        'author_avg':          book['author_avg'],
        'momentum':            book['momentum'],
        'gr_avg':              book['gr_avg'],
        'critical_reception':  book['critical_reception'],
    }, tags)

    return {
        'found_in_library': True,
        'title':       book['title'],
        'author':      book['author'],
        'cover_url':   meta.get('cover_url', '') if meta else '',
        'gr_avg':      book['gr_avg'],
        'pages':       book['pages'],
        'pct_match':   scored['pct_match'],
        'verdict':     scored['verdict'],
        'master_score': scored['master_score'],
        'bucket':      scored['bucket'],
        'risk_score':  scored['risk_score'],
        'reward_score': scored['reward_score'],
        'genre':       [book['g0'], book['g1']] if book.get('g0') else [],
        'tags': {
            'risk':   {t: book[t] for t in RISK_TAGS   if book.get(t)},
            'reward': {t: book[t] for t in REWARD_TAGS if book.get(t)},
            'vibe':   {t: book[t] for t in VIBE_TAGS   if book.get(t)},
        },
        'shelf': book['shelf'],
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get('/')
def root():
    return {'status': 'ok', 'books_loaded': len(BOOKS), 'authors': len(AUTHOR_TABLE)}


@app.get('/score')
def score(
    title: str = Query(None, description='Book title'),
    isbn:  str = Query(None, description='ISBN-10 or ISBN-13'),
):
    """
    Score a book. Checks your library first; falls back to Open Library for new books.
    """
    if not title and not isbn:
        raise HTTPException(400, 'Provide title or isbn')

    # 1. Check library
    book = find_book(BOOKS, title=title, isbn=isbn)
    if book:
        meta = ol_search(title=book['title']) if not isbn else ol_search(isbn=isbn)
        return build_result(book, meta)

    # 2. Not in library — fetch from Open Library
    meta = ol_search(title=title, isbn=isbn)
    if not meta:
        raise HTTPException(404, 'Book not found in library or Open Library')

    # Build author features from reader history
    author_feats = get_author_features(meta['author'])

    # For new books we have no stored tags or pred5
    # Use approximation formula from handoff doc
    gr_norm  = max(0, min(1, (meta['gr_avg'] - 2.5) / 2.5))
    if author_feats['author_known']:
        pred5 = 0.55 * gr_norm + 0.45 * author_feats.get('rate_5star', 0)
    else:
        pred5 = gr_norm * 0.85

    # No AI tags for new books — return a basic score with a flag
    scored = score_book({
        'pred5':              pred5,
        'author_avg':         author_feats['author_avg'],
        'momentum':           author_feats['momentum'],
        'gr_avg':             meta['gr_avg'],
        'critical_reception': 0,
    }, {})  # empty tags — no AI tagging yet

    return {
        'found_in_library': False,
        'needs_tagging':    True,
        'title':            meta['title'],
        'author':           meta['author'],
        'cover_url':        meta.get('cover_url', ''),
        'gr_avg':           meta['gr_avg'],
        'pages':            meta['pages'],
        'pct_match':        scored['pct_match'],
        'verdict':          scored['verdict'],
        'master_score':     scored['master_score'],
        'bucket':           scored['bucket'],
        'note':             'Score is approximate — book not in library, no AI tags applied',
    }


@app.get('/search')
def search(q: str = Query(..., description='Title or author search')):
    """Search your library by title or author."""
    q_lower = q.lower()
    results = []
    for b in BOOKS:
        if q_lower in b['title'].lower() or q_lower in b['author'].lower():
            results.append({
                'title':        b['title'],
                'author':       b['author'],
                'shelf':        b['shelf'],
                'master_score': b['master_score'],
                'verdict':      BUCKET_DISPLAY.get(b['bucket'], b['bucket']),
                'pct_match':    round(b['master_score'] * 100),
                'g0':           b['g0'],
            })
    results.sort(key=lambda x: x['master_score'], reverse=True)
    return {'count': len(results), 'results': results[:20]}


@app.get('/book/{title}')
def book_detail(title: str):
    """Get full details for a specific book from your library."""
    book = find_book(BOOKS, title=title)
    if not book:
        raise HTTPException(404, f'"{title}" not found in library')
    return build_result(book)
