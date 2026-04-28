from __future__ import annotations
# main.py — FastAPI server

import os
import json
import re
import urllib.parse
import requests
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer as _HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from database import engine
import models  # ensures all ORM classes are registered before create_all
from auth import router as auth_router, get_current_user, DEV_MODE
from database import get_db
from sqlalchemy.orm import Session
from models import User, UserSettings
from library import load_library, find_book
from upload import router as upload_router
from score import score_book
from weights import (
    BUCKET_DISPLAY,
    RISK_TAGS, REWARD_TAGS, VIBE_TAGS, ALL_TAGS,
    RISK_WEIGHTS, REWARD_WEIGHTS,
    TROPE_LIFTS,
)

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

# Library upload + calibration router
app.include_router(upload_router)

# ── Optional auth — resolves user from JWT if present, else None ──────────────
from fastapi import Depends
from fastapi.security import HTTPBearer as _OptBearer, HTTPAuthorizationCredentials as _Creds
from jose import JWTError, jwt as _jwt
from auth import SECRET_KEY, ALGORITHM

_opt_bearer = _OptBearer(auto_error=False)

def get_optional_user(
    credentials: _Creds | None = Depends(_opt_bearer),
    db: Session = Depends(get_db),
) -> User | None:
    """Return the authenticated User if a valid JWT is present, else None."""
    if not credentials:
        return None
    try:
        payload = _jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get('sub', 0))
    except (JWTError, ValueError):
        return None
    return db.query(User).filter(User.id == user_id).first()


def _get_user_weights(user: User | None, db: Session) -> dict | None:
    """Load parsed algorithm_weights for user, or None if uncalibrated."""
    if not user:
        return None
    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    if not settings or not settings.algorithm_weights:
        return None
    try:
        return json.loads(settings.algorithm_weights)
    except Exception:
        return None


# ── Dev bypass: mark library as built ─────────────────────────────────────────

