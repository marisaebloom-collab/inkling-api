from __future__ import annotations
# main.py — FastAPI server

import os
import json
import re
import urllib.parse
import requests
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from database import engine
import models  # ensures all ORM classes are registered before create_all
from auth import router as auth_router, get_current_user
from library import load_library, find_book, ALL_TAGS, RISK_TAGS, REWARD_TAGS, VIBE_TAGS
from score import score_book
from weights import BUCKET_DISPLAY

# ── Config ─────────────────────────────────────────────────────────────────────
CSV_PATH = os.environ.get(
    'BOOKS_CSV',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'books.csv')
)

app = FastAPI(title='Inkling API', version='2.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

# Create DB tables on startup (idempotent — safe to run every deploy)
models.Base.metadata.create_all(bind=engine)

# Auth router
app.include_router(auth_router)

# ── Load library once on startup ───────────────────────────────────────────────
print(f"Loading library from {CSV_PATH}…")
BOOKS, AUTHOR_TABLE = load_library(CSV_PATH)
print(f"Loaded {len(BOOKS)} books, {len(AUTHOR_TABLE)} authors")


# ── Metadata helpers ───────────────────────────────────────────────────────────

def google_books_search(query: str) -> dict | None:
    """Fetch book metadata from Google Books API."""
    api_key = os.environ.get('GOOGLE_BOOKS_API_KEY', '')
    try:
        base_url = (
            f"https://www.googleapis.com/books/v1/volumes"
            f"?q={urllib.parse.quote(query)}&maxResults=5&printType=books"
        )
        url = f"{base_url}&key={api_key}" if api_key else base_url
        data = requests.get(url, timeout=10).json()
        if data.get('error'):
            print(f"Google Books error: {data['error'].get('message')}")
            return None
        items = data.get('items') or []
        if not items:
            return None
        item = next(
            (i for i in items
             if i.get('volumeInfo', {}).get('pageCount')
             and i.get('volumeInfo', {}).get('imageLinks')),
            items[0]
        )
        info = item.get('volumeInfo', {})
        image_links = info.get('imageLinks', {})
        cover = (
            image_links.get('large')
            or image_links.get('medium')
            or image_links.get('thumbnail')
            or ''
        ).replace('http://', 'https://')
        title  = info.get('title', '')
        author = (info.get('authors') or [''])[0]
        # Google Books ratings are inflated (small, biased sample of Play buyers).
        # Cap at 4.5 and treat 5.0 as 0 (unusable) so GR scraping or OL is preferred.
        raw_rating = float(info.get('averageRating') or 0)
        gr_avg = 0.0 if raw_rating >= 4.99 else round(min(raw_rating, 4.5), 2)
        pages  = info.get('pageCount') or 0
        print(f"Google Books found: {title} ({pages}pp, raw_rating:{raw_rating}, gr_avg:{gr_avg})")
        return {'title': title, 'author': author, 'gr_avg': gr_avg,
                'pages': pages, 'cover_url': cover}
    except Exception as e:
        print(f"Google Books error: {e}")
    return None


def ol_search(title: str = None, isbn: str = None) -> dict | None:
    """Fetch book metadata. Google Books first, Open Library fallback.
    If Google Books finds the book but has no usable rating, supplement with Open Library rating."""
    query = f"isbn:{isbn}" if isbn else title
    result = google_books_search(query)
    if result and result.get('title') and result.get('gr_avg'):
        return result  # GB found it with a usable rating

    print(f"Falling back to Open Library for rating/metadata: {query}")
    try:
        if isbn:
            data = requests.get(
                f"https://openlibrary.org/api/books"
                f"?bibkeys=ISBN:{isbn}&format=json&jscmd=data",
                timeout=5
            ).json()
            d = data.get(f"ISBN:{isbn}")
            if d:
                return {
                    'title':     d.get('title', ''),
                    'author':    (d.get('authors') or [{}])[0].get('name', ''),
                    'gr_avg':    0.0,
                    'pages':     d.get('number_of_pages', 0),
                    'cover_url': d.get('cover', {}).get('large', ''),
                }
        if title:
            data = requests.get(
                f"https://openlibrary.org/search.json"
                f"?title={urllib.parse.quote(title)}&limit=5"
                f"&fields=title,author_name,cover_i,number_of_pages_median,ratings_average",
                timeout=15
            ).json()
            docs = data.get('docs') or []
            if docs:
                d = next(
                    (x for x in docs if x.get('cover_i') and x.get('ratings_average')),
                    next((x for x in docs if x.get('cover_i')), docs[0])
                )
                ol_rating = round(float(d.get('ratings_average') or 0), 2)
                cover = (
                    f"https://covers.openlibrary.org/b/id/{d['cover_i']}-L.jpg"
                    if d.get('cover_i') else ''
                )
                ol_data = {
                    'title':     d.get('title', ''),
                    'author':    (d.get('author_name') or [''])[0],
                    'gr_avg':    ol_rating,
                    'pages':     d.get('number_of_pages_median') or 0,
                    'cover_url': cover,
                }
                # Prefer GB cover/pages over OL if GB found it
                if result:
                    ol_data['cover_url'] = result.get('cover_url') or cover
                    ol_data['pages']     = result.get('pages') or ol_data['pages']
                return ol_data
    except Exception as e:
        print(f"Open Library error: {e}")
    # OL failed — return the GB result (no rating) if we have it, at least for cover/pages
    return result


def ol_search_results(query: str) -> list[dict]:
    """Search books via Google Books then Open Library fallback."""
    api_key = os.environ.get('GOOGLE_BOOKS_API_KEY', '')
    if api_key:
        try:
            url = (
                f"https://www.googleapis.com/books/v1/volumes"
                f"?q={urllib.parse.quote(query)}&maxResults=10&printType=books&key={api_key}"
            )
            data = requests.get(url, timeout=10).json()
            if not data.get('error'):
                items = data.get('items') or []
                results = []
                for item in items:
                    info   = item.get('volumeInfo', {})
                    t      = info.get('title', '').strip()
                    a      = (info.get('authors') or [''])[0].strip()
                    if not t or not a:
                        continue
                    img    = info.get('imageLinks', {})
                    cover  = (img.get('thumbnail') or img.get('smallThumbnail') or '').replace('http://', 'https://')
                    results.append({
                        'title':     t,
                        'author':    a,
                        'gr_avg':    round(float(info.get('averageRating') or 0), 2),
                        'pages':     info.get('pageCount') or 0,
                        'cover_url': cover,
                    })
                if results:
                    return results
        except Exception as e:
            print(f"Google Books search error: {e}")

    try:
        data = requests.get(
            f"https://openlibrary.org/search.json"
            f"?q={urllib.parse.quote(query)}&limit=10"
            f"&fields=title,author_name,cover_i,number_of_pages_median,ratings_average",
            timeout=15
        ).json()
        results = []
        for d in (data.get('docs') or []):
            t = (d.get('title') or '').strip()
            a = ((d.get('author_name') or [''])[0]).strip()
            if not t or not a:
                continue
            cover = (
                f"https://covers.openlibrary.org/b/id/{d['cover_i']}-M.jpg"
                if d.get('cover_i') else ''
            )
            results.append({
                'title':     t,
                'author':    a,
                'gr_avg':    round(float(d.get('ratings_average') or 0), 2),
                'pages':     d.get('number_of_pages_median') or 0,
                'cover_url': cover,
            })
        return results
    except Exception as e:
        print(f"Search error: {e}")
    return []


# ── Goodreads helpers ──────────────────────────────────────────────────────────

def gr_headers() -> dict:
    session  = os.environ.get('GOODREADS_SESSION_ID', '')
    sess_id  = os.environ.get('GR_SESSION_ID', '')
    sess_tok = os.environ.get('GR_SESSION_TOKEN', '')
    at_main  = os.environ.get('GR_AT_MAIN', '')
    ubid     = os.environ.get('GR_UBID_MAIN', '')
    sess_at  = os.environ.get('GR_SESS_AT_MAIN', '')
    cookie = (
        f'_session_id2={session}; session-id={sess_id}; session-token={sess_tok}; '
        f'at-main={at_main}; ubid-main={ubid}; sess-at-main={sess_at}; locale=en'
    )
    return {
        'Cookie':                  cookie,
        'User-Agent':              'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept':                  'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language':         'en-US,en;q=0.9',
        'Accept-Encoding':         'gzip, deflate, br',
        'Connection':              'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Referer':                 'https://www.goodreads.com',
    }


def gr_find_book_id(title: str, author: str) -> str | None:
    """Find Goodreads book ID by searching."""
    try:
        query = urllib.parse.quote(f"{title} {author}")
        r = requests.get(
            f"https://www.goodreads.com/search?q={query}&search_type=books",
            headers=gr_headers(), timeout=10
        )
        print(f"GR search status: {r.status_code}")
        for pattern in [
            r'href="/book/show/(\d+)',
            r'/book/show/(\d+)-',
            r'bookId=(\d+)',
            r'data-book-id="(\d+)"',
        ]:
            match = re.search(pattern, r.text)
            if match:
                print(f"GR found book id: {match.group(1)}")
                return match.group(1)
    except Exception as e:
        print(f"GR find book error: {e}")
    return None


def get_goodreads_rating(title: str, author: str) -> float:
    """Get Goodreads avg rating by fetching the book's own page via its ID."""
    try:
        book_id = gr_find_book_id(title, author)
        if not book_id:
            print(f"[GR] Could not find book ID for: {title}")
            return 0.0
        r = requests.get(
            f"https://www.goodreads.com/book/show/{book_id}",
            headers=gr_headers(), timeout=10
        )
        print(f"[GR] Book page status {r.status_code} for id={book_id}")
        if r.status_code != 200:
            return 0.0
        for pattern in [
            r'itemprop="ratingValue">\s*([\d.]+)',
            r'"ratingValue":\s*"([\d.]+)"',
            r'([\d.]+)\s*avg rating',
        ]:
            match = re.search(pattern, r.text)
            if match:
                rating = round(float(match.group(1)), 2)
                if 1.0 <= rating <= 5.0:
                    print(f"[GR] Rating for '{title}': {rating}")
                    return rating
    except Exception as e:
        print(f"[GR] Rating error: {e}")
    return 0.0


# ── Author helpers ─────────────────────────────────────────────────────────────

def _normalize_author(name: str) -> list:
    """Return candidate lookup keys for an author in both name formats."""
    name = name.strip()
    candidates = [name]
    if ',' in name:
        parts = [p.strip() for p in name.split(',', 1)]
        candidates.append(f"{parts[1]} {parts[0]}")
    else:
        parts = name.rsplit(' ', 1)
        if len(parts) == 2:
            candidates.append(f"{parts[1]}, {parts[0]}")
    candidates += [c.lower() for c in candidates]
    return candidates


def get_author_features(author: str) -> dict:
    """Look up author in library history, handling First/Last vs Last, First."""
    a = None
    for candidate in _normalize_author(author):
        a = AUTHOR_TABLE.get(candidate) or AUTHOR_TABLE.get(candidate.title())
        if a:
            print(f"Author match: '{author}' -> '{candidate}'")
            break
    # No partial last-name match — too many false positives with shared surnames (e.g. "Holly Jackson"
    # matching "Shirley Jackson" or "Robert Jackson Bennett"). Exact match + Last, First normalization
    # is sufficient; unknown authors correctly return author_known=False.
    if not a:
        print(f"Author not found: '{author}'")
        return {'author_avg': 0.0, 'momentum': 0, 'author_known': False, 'rate_5star': 0.0}
    return {
        'author_avg':   a['avg_rating'],
        'momentum':     a['momentum'],
        'author_known': True,
        'rate_5star':   a['rate_5star'],
    }


def _pred5(gr_avg: float, af: dict) -> float:
    """Approximate pred5 from Goodreads avg and author history."""
    effective_avg = gr_avg if gr_avg > 0 else 3.5
    gr_norm = max(0.0, min(1.0, (effective_avg - 2.5) / 2.5))
    if af['author_known']:
        return 0.55 * gr_norm + 0.45 * af['rate_5star']
    return gr_norm * 0.85


# ── AI tagging ─────────────────────────────────────────────────────────────────

def get_tags(title: str, author: str, pages: int,
             gr_avg: float, author_avg: float) -> dict:
    """Call Claude to generate v5 R/P/V tags for a book."""
    author_avg_str = f"{author_avg:.2f}" if author_avg > 0 else 'not previously read'

    prompt = f"""You are tagging a book for a personalized recommendation model. Return ONLY valid JSON — no preamble, no markdown, no explanation.

Book: "{title}" by {author}
Pages: {pages} | Goodreads avg: {gr_avg} | Reader's author avg: {author_avg_str}

RISK TAGS (0/1) — fire when evidence exists; err toward tagging:
R1_Slow: Pacing genuinely slow or meandering. Fire if reviews mention "slow start", "hard to get into", even if overall rating is high.
R2_Repetitive: THIS specific book retreads ground from earlier entries. Only fire if reviews of this specific book say it feels repetitive.
R3a_CharacterDisconnect: Reader won't connect with the protagonist — difficult, unsympathetic, or alienating in ways not redeemed by the end.
R3b_VibeClash: Tone mismatch — cozy/whimsical when profile is dark/literary, OR explicitly graphic/sexual content that would feel jarring.
R4_HighConcept: Ambitious premise with uneven or disappointing execution. Fire if reviews say "great idea but...", "didn't deliver", "uneven".
R5_Dense: Prose is genuinely difficult — experimental, opaque, demanding. NOT just long or literary — only when writing itself is hard to parse.
R6_WeakWriting: ONLY if Goodreads avg < 3.5 AND reviews explicitly criticize prose, dialogue, or writing quality. NEVER apply to GR 3.5+ books.
R7_SeriesFatigue: Evidence of quality decline in THIS entry — reviews note it's weaker than earlier books, author stretching the series.
R8_LowPayoff: Ending or resolution disappoints — readers say the payoff wasn't worth the journey, unsatisfying conclusion.
R9_UnconvincingRelationship: Central relationship (romantic or otherwise) feels unearned, forced, or underdeveloped.
R10_UnderdevelopedConcept: Interesting premise not fully explored — surface-level treatment of a rich idea.
R11_LowSubstance: Entertainment without depth — engaging but leaves nothing behind, forgettable.
R12_PoorCohesion: Tonally inconsistent or structurally disjointed — doesn't hold together as a unified work.
R13_EmptyIntensity: Tries to be intense or dramatic but the emotional weight feels manufactured, not earned.
R14_LowFantasyPayoff: Fantasy/SF elements or world-building don't deliver — magic system, world, or concept underwhelms.
R15_FlatExecution: Competent but flat — lacks the spark that elevates good craft to something memorable.

REWARD TAGS — apply generously when quality is genuinely present:
P1_Distinctive: +25pts. Unlike most books in its genre — genuinely original. Reserve for books that feel unlike anything else.
P2_Propulsive: +20pts. Genuinely hard to put down — readers report staying up late, reading in one sitting.
P3_Emotional: +15pts, GRADED 0/0.5/1.0. 0=not particularly; 0.5=emotionally engaging; 1.0=devastating, lingering, life-changing.
P4_Clever: +15pts, GRADED 0/0.5/1.0. 0=not applicable; 0.5=some wit or intelligence; 1.0=cleverness is primary and fully pays off.
P5_Structure: +15pts. Meaningfully unconventional narrative structure that enhances the story.
P6_Voice: +10pts. Truly singular, unmistakable narrative voice. Reserve for genuinely distinctive voices.

VIBE TAGS (0/1) — for display only, do not affect score:
V1_Speculative: Speculative premise not full SF/fantasy
V2_Dark: Heavy, bleak, or disturbing tone
V3_Romantic: Romance is central to plot or emotional arc
V4_PlotDriven: Momentum-driven, things keep happening
V5_Atmospheric: Deep sense of place, world, or mood
V6_ShortOrStandalone: Standalone or novella-length

CRITICAL RECEPTION (0–3): 0=no notable coverage, 1=notable critical coverage or buzz, 2=major award shortlist, 3=major award winner

GENRE:
G0_Genre: One of: Fantasy, Science Fiction, Literary Fiction, Historical Fiction, Mystery/Thriller, Romance, Horror, Memoir/Biography, Nonfiction, Young Adult, Graphic Novel, Short Stories
G1_Subgenre: Specific subgenre (e.g. "Dark Fantasy", "Gothic Horror", "Magical Realism")

Return ONLY this JSON with no other text:
{{"R1_Slow":0,"R2_Repetitive":0,"R3a_CharacterDisconnect":0,"R3b_VibeClash":0,"R4_HighConcept":0,"R5_Dense":0,"R6_WeakWriting":0,"R7_SeriesFatigue":0,"R8_LowPayoff":0,"R9_UnconvincingRelationship":0,"R10_UnderdevelopedConcept":0,"R11_LowSubstance":0,"R12_PoorCohesion":0,"R13_EmptyIntensity":0,"R14_LowFantasyPayoff":0,"R15_FlatExecution":0,"P1_Distinctive":0,"P2_Propulsive":0,"P3_Emotional":0,"P4_Clever":0,"P5_Structure":0,"P6_Voice":0,"V1_Speculative":0,"V2_Dark":0,"V3_Romantic":0,"V4_PlotDriven":0,"V5_Atmospheric":0,"V6_ShortOrStandalone":0,"Critical_Reception":0,"G0_Genre":"","G1_Subgenre":""}}"""

    try:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            print("ERROR: ANTHROPIC_API_KEY not set")
            return {t: 0 for t in ALL_TAGS + ['Critical_Reception', 'G0_Genre', 'G1_Subgenre']}
        client = anthropic.Anthropic(api_key=api_key)
        print(f"[TAGS] Calling Claude for: {title} by {author}")
        message = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=800,
            messages=[{'role': 'user', 'content': prompt}]
        )
        raw = message.content[0].text.strip()
        print(f"[TAGS] Raw response ({len(raw)} chars): {raw[:300]}")

        start = raw.find('{')
        end   = raw.rfind('}') + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON in response")
        parsed = json.loads(raw[start:end])
        print(f"[TAGS] Parsed {len(parsed)} keys successfully")
        return parsed
    except Exception as e:
        import traceback
        print(f"[TAGS] ERROR: {e}")
        print(traceback.format_exc())
        return {t: 0 for t in ALL_TAGS + ['Critical_Reception', 'G0_Genre', 'G1_Subgenre']}


