from pathlib import Path

path = Path("app/domain_routes_v1.py")
text = path.read_text(encoding="utf-8")

start = text.index("def where_sql(")
end = text.index("def domain_card(", start)

new_func = r'''def where_sql(
    table_name: str,
    search_query: str = "",
    language: Optional[str] = None,
    year: Optional[int] = None,
):
    cols = table_columns(table_name)
    where = []
    params = []

    if search_query:
        title_clauses = []
        normalized_query = "".join(ch.lower() for ch in search_query if ch.isalnum())

        for col in ["title", "original_title", "wiki_title"]:
            if col in cols:
                title_clauses.append(
                    "("
                    f"LOWER(CAST({qident(col)} AS TEXT)) LIKE LOWER(%s) "
                    "OR "
                    f"regexp_replace(LOWER(CAST({qident(col)} AS TEXT)), '[^a-z0-9]+', '', 'g') LIKE %s"
                    ")"
                )
                params.append(f"%{search_query}%")
                params.append(f"%{normalized_query}%")

        if title_clauses:
            where.append("(" + " OR ".join(title_clauses) + ")")

    if language:
        lang = language.strip().lower()

        if "language_slug" in cols:
            where.append("LOWER(CAST(language_slug AS TEXT)) = LOWER(%s)")
            params.append(lang)
        elif "primary_language" in cols:
            where.append("LOWER(CAST(primary_language AS TEXT)) = LOWER(%s)")
            params.append(lang)
        elif "language" in cols:
            where.append("LOWER(CAST(language AS TEXT)) = LOWER(%s)")
            params.append(lang)

    if year:
        if "release_year" in cols:
            where.append("release_year = %s")
            params.append(year)
        elif "year" in cols:
            where.append("year = %s")
            params.append(year)

    return ("WHERE " + " AND ".join(where)) if where else "", params


'''

text = text[:start] + new_func + text[end:]

path.write_text(text, encoding="utf-8")

print("PATCHED: domain search now ignores punctuation like Spider-Man vs spiderman")