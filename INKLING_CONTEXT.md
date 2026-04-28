# Inkling — Full Project Context
*Covers all sessions April 2026. Read this at the start of every Claude Code session before making any changes.*

---

## Who Is Marisa

Marisa is the product owner, primary decision-maker, and the person this app is built for. She is not a developer but works in tech and has led large-scale implementations — she understands high-level technical explanations and works in sprint-based iterations with Claude as developer and design partner. She has ADHD, which informs both the design (scannable, clear hierarchy, low cognitive load despite density) and the working style (direct, specific feedback, iterative).

Claude Code functions as the developer. Marisa makes all product decisions.

---

## What Is Inkling

A personal iOS book recommendation app designed for **point-of-decision use** — primarily in bookstores and libraries, in the moment when you're standing in front of a shelf deciding whether to buy or borrow a book.

The core experience: scan a book's ISBN or photograph the cover and receive an instant, personalized verdict — **Strong Inkling**, **On the Fence**, or **Hard Pass** — with a match percentage and a one-sentence explanation of why.

The algorithm is trained on Marisa's 13-year, ~1,010-book Goodreads history. The algorithm structure *is* the product — each future user generates their own version by uploading their Goodreads CSV at onboarding.

**The "explains why" feature is central to the value proposition** — not just a score, but an articulation of why this book is or isn't right for you. This is a stronger monetization argument than raw accuracy.

---

## Product Vision & Strategy

### The Unowned Moment
Inkling addresses a UX moment that no product currently owns: the point-of-purchase decision at a bookstore or library. Goodreads tells you what you've read. Amazon recommends what to buy. No tool gives you a fast, personal verdict when you're standing in front of a book right now.

### Target Users
- Avid readers who use Goodreads and have meaningful reading history
- People who feel overwhelmed by choice at bookstores/libraries
- Readers who want personalized recommendations beyond "people who bought this also bought"
- Initially: readers like Marisa — literary fiction, speculative fiction, ADHD-friendly design

### Monetization Thinking
- Price range: **$5–$7** — more defensible than $2–3 because the "explains why" feature justifies it
- The algorithm being *trained on your own data* is a strong differentiator — not a generic recommender
- Future: onboarding flow where any reader uploads their Goodreads CSV and generates their own personalized model
- Potential for tiered features (basic score free, full tag explanation paid)

### Competitive Positioning
- Not Goodreads (cataloging, not decision support)
- Not Amazon recommendations (generic, commercial)
- Not StoryGraph (discovery-focused, not point-of-decision)
- Unique: personal algorithm + at-shelf use case + explains reasoning

### Distribution Path
1. Currently: personal use via Xcode install / Railway URL
2. Near-term: TestFlight beta with select readers
3. Target: App Store (iOS first)
4. Beta outreach one-pager written — three engagement paths: beta testing, guidance/expertise, collaboration

---

## Design Philosophy

### Aesthetic
- **Dark mode only**
- **Dense and rich** — more information, tighter layout
- **Midcentury modern meets literary** — think Saul Bass meets a well-designed hardcover
- **ADHD-friendly hierarchy** — verdict is the hero, instant scanability, color-coded categories
- Warm sans-serif body text (not monospace/"coder" feel — that reads as Matrix/terminal)
- Serif (Newsreader) for verdicts, titles, and emotional moments
- The verdict is always the hero element — the number and verdict label are what your eye hits first

### Color System (locked — do not change)
| Color | Hex | Usage |
|-------|-----|-------|
| Plum | `#7c3060` | Brand, tile backgrounds, bonuses |
| Amber/Gold | `#df832e` | Primary accent, all gold text/icons — Strong Inkling verdict |
| Light Plum | `#c084a0` | On the Fence verdict |
| Mauve | `#b478a0` | Vibe chips |
| Rose | `#c45070` | Watch Out For chips (negative-lift tropes) |
| Burgundy | `#aa2840` | Hard Pass verdict, risk tag accent |
| Black | `#000000` | All screen backgrounds |

### Typography
- **Newsreader** (serif) — verdicts, titles, taglines, emotional moments
- **Manrope** (sans-serif) — body text, labels, descriptions
- Never monospace for UI text (was tried, rejected — feels like a developer tool)