def format_tags(tags: dict) -> dict:
    """Split flat tags dict into labeled risk/reward/vibe groups."""
    risk_labels = {
        'R1_Slow':                     'Slow Pacing',
        'R2_Repetitive':               'Repetitive',
        'R3a_CharacterDisconnect':     'Character Disconnect',
        'R3b_VibeClash':               'Vibe Clash',
        'R4_HighConcept':              'High Concept Risk',
        'R5_Dense':                    'Dense Prose',
        'R6_WeakWriting':              'Weak Writing',
        'R7_SeriesFatigue':            'Series Fatigue',
        'R8_LowPayoff':                'Low Payoff',
        'R9_UnconvincingRelationship': 'Unconvincing Relationship',
        'R10_UnderdevelopedConcept':   'Underdeveloped Concept',
        'R11_LowSubstance':            'Low Substance',
        'R12_PoorCohesion':            'Poor Cohesion',
        'R13_EmptyIntensity':          'Empty Intensity',
        'R14_LowFantasyPayoff':        'Low Fantasy Payoff',
        'R15_FlatExecution':           'Flat Execution',
    }
    reward_labels = {
        'P1_Distinctive': 'Distinctive',
        'P2_Propulsive':  'Propulsive',
        'P3_Emotional':   'Emotionally Resonant',
        'P4_Clever':      'Clever',
        'P5_Structure':   'Unconventional Structure',
        'P6_Voice':       'Strong Voice',
    }
    vibe_labels = {
        'V1_Speculative':      'Speculative',
        'V2_Dark':             'Dark',
        'V3_Romantic':         'Romantic',
        'V4_PlotDriven':       'Plot-Driven',
        'V5_Atmospheric':      'Atmospheric',
        'V6_ShortOrStandalone':'Standalone',
    }
    risk_out   = {risk_labels[t]:   float(tags.get(t, 0)) for t in RISK_TAGS   if tags.get(t) and t in risk_labels}
    reward_out = {reward_labels[t]: float(tags.get(t, 0)) for t in REWARD_TAGS if tags.get(t) and t in reward_labels}
    vibe_out   = {vibe_labels[t]:   1                     for t in VIBE_TAGS   if tags.get(t) and t in vibe_labels}
    return {
        'risk':   risk_out,
        'reward': reward_out,
        'vibe':   vibe_out,
        'g0':     tags.get('G0_Genre', ''),
        'g1':     tags.get('G1_Subgenre', ''),
    }


