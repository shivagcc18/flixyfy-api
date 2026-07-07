import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_env_file(path):
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(".env")
load_env_file(".env.local")

from fastapi.testclient import TestClient

from app.domain_routes_v1 import (
    historical_movies_patched_v1,
    historical_person_detail_patched_v1,
)
from app.main_v3 import app


def assert_nonzero_historical(name, **kwargs):
    result = historical_movies_patched_v1(page=1, limit=12, **kwargs)
    total = int(result.get("total") or 0)
    count = len(result.get("items") or [])
    print(f"{name}: total={total} items={count}")
    if total <= 0 or count <= 0:
        raise AssertionError(f"{name} expected nonzero total/items")
    return result


def assert_person_total(slug, expected_total):
    result = historical_person_detail_patched_v1(slug, page=1, limit=12)
    total = int(result.get("total") or 0)
    count = len(result.get("items") or [])
    print(f"person {slug}: total={total} items={count}")
    if total != expected_total:
        raise AssertionError(f"{slug} expected total={expected_total}, got {total}")
    if count <= 0:
        raise AssertionError(f"{slug} expected movie rows")


def assert_movie_detail(client, slug):
    response = client.get(f"/api/v3/movie/{slug}")
    print(f"movie {slug}: status={response.status_code}")
    if response.status_code != 200:
        raise AssertionError(f"{slug} expected HTTP 200, got {response.status_code}")
    data = response.json()
    if not data.get("title") or not data.get("slug"):
        raise AssertionError(f"{slug} missing title/slug in response")


def main():
    assert_nonzero_historical("provider_youtube", provider="youtube")
    assert_nonzero_historical("te_provider_youtube", language="te", provider="youtube")
    assert_nonzero_historical("te_availability_youtube", language="te", availability="youtube")
    assert_nonzero_historical("te_availability_ott", language="te", availability="ott")
    assert_nonzero_historical("te_has_ott_1", language="te", has_ott="1")

    assert_person_total("ntr", 207)
    assert_person_total("jaishankar", 291)
    assert_person_total("dharmendra", 235)

    client = TestClient(app)
    for slug in ("a-aa-2016", "36-china-town-2006", "3-deewarein-2003"):
        assert_movie_detail(client, slug)

    print("SMOKE_PASS")


if __name__ == "__main__":
    main()