### Key Design Decisions Made
- Both action tiles are plum background with gold text (tried split plum/amber — looked unbalanced)
- Full animated logo video as home page hero, no standard header on home
- Lightbulb verdicts: three separate videos (strong/fence/pass), each baked with its own glow
- Tagline: "To read or not to read" (line 1, bold, gold) / "That is the question." (line 2, smaller, 62% opacity)
- "Hard Pass" not "Pass on It" (changed April 2026)

---

## Architecture

### Backend (Railway)
- **Framework**: FastAPI
- **URL**: `https://web-production-2c0f89.up.railway.app`
- **Service**: romantic-courtesy
- **Repo**: `https://github.com/marisaebloom-collab/inkling-api`
- **Local path**: `~/inkling-api/`
- **Deploy**: `cd ~/inkling-api && railway up`
- **Logs**: `railway logs`

### Frontend
- Single HTML file (`inkling_mobile.html`) served from Railway at `/app`
- Also installed as iOS app via Xcode (WKWebView → Railway URL)
- File size: ~8.4MB (embedded b64 videos)

### Railway Environment Variables
- `ANTHROPIC_API_KEY` — set
- `GOOGLE_BOOKS_API_KEY` — set
- `GOODREADS_USER_ID` — 11539439

### Key Files in `~/inkling-api/`
| File | Purpose |
|------|---------|
| `main.py` | FastAPI server, all endpoints |
| `score.py` | `score_book()` function |
| `weights.py` | All tunable constants |
| `library.py` | CSV loader, `find_book()`, `load_library()` |
| `inkling_mobile.html` | Complete frontend (~8.4MB, embedded videos) |
| `books.csv` | Goodreads export with v5 tags (single-user, pre-auth) |
| `INKLING_CONTEXT.md` | This file |

*Note: `books.csv` + global `BOOKS`/`AUTHOR_TABLE` are the pre-auth single-user architecture. Once auth + UserBook DB is live, all scoring routes use per-user DB queries instead.*

### API Endpoints
| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/` | GET | — | Health check |
| `/app` | GET | — | Serves inkling_mobile.html |
| `/score` | GET | required | Score a book (title or isbn param) |
| `/search` | GET | required | Search books (q param) |
| `/identify` | POST | — | Claude vision: identify book from cover photo |
| `/debug-search` | GET | — | Test metadata lookup without scoring |
| `/test-tags` | GET | — | Test Claude tagging without scoring |
| `/recents` | GET/POST/DELETE | required | Per-user scan history (replaces recents.json) |
| `/goodreads/find` | GET | — | Find Goodreads book ID |
| `/auth/register` | POST | — | Email + password → JWT |
| `/auth/login` | POST | — | Email + password → JWT |
| `/auth/apple` | POST | — | Apple user ID + email → JWT |
| `/auth/me` | GET | required | Current user info |
| `/library/upload` | POST | required | Upload Goodreads CSV → populate UserBook |
| `/library/status` | GET | required | library_built flag + book count |

---

## Auth Architecture (Sprint — In Progress)

### Goal
Add a complete auth layer so Inkling can support multiple users, each with their own reading history, algorithm calibration, and scan history.

### Onboarding Flow
1. **Create account** — email/password or Apple Sign In
2. **Upload reading history** — user uploads their Goodreads CSV export; backend parses it into `UserBook` rows; `library_built` set to `True`
3. **Algorithm calibration** — profile/algorithm screen shows the user's weights derived from their own data

### Files to Create
| File | Purpose |
|------|---------|
| `database.py` | SQLAlchemy setup, `DATABASE_URL` env var, `postgres://` → `postgresql://` fix, `pool_pre_ping=True`, `pool_recycle=300`, `get_db()` dependency |
| `models.py` | Five tables: User, UserBook, ScanResult, UserSettings (see schema below) |
| `auth.py` | APIRouter `/auth`, JWT (HS256, 30-day expiry), bcrypt, register/login/apple/me endpoints, `get_current_user()` dependency |

### Design Principle
Store only what the scoring algorithm needs as *input*. Tags, individual book titles, ISBNs — all generated or fetched at scan time. The CSV upload aggregates into AuthorProfile rows and is discarded. No per-book storage.

### Database Schema (4 tables)