# ── Library book result builder ────────────────────────────────────────────────

def build_result(book: dict, meta: dict = None) -> dict:
    """Score a library book (pre-tagged from CSV) and format the full response."""
    tags = {tag: book.get(tag, 0) for tag in ALL_TAGS}

    scored = score_book({
        'pred5':              book['pred5'],
        'author_avg':         book['author_avg'],
        'momentum':           book['momentum'],
        'gr_avg':             book['gr_avg'],
        'critical_reception': book['critical_reception'],
    }, tags)

    formatted = format_tags(tags)
    genre = [g for g in [book.get('g1', ''), book.get('g0', '')] if g]

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
        'genre':       genre,
        'tags':        formatted,
        'shelf':       book['shelf'],
    }


# ── Recents storage ────────────────────────────────────────────────────────────

RECENTS_FILE = Path(__file__).parent / 'recents.json'


def load_recents() -> list:
    try:
        if RECENTS_FILE.exists():
            return json.loads(RECENTS_FILE.read_text())
    except Exception:
        pass
    return []


def save_recents(recents: list) -> None:
    try:
        RECENTS_FILE.write_text(json.dumps(recents))
    except Exception as e:
        print(f"Failed to save recents: {e}")


class RecentBook(BaseModel):
    title:     str
    author:    str
    cover_url: str  = ''
    verdict:   str
    pct_match: int
    timestamp: int
    genre:     list = []
    tags:      dict = {}


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get('/')
def root():
    return {'status': 'ok', 'books_loaded': len(BOOKS), 'authors': len(AUTHOR_TABLE)}


