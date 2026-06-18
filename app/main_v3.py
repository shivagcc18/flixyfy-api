import json
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
TABLE = os.getenv("SERVING_TABLE", "media_serving_v7_final")

app = FastAPI(
    title="Flixyfy API V3",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://flixyfy-web.vercel.app",
        "https://flixyfy.com",
        "https://www.flixyfy.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- DB ----------------

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


# ---------------- HEALTH ----------------

@app.get("/api/v3/health")
def health():
    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {TABLE}")
        total = cur.fetchone()["total"]

        cur.execute(f"SELECT COUNT(*) AS ott FROM {TABLE} WHERE has_ott=1")
        ott = cur.fetchone()["ott"]

    finally:
        conn.close()

    return {
        "status": "ok",
        "table": TABLE,
        "movies": total,
        "ott": ott
    }


# ---------------- LANGUAGE ----------------

@app.get("/api/v3/language/{language_slug}")
def language(language_slug: str, limit: int = 50):
    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(f"""
            SELECT tmdb_id, title, slug, poster_url, release_year,
                   language_slug, rating, has_ott, ott_primary
            FROM {TABLE}
            WHERE language_slug = %s
            ORDER BY popularity_rank ASC
            LIMIT %s
        """, (language_slug, limit))

        rows = cur.fetchall()

    finally:
        conn.close()

    return {
        "language": language_slug,
        "count": len(rows),
        "items": rows
    }


# ---------------- SEARCH ----------------

@app.get("/api/v3/search")
def search(q: str, limit: int = 24):
    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(f"""
            SELECT tmdb_id, title, slug, poster_url, release_year, rating
            FROM {TABLE}
            WHERE LOWER(title) LIKE LOWER(%s)
            LIMIT %s
        """, (f"%{q}%", limit))

        rows = cur.fetchall()

    finally:
        conn.close()

    return {
        "query": q,
        "count": len(rows),
        "items": rows
    }
@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "flixyfy-api",
        "version": "v3",
        "docs": "/docs"
    }