@app.post('/library/dev-skip', include_in_schema=DEV_MODE)
def library_dev_skip(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dev-only: mark library_built=True without uploading a CSV."""
    if not DEV_MODE:
        raise HTTPException(404, 'Not found')
    current_user.library_built = True
    db.commit()
    return {'ok': True}

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

# JSON template for tagging prompt — built once at module load
_TROPE_ZERO = {t: 0 for t in TROPE_LIFTS}
_TAG_TEMPLATE = json.dumps({
    'R1_Slow': 0, 'R2_Repetitive': 0, 'R3_VibeClash': 0, 'R4_HighConcept': 0,
    'R5_InaccessibleProse': 0, 'R6_WeakWriting': 0, 'R7_SeriesFatigue': 0,
    'R8_TooLong': 0, 'R9_ContentWarnings': 0, 'R10_TranslationQuality': 0,
    'R11_DatedContent': 0,
    'P1_Distinctive': 0, 'P2_Propulsive': 0, 'P3_Emotional': 0, 'P4_Clever': 0,
    'P5_Structure': 0, 'P6_Voice': 0, 'P7_Satisfying': 0,
    'V2_Dark': 0, 'V4_PlotDriven': 0, 'V5_Atmospheric': 0, 'V6_Funny': 0,
    'V7_Unsettling': 0, 'V8_Philosophical': 0, 'V9_Heartbreaking': 0, 'V10_Cozy': 0,
    **_TROPE_ZERO,
    'Critical_Reception': 0, 'G0_Genre': '', 'G1_Subgenre': '',
})


def get_tags(title: str, author: str, pages: int,
             gr_avg: float, author_avg: float) -> dict:
    """Call Claude to generate v5 R/P/V/T tags for a book."""
    author_avg_str = f"{author_avg:.2f}" if author_avg > 0 else 'not previously read'

    prompt = f"""You are tagging a book for a personalized recommendation engine. Using the book's description, Goodreads reviews, and any available critical reception data, assess the following book and return a JSON object with these exact fields. No preamble, no explanation — JSON only.

Book: "{title}" by {author}
Pages: {pages} | Goodreads avg: {gr_avg} | Reader's author avg: {author_avg_str}

────────────────────────────────────────────────────────────
RISK TAGS (0 or 1)
────────────────────────────────────────────────────────────

R1_Slow (weight 0.09):
  Does the book have genuinely slow or meandering pacing?
  Look for: reviews mentioning "slow start", "hard to get into", "took 100 pages to hook me", "meandering middle."
  DO NOT fire on: books that are simply contemplative, atmospheric, or character-driven. Slow ≠ quiet.

R2_Repetitive (weight 0.11):
  Is this a sequel where the series is showing diminishing returns or repetitive patterns?
  Look for: reviews saying "more of the same", "didn't add anything new", "felt like filler."
  DO NOT fire on: sequels that are well-regarded continuations.

R3_VibeClash (weight 0.07):
  Is this a strong genre or tone mismatch for a reader who loves dark, literary, speculative fiction?
  Fire on: cozy mysteries, self-help, pure contemporary romance with no speculative element, business books.
  DO NOT fire on: books that are dark, literary, or genre-adjacent even if they also have romance.

R4_HighConcept (weight 0.13):
  Is the premise stronger than the execution? Does the book fail to deliver on its central idea?
  Look for: reviews saying "great concept but...", "premise sounded amazing, execution disappointed", "all setup no payoff."
  DO NOT fire just because a book is ambitious or high-concept — only fire if execution is the problem.

R5_InaccessibleProse (weight 0.07):
  Is the prose genuinely difficult, dense, or inaccessible — requiring active effort to parse?
  Look for: translated experimental literary fiction, stream of consciousness, deliberately challenging syntax.
  DO NOT fire on: lyrical, ornate, or literary prose that is beautiful but readable.

R6_WeakWriting (weight 0.23):
  IMPORTANT: This tag is about prose quality, NOT crowd reception. A low Goodreads rating alone
  is NOT sufficient to fire this tag. Many niche, polarizing, or underseen books have low ratings
  but strong prose.
  Fire ONLY when there is direct evidence of weak prose:
    - Multiple reviews specifically citing flat, serviceable, or forgettable writing
    - Reviews saying "the writing let it down", "prose was pedestrian", "couldn't connect to the voice"
    - The writing itself (not the plot or concept) is the primary complaint
  DO NOT fire on:
    - Books with low Goodreads averages that lack prose complaints
    - Books where the concept/plot is divisive but the writing is praised
    - Books where this reader's author avg is high (they've read the author and liked them)
    - Polarizing books where some readers love and some hate the style

R7_SeriesFatigue (weight 0.12):
  Is this book 4 or later in a numbered series where earlier books got declining reviews?
  Look for: series position 4+ AND reviews noting fatigue, repetition, or quality drop.
  DO NOT fire if: the series is consistently well-regarded through this entry, or this is a standalone in a shared world.

R8_TooLong (weight 0.00 — tag only, not scored yet):
  Is the book significantly longer than its genre peers AND reviews cite bloat or pacing issues due to length?
  DO NOT fire just because a book is long if the length is justified.

R9_ContentWarnings (weight 0.00 — tag only, not scored yet):
  Does the book contain heavy content that commonly requires trigger warnings?
  (e.g., graphic violence, sexual assault, child abuse, suicide depicted in detail)
  This is about content presence, not tone. A dark atmospheric book with no graphic content = 0.

R10_TranslationQuality (weight 0.00 — tag only, not scored yet):
  Is this a translated work where the translation quality is specifically cited as a problem?
  DO NOT fire just because a book is translated — only if translation is a cited issue.

R11_DatedContent (weight 0.00 — tag only, not scored yet):
  Does the book contain attitudes, representations, or cultural content that reviewers flag as
  significantly dated or problematic in ways that affect the reading experience?

