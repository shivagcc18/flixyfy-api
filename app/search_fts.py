import sqlite3
from data_factory.config.settings import DB_PATH


def search(query: str, limit: int = 20):

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # -----------------------------
    # SAFETY CHECK (IMPORTANT)
    # -----------------------------
    cursor.execute("""
        SELECT name 
        FROM sqlite_master 
        WHERE type='table' AND name='content_fts'
    """)

    if not cursor.fetchone():
        conn.close()
        return {
            "error": "FTS index not built. Run rebuild_index first."
        }

    # -----------------------------
    # SEARCH QUERY
    # -----------------------------
    cursor.execute("""
        SELECT 
            content_id,
            title,
            overview,
            genre,
            language
        FROM content_fts
        WHERE content_fts MATCH ?
        LIMIT ?
    """, (query, limit))

    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "content_id": r[0],
            "title": r[1],
            "overview": r[2],
            "genre": r[3],
            "language": r[4]
        }
        for r in rows
    ]