@app.get('/app', response_class=HTMLResponse)
def serve_app():
    """Serve the Inkling mobile app HTML."""
    html_path = Path(__file__).parent / 'inkling_mobile.html'
    if not html_path.exists():
        raise HTTPException(404, 'App HTML not found')
    return HTMLResponse(content=html_path.read_text(), status_code=200)


@app.get('/score')
def score_endpoint(
    title:     str = Query(None, description='Book title'),
    isbn:      str = Query(None, description='ISBN-10 or ISBN-13'),
    author:    str = Query(None, description='Author name (optional, skips re-fetch)'),
    cover_url: str = Query(None, description='Cover URL (optional)'),
    pages:     int = Query(None, description='Page count (optional)'),
):
    """
    Score a book:
    1. Check library (pre-tagged CSV) — fast path
    2. If not in library: fetch metadata, scrape Goodreads rating, call Claude for tags
    """
    if not title and not isbn:
        raise HTTPException(400, 'Provide title or isbn')

    # 1. Library fast path
    book = find_book(BOOKS, title=title, isbn=isbn)
    if book:
        meta = ol_search(title=book['title']) if not isbn else ol_search(isbn=isbn)
        return build_result(book, meta)

    # 2. Not in library — fetch metadata
    if title and author:
        # Title+author provided (e.g. from /identify) — still fetch rating/pages/cover from metadata sources
        meta_info = ol_search(title=title)
        meta = {
            'title':     title,   # Trust provided title over fetched
            'author':    author,  # Trust provided author over fetched
            'gr_avg':    meta_info['gr_avg'] if meta_info else 0.0,
            'pages':     pages or (meta_info['pages'] if meta_info else 300),
            'cover_url': cover_url or (meta_info['cover_url'] if meta_info else ''),
        }
        print(f"[SCORE] Using provided metadata: {title} by {author} (fetched rating: {meta['gr_avg']})")
    else:
        meta = ol_search(isbn=isbn) if isbn else ol_search(title=title)
        if not meta:
            raise HTTPException(404, f'Book not found: {title or isbn}')

    # Always scrape Goodreads for the rating — it's the primary signal
    gr_rating = get_goodreads_rating(meta['title'], meta['author'])
    if gr_rating > 0:
        meta['gr_avg'] = gr_rating
        print(f"[GR] Using Goodreads rating: {gr_rating}")
    elif not meta.get('gr_avg'):
        # Only fall back to neutral if we have no rating from any source
        meta['gr_avg'] = 3.5
        print("[GR] No rating from any source — using neutral 3.5")
    else:
        print(f"[GR] GR scrape failed — keeping {meta['gr_avg']} from metadata source")

    # Author history
    af = get_author_features(meta['author'])

    # AI tags via Claude
    tags = get_tags(
        title=meta['title'],
        author=meta['author'],
        pages=meta['pages'],
        gr_avg=meta['gr_avg'],
        author_avg=af['author_avg'],
    )

    scored = score_book({
        'pred5':              _pred5(meta['gr_avg'], af),
        'author_avg':         af['author_avg'],
        'momentum':           af['momentum'],
        'gr_avg':             meta['gr_avg'],
        'critical_reception': tags.get('Critical_Reception', 0),
    }, tags)

    formatted = format_tags(tags)
    genre = [g for g in [formatted.get('g1', ''), formatted.get('g0', '')] if g]

    return {
        'found_in_library': False,
        'needs_tagging':    False,
        'title':            meta['title'],
        'author':           meta['author'],
        'cover_url':        meta['cover_url'],
        'gr_avg':           meta['gr_avg'],
        'pages':            meta['pages'],
        'pct_match':        scored['pct_match'],
        'verdict':          scored['verdict'],
        'master_score':     scored['master_score'],
        'bucket':           scored['bucket'],
        'author_known':     af['author_known'],
        'genre':            genre,
        'tags':             formatted,
    }