**User**
- `id` (PK), `email` (unique, nullable), `hashed_password` (nullable — dev bypass only), `apple_user_id` (unique, nullable), `google_user_id` (unique, nullable), `created_at`, `library_built` (bool, default False)

**AuthorProfile** — one row per author the user has read and rated
- `id`, `user_id` FK (cascade delete)
- `author_name` (canonical "First Last" form — normalised at upload time)
- `books_read`, `avg_rating`, `best_rating`, `rate_4plus`, `rate_5star`, `most_recent_year_read`
- Unique constraint on (user_id, author_name)
- This is the *only* reading-history data stored. Everything `get_author_features()` needs to compute `author_signal`, `pred5`, `momentum`, and `div_bonus`.

**ScanResult** — per-user scan history (replaces `recents.json`)
- `id`, `user_id` FK (cascade delete), `isbn`, `title`, `author`, `cover_url`, `verdict`, `match_pct`, `master_score`, `scanned_at`
- `vibe_tags` (comma-separated string), `genre` (string) — snapshot for filtering
- Also serves as a repeat-scan cache: if a user scans the same ISBN twice, return stored result instead of calling Claude again

**UserSettings**
- `id`, `user_id` FK (cascade delete, unique)
- `threshold_strong` (default 0.90), `threshold_keep` (default 0.75), `threshold_maybe` (default 0.60)
- `goodreads_connected` (bool), `storygraph_connected` (bool), `updated_at`
- *Note: thresholds must be threaded into `score_book()` call — currently hardcoded in `weights.py`, needs updating when UserSettings is wired into scoring*

### Auth Rules
- JWT, HS256, `SECRET_KEY` from env (min 32 random bytes)
- 30-day expiry, no refresh tokens (MVP acceptable tradeoff — document that token leak = 30-day window)
- bcrypt password hashing
- Apple Sign In edge cases:
  - Email is **only sent by Apple on the user's first login** — all subsequent Apple auths only include `apple_user_id`. The `/auth/apple` endpoint must handle absent/empty email by looking up solely by `apple_user_id`.
  - If the email from Apple matches an existing email/password account: **link** the accounts (set `apple_user_id` on the existing User row) rather than creating a duplicate. Return a token for the existing account.
  - If no match found by `apple_user_id` or email: create a new User with `apple_user_id` set and `email` nullable.

### Architecture Change: Global State → Per-User DB
- `books.csv`, global `BOOKS`, global `AUTHOR_TABLE` are the pre-auth single-user architecture
- Post-auth: all scoring routes use per-user `UserBook` rows from the DB
- `get_author_features()` computed from `UserBook` filtered by `user_id` and `shelf='read'` with `my_rating > 0`
- No `AuthorProfile` table — compute on the fly from `UserBook` (same logic as `_build_author_table()`)

### Requirements to Add
```
python-jose[cryptography]
passlib[bcrypt]
sqlalchemy
psycopg2-binary
pydantic[email]
```

---

## Scoring Algorithm (v5 — Current)

See `algorithm_handoff.md` for the authoritative spec. Summary:

### Base Score
```
base = 0.50 × pred5 + 0.40 × author_signal + 0.10 × momentum_signal
```
- `author_signal` = author_avg / 5.0 if known, else pred5
- `momentum_signal` = momentum / 2.0 if > 0, else 0.5

### Risk Tags (binary 0/1, multiplicative penalty × 0.65)
- R1_Slow (0.09) · R2_Repetitive (0.11) · R3_VibeClash (0.07) · R4_HighConcept (0.13)
- R5_InaccessibleProse (0.07) · R6_WeakWriting (0.23) · R7_SeriesFatigue (0.12)
- R8_TooLong (0.00) · R9_ContentWarnings (0.00) · R10_TranslationQuality (0.00) · R11_DatedContent (0.00)
- R8–R11 are zeroed but kept in the tag set for future users — DO NOT REMOVE

### Reward Tags (binary 0/1, additive boost × 0.30)
- P1_Distinctive (0.12) · P2_Propulsive (0.15) · P3_Emotional (0.22) · P4_Clever (0.10)
- P5_Structure (0.08) · P6_Voice (0.10) · P7_Satisfying (0.23)