────────────────────────────────────────────────────────────
REWARD TAGS (0 or 1)
────────────────────────────────────────────────────────────

P1_Distinctive (weight 0.12):
  Is this book genuinely original — unlike anything else in its genre?
  Look for: reviews saying "unlike anything I've read", award recognition for originality,
  a truly unusual premise OR execution that stands out.

P2_Propulsive (weight 0.15):
  Is the READING EXPERIENCE hard to put down — does it pull you forward?
  This is about reading momentum, not plot mechanics. A literary novel can be propulsive.
  Look for: "couldn't stop", "stayed up all night", "devoured it."
  DO NOT confuse with V4_PlotDriven (plot-structured) — those are different things.

P3_Emotional (weight 0.22):
  Is the book emotionally resonant — does it stay with you after you finish?
  Look for: "cried", "devastating", "life-changing", "couldn't stop thinking about it."

P4_Clever (weight 0.10):
  Did the author do something smart, interesting, or intellectually impressive?
  This is about authorial intelligence — structural cleverness, thematic depth, subversive choices.
  NOT the same as funny. Look for: "brilliant", "smart", "subversive", "layered."

P5_Structure (weight 0.08):
  Does the book use unconventional narrative structure in a way that adds to the experience?
  (e.g., epistolary, nonlinear, multiple unreliable narrators, frame narrative, second person)
  DO NOT fire just because there are multiple POVs — structure must be notable.

P6_Voice (weight 0.10):
  Does the book have a strong, distinctive narrative voice that feels singular and alive?
  Look for: reviews praising the narrator's personality, close first-person intimacy, a voice
  you would recognize immediately.

P7_Satisfying (weight 0.23):
  Does the book deliver a satisfying payoff — does it earn its ending?
  This is the strongest single reward signal. Look for: "perfect ending", "everything came together",
  "so satisfying", "stuck the landing." Can apply to tragedy as well as happy endings — satisfying
  means earned, not necessarily happy.
  DO NOT fire if reviews frequently cite an unsatisfying, abrupt, or unearned ending.

────────────────────────────────────────────────────────────
VIBE TAGS (0 or 1 — display only, not scored)
────────────────────────────────────────────────────────────

V2_Dark: The book has a dark, heavy, or disturbing TONE and atmosphere.
  This is about mood, not content warnings. DO NOT confuse with R9_ContentWarnings — dark tone ≠ graphic content.

V4_PlotDriven: The book is STRUCTURALLY organized around plot mechanics — mystery, thriller, heist, quest.
  This is about structure, not pace. DO NOT confuse with P2_Propulsive (reading feel) — those are different axes.

V5_Atmospheric: The setting or world is so vividly rendered it feels like a character itself.
  Reserve for books where place is central to the experience — not just "well-described."

V6_Funny: The book is genuinely funny — it makes you laugh.
  NOT the same as P4_Clever. Look for: "laugh out loud", "hilarious", "comedy."

V7_Unsettling: The book creates a sense of unease, dread, or psychological discomfort — even if not horror.

V8_Philosophical: The book engages seriously with philosophical, moral, or existential questions.

V9_Heartbreaking: The book is emotionally devastating — grief, loss, tragedy are central.
  More specific than P3_Emotional — this is specifically about heartbreak.

V10_Cozy: The book has a warm, comforting, low-stakes tone.

────────────────────────────────────────────────────────────
GENRE TAGS (display only, not scored)
────────────────────────────────────────────────────────────

G0_Genre: Primary genre. Use ONLY one of:
  Fantasy, Science Fiction, Horror, Mystery, Thriller,
  Historical Fiction, Literary Fiction, Nonfiction, Graphic Novel