@app.get('/search')
def search_endpoint(q: str = Query(..., description='Title or author search')):
    """Search books. Returns approximate scores (no Claude call — too slow for a list)."""
    results = ol_search_results(q)
    if not results:
        return {'count': 0, 'results': []}

    enriched = []
    for r in results:
        af = get_author_features(r['author'])
        approx = score_book({
            'pred5':              _pred5(r['gr_avg'], af),
            'author_avg':         af['author_avg'],
            'momentum':           af['momentum'],
            'gr_avg':             r['gr_avg'],
            'critical_reception': 0,
        }, {})
        enriched.append({
            'title':        r['title'],
            'author':       r['author'],
            'cover_url':    r['cover_url'],
            'gr_avg':       r['gr_avg'],
            'pct_match':    approx['pct_match'],
            'verdict':      approx['verdict'],
            'master_score': approx['master_score'],
            'author_known': af['author_known'],
        })

    enriched.sort(key=lambda x: x['master_score'], reverse=True)
    return {'count': len(enriched), 'results': enriched}


@app.get('/book/{title}')
def book_detail(title: str):
    """Get full details for a specific library book."""
    book = find_book(BOOKS, title=title)
    if not book:
        raise HTTPException(404, f'"{title}" not found in library')
    return build_result(book)


class IdentifyRequest(BaseModel):
    image: str  # base64 JPEG