### Trope Tags (scored via TROPE_LIFTS in weights.py)
- 69 T_ tags, author-adjusted Bayesian shrinkage lifts
- Library-size gated: 0.00 < 100 books, 0.03 at 100–199, 0.06 at 200–299, 0.10 at ≥ 300

### Bonuses
- Critical reception: (crit / 3) × 0.12 (max +0.12 for award winner)
- Crowd divergence: +0.08 if author_avg − gr_avg > 0.5 and author known

### Verdict Thresholds
- **Strong Inkling**: ≥ 0.75
- **On the Fence**: 0.60–0.74
- **Hard Pass**: < 0.60

---

## Tagging System

### Flow
1. `/score` fetches metadata (Google Books first → Open Library fallback)
2. `get_goodreads_rating()` fetches the book's actual GR page via book ID (NOT search page)
3. `get_tags()` calls `claude-sonnet-4-6`, max_tokens=2048, returns JSON with all R/P/V/T/G tags
4. `format_tags()` converts to labeled display format
5. `score_book()` runs algorithm

### Critical Rules in Tagging Prompt
- **R6_WeakWriting**: ONLY fire on direct evidence of weak prose (reviews explicitly citing flat/forgettable writing). Never fire on low GR avg alone or divisive books.
- **R4_HighConcept**: Only if execution is the specific complaint, not just because a book is ambitious.
- **P7_Satisfying**: Strongest single predictor (+0.491 corr). DO NOT fire if reviews cite unsatisfying/abrupt ending.
- All tags are binary 0/1 (P3/P4 no longer graded)

### Goodreads Rating Scraping
- Uses `gr_find_book_id()` → fetches `/book/show/{id}` directly
- Old version scraped search page → matched wrong book's rating (returned 2.0 for Schwab) — fixed
- Rating source priority: Goodreads scrape → Open Library `ratings_average` → neutral 3.5 fallback
- Google Books `averageRating` is capped and discarded if ≥ 5.0 (inflated Play Store sample)
- When title + author are provided directly, `ol_search()` is still called for rating/cover/pages

---

## UI Screens

`splash → home → [scanner / identify / search] → loading → [result-strong / result-fence / result-pass] → recents → profile`

### Home Screen
- No standard header — full animated logo video as hero
- Floating gold profile avatar top-right
- Two plum tiles: "Scan a Book" + "Identify Cover" (gold text/icons, internal plum glow)
- Search bar (amber border, dark background)
- Recent Lookups: compact horizontal scroll strip (56px covers + verdict badge)
- Bottom nav: Home + Recents (gold icons, dot indicator on active)

### Result Screen
- Header: animated book video + Inkling wordmark (bottom-aligned)
- Hero: verdict-specific lightbulb video (220px wide, three versions)
- Large percentage number below bulb (64px, line-height 1.1)
- "% match" label
- Verdict text (38px italic serif)
- One-sentence summary
- Book cover + title/author card
- Tags card: Genre / Vibes / Why You'll Love It / Watch Out For

### Profile Screen
- Accessed via person icon (gold, `#df832e`) on all screens
- Shows full scoring model with all weights
- X button to close (not swipe)

### Embedded Videos (do not remove or reorder)
- Splash (~550KB b64)
- Full logo animation (~391KB b64) — home hero
- Header book animation (~810KB b64) — all non-home headers
- Strong Inkling bulb (~1.26MB b64)
- On the Fence bulb (~730KB b64)
- Hard Pass bulb (~470KB b64)

---

## iOS App Notes

- **Xcode project**: separate folder from `~/inkling-api/`
- **WKWebView URL**: `https://web-production-2c0f89.up.railway.app/app`
- **Certificate expiry**: 7 days (free Apple account) / 1 year ($99 paid Apple Developer)
- **"App no longer available"** = expired cert → reinstall via Xcode play button
- **Phone**: Marisa's Personal Phone, currently running iOS 26 beta
- **Paid developer account ($99/year)** needed for TestFlight and App Store

---

## Known Issues & Hard Rules