G1_Subgenre: Use ONLY values from this list (must match G0):
  Fantasy: Epic Fantasy, Dark Fantasy, Urban Fantasy, Cozy Fantasy, Romantic Fantasy,
           Portal Fantasy, Mythic Fantasy, Fairy Tale Retelling, Gaslamp Fantasy,
           Magical Realism, Speculative
  Science Fiction: Dystopian, Space Opera, Hard Sci-Fi, Cyberpunk, Biopunk,
                   Climate Fiction, First Contact, LitRPG/GameLit
  Horror: Gothic, Psychological Horror, Supernatural Horror, Folk Horror,
          Body Horror, Creature Horror, Haunted House, Horror Comics, Southern Gothic
  Mystery: Historical Mystery, Amateur Sleuth, Police Procedural, Cozy Mystery,
           Crime Noir, Locked Room
  Thriller: Psychological Thriller, Legal Thriller, Tech Thriller, Historical Thriller,
            Spy/Espionage, Action/Adventure
  Historical Fiction: Literary Historical, Ancient World, War Fiction, Historical Romance,
                      Historical Mystery, Historical Thriller
  Literary Fiction: Contemporary Literary, Classic Literary, Autofiction, Magical Realism,
                    Satire, Short Stories, Speculative
  Nonfiction: Memoir, Memoir Comics, Essays & Narrative, Biography, Popular Science,
              Literary Criticism, Business/Professional, Food & Travel, Self-Help
  Graphic Novel: Horror Comics, Memoir Comics

────────────────────────────────────────────────────────────
TROPE TAGS (0 or 1 — used for display and scoring)
────────────────────────────────────────────────────────────

Tag 1 if the trope is a significant element of the book, 0 if absent or only incidental.

T_Addiction, T_Age_Gap, T_AI_Robots, T_Amnesia, T_Anti_Hero,
T_Art_Creativity, T_Band_of_Misfits, T_Boarding_School, T_Books_Libraries,
T_Chosen_One, T_Class_Society, T_Cold_Case, T_Demons_Angels, T_Dragons,
T_Fae_Faerie, T_Fake_Dating, T_Fish_Out_of_Water, T_Forced_Proximity,
T_Found_Family, T_Found_Purpose, T_Frame_Narrative, T_Gods_Mythology,
T_Grief_Loss, T_Ghosts_Spirits, T_Heist, T_Hidden_Identity, T_Hidden_World,
T_Identity_Belonging, T_Island_Isolated_Setting, T_Locked_Room, T_Magic_System,
T_Mental_Health, T_Missing_Person, T_Mentor_Protege, T_Morally_Grey_Protagonist,
T_Necromancy, T_One_Bed, T_Outsider_POV, T_Parallel_Timelines,
T_Politics_Revolution, T_Post_Apocalyptic, T_Power_Corruption, T_Prophecy,
T_Quest_Journey, T_Redemption_Arc, T_Reluctant_Hero, T_Revenge_Plot,
T_Rivals_to_Lovers, T_Road_Trip, T_Second_Chance_Romance, T_Secret_Society,
T_Slow_Burn, T_Small_Town, T_Space_Exploration, T_Story_Within_a_Story,
T_Superpowers, T_Survival, T_Time_Loop, T_Tournament_Competition,
T_Trauma_Recovery, T_Twist_Ending, T_Underdog, T_Unreliable_Narrator,
T_Unrequited_Love, T_Vampires, T_Villain_Protagonist, T_War_Aftermath,
T_Werewolves, T_Witches_Warlocks

────────────────────────────────────────────────────────────
CRITICAL RECEPTION (integer 0–3)
────────────────────────────────────────────────────────────