@app.post('/identify')
def identify_cover(req: IdentifyRequest):
    """Identify a book cover from a base64 JPEG image using Claude vision."""
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        raise HTTPException(500, 'API key not configured')
    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=200,
            messages=[{
                'role': 'user',
                'content': [
                    {
                        'type': 'image',
                        'source': {
                            'type':       'base64',
                            'media_type': 'image/jpeg',
                            'data':       req.image,
                        }
                    },
                    {
                        'type': 'text',
                        'text': (
                            'This is a photo of a book cover taken with a phone camera. '
                            'Look carefully at any text visible on the cover. Your job is to read '
                            'the EXACT title and author name as they appear printed on this specific cover '
                            '— do not guess, infer, or suggest similar books.\n\n'
                            'Respond with ONLY these two lines:\n'
                            'TITLE: <exact title as printed on the cover>\n'
                            'AUTHOR: <exact author name as printed on the cover>\n\n'
                            'If the image is too blurry, dark, or you cannot confidently read both the '
                            'title and author, respond only with: UNKNOWN\n'
                            'Do NOT substitute a different book, do NOT guess based on partial text.'
                        )
                    }
                ]
            }]
        )
        return {'result': message.content[0].text.strip()}
    except Exception as e:
        print(f"Identify error: {e}")
        raise HTTPException(500, f'Identification failed: {str(e)}')