### WKWebView JavaScript Constraints
- **NEVER use `async` on `renderRecents()`** — it calls synchronous `getRecents()`, and the `async` keyword alone breaks WKWebView's JS parser even without any `await` inside. `renderHomeStrip()` IS async (uses `.catch()` at its call site) — keep it that way.
- The splash timeout must use `function(){}` not `async () => {}`: `setTimeout(function(){goTo('home');renderHomeStrip();}, 6000);`
- Core networking functions (`scoreBook`, `liveSearch`, `startCamera`, etc.) can and should use async/await — WKWebView supports it for fetch calls

### HTML Editing Rules
- File is 8.4MB — always use string replacement, never rewrite from scratch
- Embedded b64 data must stay in HTML tags, NEVER in script blocks
- After any edit verify screen count is still 9: splash, home, identify, scanner, search, loading, recents, result-strong, profile
- Always check for async on non-async functions after edits

### Scoring/Tagging Rules
- `max_tokens` for tagging: set to 2048 (full tag JSON with all 69 tropes needs room)
- Any weight change in `weights.py` → update profile page in `inkling_mobile.html`
- Any new tag → update: weights.py, score.py, main.py (get_tags prompt + format_tags), inkling_mobile.html (labelMap + profile page)

### Recurring False Positives to Watch
- **R6_WeakWriting** over-triggers: prompt explicitly guards against it but monitor. Worth 5–15pts swing.
- **P3_Emotional, P6_Voice, P1_Distinctive** over-fire at ~85–89% — intentionally weighted modestly
- **V5_Atmospheric** fires on ~70% of books — tagging prompt guards against it but monitor

### Data/Storage
- **No localStorage** — replaced with in-memory `RECENTS` array (Safari on iOS over HTTP blocks localStorage). Resets on reload — accepted tradeoff.
- Recents are also partially stored server-side via `/recents` endpoint but not fully wired

---

## Backlog (Priority Order)

1. **Auth + multi-user architecture** *(in sprint)*: `database.py`, `models.py`, `auth.py`, library upload endpoint, wire per-user data into scoring. See Auth Architecture section above.
2. **User Settings screen** *(in sprint)*: UI for `UserSettings` thresholds + `goodreads_connected`/`storygraph_connected`. Requires threading per-user thresholds into `score_book()`.
3. ~~**Result screen + profile UI**~~ *(done 2026-04-27)*: Tiered chips (Strong/Moderate/Soft opacity), positive tropes nested under Rewards, negative under Risks, neutral in Additional Tropes & Themes section. Profile updated with v5 tag weights and calibration indicator.
4. **Library data sync** *(decided, not built)*: Three options discussed — preference is Option B (Sync button with smart diff). Diff against existing UserBook rows, only tag new books. Prompt periodically: "Last synced 3 months ago. Update your algorithm?" Option C (push notification reminder every 3 months) pairs well with B.
5. **Upload error handling** *(spec decided, not built)*:
   - Wrong file type → "Please upload a CSV or Excel file"
   - Missing required columns → "We couldn't find a rating column. Here's what we need and how to map yours"
   - Empty or too small → "Only X books found — are you sure this is your full library?"
   - Encoding issues → auto-attempt UTF-8 and Latin-1 before failing
   - API rate limit mid-tagging → automatic retry with backoff, user never sees it
   - Single book fails to tag → skip and continue, flag for retry. Don't abort whole job.
   - API completely down → pause job, notify user "Processing paused — we'll resume automatically"
6. **Re-tag library**: Re-tag all books with new v5 tag model (R1–R11, P1–P7, T_ tropes). Required for scoring accuracy on the CSV fast-path.
7. **Apple Developer account ($99/year)**: Required for TestFlight beta and App Store. Eliminates 7-day cert expiry.
8. **"Add to Goodreads" button**: UI exists (soft plum → saturated plum + checkmark on tap), not wired. Needs Goodreads OAuth.
9. **Beta outreach**: One-pager written. Three paths: beta testing, guidance/expertise, collaboration. Ready to send when app is stable.
10. **App Store submission**: Requirements identified, not yet pursued.

---

## Working Style Notes

- Marisa provides direct, specific feedback — take ownership of errors, don't attribute to "previous sessions"
- Always read files completely before making changes
- Never remove existing features during edits — only add or modify
- When in doubt about a design decision, ask before building
- Prefer targeted string replacements over full rewrites
- After any Python change: syntax check before deploying
- Deploy command: `cd ~/inkling-api && railway up`
- Check logs: `railway logs`