0 = no notable critical recognition
1 = positive critical reception, notable reviews, best-of lists
2 = major award shortlist or longlist (Booker, Hugo, Nebula, Pulitzer, National Book, Women's Prize, NBCC, etc.)
3 = major award winner

────────────────────────────────────────────────────────────
OUTPUT
────────────────────────────────────────────────────────────

Return ONLY valid JSON. No preamble, no explanation, no markdown fences.

{_TAG_TEMPLATE}"""

    try:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            print("ERROR: ANTHROPIC_API_KEY not set")
            return {}
        client = anthropic.Anthropic(api_key=api_key)
        print(f"[TAGS] Calling Claude for: {title} by {author}")
        message = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=2048,
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
        return {}


def format_tags(tags: dict) -> dict:
    """Split flat tags dict into labeled risk/reward/vibe groups for the frontend."""
    risk_labels = {
        'R1_Slow':              'Slow Pacing',
        'R2_Repetitive':        'Repetitive',
        'R3_VibeClash':         'Vibe Clash',
        'R4_HighConcept':       'High Concept Risk',
        'R5_InaccessibleProse': 'Inaccessible Prose',
        'R6_WeakWriting':       'Weak Writing',
        'R7_SeriesFatigue':     'Series Fatigue',
        'R8_TooLong':           'Too Long',
        'R9_ContentWarnings':   'Content Warnings',
        'R10_TranslationQuality': 'Translation Quality',
        'R11_DatedContent':     'Dated Content',
    }
    reward_labels = {
        'P1_Distinctive': 'Distinctive',
        'P2_Propulsive':  'Propulsive',
        'P3_Emotional':   'Emotionally Resonant',
        'P4_Clever':      'Clever',
        'P5_Structure':   'Unconventional Structure',
        'P6_Voice':       'Strong Voice',
        'P7_Satisfying':  'Satisfying Payoff',
    }
    vibe_labels = {
        'V2_Dark':          'Dark',
        'V4_PlotDriven':    'Plot-Driven',
        'V5_Atmospheric':   'Atmospheric',
        'V6_Funny':         'Funny',
        'V7_Unsettling':    'Unsettling',
        'V8_Philosophical': 'Philosophical',
        'V9_Heartbreaking': 'Heartbreaking',
        'V10_Cozy':         'Cozy',
    }
    trope_labels = {
        'T_Addiction': 'Addiction', 'T_Age_Gap': 'Age Gap',
        'T_AI_Robots': 'AI & Robots', 'T_Amnesia': 'Amnesia',
        'T_Anti_Hero': 'Antihero', 'T_Art_Creativity': 'Art & Creativity',
        'T_Band_of_Misfits': 'Band of Misfits', 'T_Boarding_School': 'Boarding School',
        'T_Books_Libraries': 'Books & Libraries', 'T_Chosen_One': 'Chosen One',
        'T_Class_Society': 'Class & Society', 'T_Cold_Case': 'Cold Case',
        'T_Demons_Angels': 'Demons & Angels', 'T_Dragons': 'Dragons',
        'T_Fae_Faerie': 'Fae / Faerie', 'T_Fake_Dating': 'Fake Dating',
        'T_Fish_Out_of_Water': 'Fish Out of Water', 'T_Forced_Proximity': 'Forced Proximity',
        'T_Found_Family': 'Found Family', 'T_Found_Purpose': 'Finding Purpose',
        'T_Frame_Narrative': 'Frame Narrative', 'T_Gods_Mythology': 'Gods & Mythology',
        'T_Grief_Loss': 'Grief & Loss', 'T_Ghosts_Spirits': 'Ghosts & Spirits',
        'T_Heist': 'Heist', 'T_Hidden_Identity': 'Hidden Identity',
        'T_Hidden_World': 'Hidden World', 'T_Identity_Belonging': 'Identity & Belonging',
        'T_Island_Isolated_Setting': 'Isolated Setting', 'T_Locked_Room': 'Locked Room',
        'T_Magic_System': 'Magic System', 'T_Mental_Health': 'Mental Health',
        'T_Missing_Person': 'Missing Person', 'T_Mentor_Protege': 'Mentor & Protégé',
        'T_Morally_Grey_Protagonist': 'Morally Grey Protagonist', 'T_Necromancy': 'Necromancy',
        'T_One_Bed': 'One Bed', 'T_Outsider_POV': 'Outsider POV',
        'T_Parallel_Timelines': 'Parallel Timelines', 'T_Politics_Revolution': 'Politics & Revolution',
        'T_Post_Apocalyptic': 'Post-Apocalyptic', 'T_Power_Corruption': 'Power & Corruption',
        'T_Prophecy': 'Prophecy', 'T_Quest_Journey': 'Quest / Journey',
        'T_Redemption_Arc': 'Redemption Arc', 'T_Reluctant_Hero': 'Reluctant Hero',
        'T_Revenge_Plot': 'Revenge Plot', 'T_Rivals_to_Lovers': 'Rivals to Lovers',
        'T_Road_Trip': 'Road Trip', 'T_Second_Chance_Romance': 'Second Chance Romance',
        'T_Secret_Society': 'Secret Society', 'T_Slow_Burn': 'Slow Burn',
        'T_Small_Town': 'Small Town', 'T_Space_Exploration': 'Space Exploration',
        'T_Story_Within_a_Story': 'Story Within a Story', 'T_Superpowers': 'Superpowers',
        'T_Survival': 'Survival', 'T_Time_Loop': 'Time Loop',
        'T_Tournament_Competition': 'Tournament / Competition', 'T_Trauma_Recovery': 'Trauma & Recovery',
        'T_Twist_Ending': 'Twist Ending', 'T_Underdog': 'Underdog',
        'T_Unreliable_Narrator': 'Unreliable Narrator', 'T_Unrequited_Love': 'Unrequited Love',
        'T_Vampires': 'Vampires', 'T_Villain_Protagonist': 'Villain Protagonist',
        'T_War_Aftermath': 'War Aftermath', 'T_Werewolves': 'Werewolves',
        'T_Witches_Warlocks': 'Witches & Warlocks',
    }

    risk_out   = {risk_labels[t]:   RISK_WEIGHTS.get(t, 0)   for t in risk_labels   if tags.get(t)}
    reward_out = {reward_labels[t]: REWARD_WEIGHTS.get(t, 0) for t in reward_labels if tags.get(t)}
    vibe_out   = {vibe_labels[t]:   1                     for t in vibe_labels   if tags.get(t)}
    trope_out  = {trope_labels[t]:  TROPE_LIFTS.get(t, 0) for t in trope_labels  if tags.get(t)}

    return {
        'risk':   risk_out,
        'reward': reward_out,
        'vibe':   vibe_out,
        'tropes': trope_out,
        'g0':     tags.get('G0_Genre', ''),
        'g1':     tags.get('G1_Subgenre', ''),
    }


# ── Library book result builder ────────────────────────────────────────────────

def build_result(book: dict, meta: dict = None, user_weights: dict | None = None) -> dict:
    """Score a library book (pre-tagged from CSV) and format the full response."""
    tags = {tag: book.get(tag, 0) for tag in ALL_TAGS}

    scored = score_book({
        'pred5':              book['pred5'],
        'author_avg':         book['author_avg'],
        'momentum':           book['momentum'],
        'gr_avg':             book['gr_avg'],
        'critical_reception': book['critical_reception'],
    }, tags, user_weights=user_weights)

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
    current_user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Score a book:
    1. Check library (pre-tagged CSV) — fast path
    2. If not in library: fetch metadata, scrape Goodreads rating, call Claude for tags
    """
    if not title and not isbn:
        raise HTTPException(400, 'Provide title or isbn')

    # Resolve per-user weights (None = use global defaults)
    user_weights = _get_user_weights(current_user, db) if current_user else None

    # 1. Library fast path
    book = find_book(BOOKS, title=title, isbn=isbn)
    if book:
        meta = ol_search(title=book['title']) if not isbn else ol_search(isbn=isbn)
        return build_result(book, meta, user_weights=user_weights)

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
    }, tags, user_weights=user_weights)

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
