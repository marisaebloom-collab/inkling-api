from __future__ import annotations
# library.py — loads books.csv and builds lookup tables

import csv
import os
from datetime import datetime
from collections import defaultdict

# Column name mapping from your CSV headers to internal names
COL = {
    'book_id':             'Book Id',
    'title':               'Title',
    'author':              'Author',
    'my_rating':           'My Rating',
    'gr_avg':              'Average Rating',
    'shelf':               'Exclusive Shelf',
    'year_read':           'Year Read',
    'pages':               'Number of Pages',
    'pred5':               'Predicted 5★ Probability',
    'author_avg':          'Author Avg Rating',
    'momentum':            'Momentum Score',
    'critical_reception':  'Critical_Reception',
    'master_score':        'Master Decision Score',
    'bucket':              'Suggested Bucket',
    'g0':                  'G0_Genre',
    'g1':                  'G1_Subgenre',
    # Risk tags (v5)
    'R1_Slow':                     'R1_Slow',
    'R2_Repetitive':               'R2_Repetitive',
    'R3a_CharacterDisconnect':     'R3a_CharacterDisconnect',
    'R3b_VibeClash':               'R3b_VibeClash',
    'R4_HighConcept':              'R4_HighConcept',
    'R5_Dense':                    'R5_Dense',
    'R6_WeakWriting':              'R6_WeakWriting',
    'R7_SeriesFatigue':            'R7_SeriesFatigue',
    'R8_LowPayoff':                'R8_LowPayoff',
    'R9_UnconvincingRelationship': 'R9_UnconvincingRelationship',
    'R10_UnderdevelopedConcept':   'R10_UnderdevelopedConcept',
    'R11_LowSubstance':            'R11_LowSubstance',
    'R12_PoorCohesion':            'R12_PoorCohesion',
    'R13_EmptyIntensity':          'R13_EmptyIntensity',
    'R14_LowFantasyPayoff':        'R14_LowFantasyPayoff',
    'R15_FlatExecution':           'R15_FlatExecution',
    # Reward tags
    'P1_Distinctive':      'P1_Distinctive',
    'P2_Propulsive':       'P2_Propulsive',
    'P3_Emotional':        'P3_Emotional',
    'P4_Clever':           'P4_Clever',
    'P5_Structure':        'P5_Structure',
    'P6_Voice':            'P6_Voice',
    # Vibe tags
    'V1_Speculative':      'V1_Speculative',
    'V2_Dark':             'V2_Dark',
    'V3_Romantic':         'V3_Romantic',
    'V4_PlotDriven':       'V4_PlotDriven',
    'V5_Atmospheric':      'V5_Atmospheric',
    'V6_ShortOrStandalone':'V6_ShortOrStandalone',
}

RISK_TAGS   = [
    'R1_Slow', 'R2_Repetitive', 'R3a_CharacterDisconnect', 'R3b_VibeClash',
    'R4_HighConcept', 'R5_Dense', 'R6_WeakWriting', 'R7_SeriesFatigue',
    'R8_LowPayoff', 'R9_UnconvincingRelationship', 'R10_UnderdevelopedConcept',
    'R11_LowSubstance', 'R12_PoorCohesion', 'R13_EmptyIntensity',
    'R14_LowFantasyPayoff', 'R15_FlatExecution',
]
REWARD_TAGS = ['P1_Distinctive','P2_Propulsive','P3_Emotional',
               'P4_Clever','P5_Structure','P6_Voice']
VIBE_TAGS   = ['V1_Speculative','V2_Dark','V3_Romantic','V4_PlotDriven',
               'V5_Atmospheric','V6_ShortOrStandalone']
ALL_TAGS    = RISK_TAGS + REWARD_TAGS + VIBE_TAGS


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val, default=0):
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def load_library(csv_path: str) -> tuple[list[dict], dict]:
    """
    Returns:
        books       — list of dicts, one per row
        author_table — dict keyed by author name
    """
    books = []
    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get(COL['title'], '').strip():
                continue  # skip blank rows

            book = {
                'book_id':    row.get(COL['book_id'], '').strip(),
                'title':      row.get(COL['title'], '').strip(),
                'author':     row.get(COL['author'], '').strip(),
                'my_rating':  _safe_int(row.get(COL['my_rating'])),
                'gr_avg':     _safe_float(row.get(COL['gr_avg'])),
                'shelf':      row.get(COL['shelf'], '').strip(),
                'year_read':  _safe_int(row.get(COL['year_read'])),
                'pages':      _safe_int(row.get(COL['pages'])),
                'pred5':      _safe_float(row.get(COL['pred5'])),
                'author_avg': _safe_float(row.get(COL['author_avg'])),
                'momentum':   _safe_int(row.get(COL['momentum'])),
                'critical_reception': _safe_int(row.get(COL['critical_reception'])),
                'master_score': _safe_float(row.get(COL['master_score'])),
                'bucket':     row.get(COL['bucket'], '').strip(),
                'g0':         row.get(COL['g0'], '').strip(),
                'g1':         row.get(COL['g1'], '').strip(),
            }

            # Load all tags (new columns default to 0 if not in CSV)
            for tag in ALL_TAGS:
                book[tag] = _safe_int(row.get(COL[tag]))

            # Legacy migration: old CSV uses R3_VibeClash; map it to R3b_VibeClash
            if book['R3b_VibeClash'] == 0 and _safe_int(row.get('R3_VibeClash')):
                book['R3b_VibeClash'] = 1

            books.append(book)

    author_table = _build_author_table(books)
    return books, author_table


def _build_author_table(books: list[dict]) -> dict:
    data = defaultdict(list)
    for b in books:
        if b['shelf'] == 'read' and b['my_rating'] > 0:
            data[b['author']].append({
                'rating':    b['my_rating'],
                'year_read': b['year_read'],
            })

    table = {}
    for author, entries in data.items():
        ratings   = [e['rating'] for e in entries]
        years     = [e['year_read'] for e in entries if e['year_read']]
        now       = datetime.now().year
        most_recent = max(years) if years else 0
        years_ago   = now - most_recent if most_recent else 999

        table[author] = {
            'books_read':   len(ratings),
            'avg_rating':   round(sum(ratings) / len(ratings), 4),
            'best_rating':  max(ratings),
            'rate_4plus':   round(sum(1 for r in ratings if r >= 4) / len(ratings), 4),
            'rate_5star':   round(sum(1 for r in ratings if r == 5) / len(ratings), 4),
            'momentum':     2 if years_ago <= 2 else (1 if years_ago <= 5 else 0),
        }
    return table


def find_book(books: list[dict], title: str = None, isbn: str = None) -> dict | None:
    """Look up a book from the library by title (fuzzy) or ISBN."""
    if not title and not isbn:
        return None

    if title:
        title_lower = title.lower().strip()
        for b in books:
            if b['title'].lower().strip() == title_lower:
                return b
        # Partial match fallback
        for b in books:
            if title_lower in b['title'].lower():
                return b

    return None