@app.get('/debug-search')
def debug_search(title: str = Query(...)):
    """Debug — test book metadata lookup without scoring."""
    gb_result = google_books_search(title)
    ol_result = ol_search(title=title)
    return {
        'title_queried': title,
        'google_books':  gb_result,
        'open_library':  ol_result,
        'final':         gb_result or ol_result,
    }


@app.get('/test-tags')
def test_tags_endpoint(title: str = Query(...), author: str = Query(default='')):
    """Debug — returns raw Claude tags for a book without scoring."""
    tags = get_tags(title=title, author=author, pages=300, gr_avg=3.8, author_avg=0)
    return {'title': title, 'author': author, 'tags': tags}


@app.get('/recents')
def get_recents():
    """Return saved recent lookups."""
    return {'recents': load_recents()}


@app.post('/recents')
def add_recent(book: RecentBook):
    """Add a book to recents, deduplicating by title."""
    recents = load_recents()
    recents = [b for b in recents if b.get('title', '').lower() != book.title.lower()]
    recents.insert(0, book.dict())
    recents = recents[:20]
    save_recents(recents)
    return {'ok': True, 'count': len(recents)}


@app.delete('/recents')
def clear_recents():
    """Clear all recents."""
    save_recents([])
    return {'ok': True}


@app.get('/goodreads/find')
def gr_find_endpoint(title: str = Query(...), author: str = Query('')):
    """Find a Goodreads book ID by title/author."""
    book_id = gr_find_book_id(title, author)
    if not book_id:
        raise HTTPException(404, f'Could not find "{title}" on Goodreads')
    return {'book_id': book_id}
