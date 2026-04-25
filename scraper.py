# scraper.py — builds author history table from Goodreads CSV export

import csv
from datetime import datetime
from collections import defaultdict


def build_author_table_from_csv(csv_path: str) -> dict:
    """
    Read Goodreads export CSV and build author history table.
    Only uses books on the 'read' shelf with a rating > 0.
    """
    books = []
    try:
        with open(csv_path, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                title  = row.get('Title', '').strip()
                author = row.get('Author', '').strip()
                shelf  = row.get('Exclusive Shelf', '').strip()
                if not title or not author or shelf != 'read':
                    continue
                try:
                    rating = int(float(row.get('My Rating', 0)))
                except (ValueError, TypeError):
                    rating = 0
                try:
                    year_read = int(float(row.get('Year Read', 0)))
                except (ValueError, TypeError):
                    year_read = 0
                if rating > 0:
                    books.append({
                        'author':    author,
                        'rating':    rating,
                        'year_read': year_read,
                    })
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return {}

    print(f"Read {len(books)} rated books from CSV")
    return _build_table(books)


def _build_table(books: list) -> dict:
    data = defaultdict(list)
    for b in books:
        data[b['author']].append({
            'rating':    b['rating'],
            'year_read': b['year_read'],
        })

    table = {}
    now = datetime.now().year

    for author, entries in data.items():
        ratings     = [e['rating'] for e in entries]
        years       = [e['year_read'] for e in entries if e['year_read']]
        most_recent = max(years) if years else 0
        years_ago   = (now - most_recent) if most_recent else 999

        table[author] = {
            'books_read':  len(ratings),
            'avg_rating':  round(sum(ratings) / len(ratings), 4),
            'best_rating': max(ratings),
            'rate_4plus':  round(sum(1 for r in ratings if r >= 4) / len(ratings), 4),
            'rate_5star':  round(sum(1 for r in ratings if r == 5) / len(ratings), 4),
            'momentum':    2 if years_ago <= 2 else (1 if years_ago <= 5 else 0),
        }

    print(f"Built author table with {len(table)} authors")
    return table
