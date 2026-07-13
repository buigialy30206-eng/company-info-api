"""
Company Info API
Searches companies via Wikidata — free, global coverage.
"""
import subprocess, json as _json, time, threading
from typing import Optional
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Company Info API", version="1.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

WIKIDATA_API = "https://www.wikidata.org/w/api.php"

_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 86400  # Company data changes rarely, cache 24h

class CompanyInfo(BaseModel):
    name: str
    description: Optional[str] = None
    industry: Optional[str] = None
    employees: Optional[int] = None
    founded: Optional[str] = None
    headquarters: Optional[str] = None
    website: Optional[str] = None
    country: Optional[str] = None
    wikidata_id: Optional[str] = None

def curl_get(url: str, params: dict = None) -> dict:
    url = url if not params else url + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    cmd = ["curl", "-s", "--connect-timeout", "6", "--max-time", "10", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        if r.returncode == 0 and r.stdout.strip():
            return _json.loads(r.stdout)
    except:
        pass
    return {}

def wikidata_search(query: str, limit: int = 5) -> list[dict]:
    data = curl_get(WIKIDATA_API, {
        "action": "wbsearchentities", "search": query,
        "language": "en", "format": "json", "limit": str(limit), "type": "item",
    })
    return data.get("search", [])

def wikidata_entity(qid: str) -> dict:
    data = curl_get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
    return data.get("entities", {}).get(qid, {})

def parse_entity(entity: dict) -> CompanyInfo:
    labels = entity.get("labels", {})
    name = labels.get("en", {}).get("value", "Unknown")
    desc = entity.get("descriptions", {}).get("en", {}).get("value")
    claims = entity.get("claims", {})

    def claim_value(pid: str):
        if pid not in claims:
            return None
        v = claims[pid][0]["mainsnak"]["datavalue"]["value"]
        if isinstance(v, dict) and "id" in v:
            return v["id"]
        if isinstance(v, dict) and "amount" in v:
            return int(float(v["amount"]))
        if isinstance(v, dict) and "time" in v:
            return v["time"][1:11]
        if isinstance(v, dict) and "text" in v:
            return v["text"]
        return str(v)[:100]

    def resolve_id(q):
        if q and isinstance(q, str) and q.startswith("Q"):
            return entity.get("entities", {}).get(q, {}).get("labels", {}).get("en", {}).get("value", q)
        return q

    return CompanyInfo(
        name=name,
        description=desc,
        industry=resolve_id(claim_value("P452")),
        employees=claim_value("P1128") or claim_value("P1129"),
        founded=claim_value("P571"),
        headquarters=resolve_id(claim_value("P159")),
        website=claim_value("P856"),
        country=resolve_id(claim_value("P17")),
        wikidata_id=entity.get("id"),
    )

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok", "source": "Wikidata", "cache_size": len(_cache)}

@app.get("/")
async def root():
    return {"service": "Company Info API", "version": "1.1.0", "related": ["Domain WHOIS API", "Email Validator API", "Places Search API"]}

@app.get("/search", response_model=list[CompanyInfo])
async def search_companies(
    q: str = Query(..., description="Company name, e.g. 'Apple', 'Google', 'Tesla'"),
    limit: int = Query(5, ge=1, le=10),
):
    results = wikidata_search(q, limit)
    companies = []
    for r in results:
        entity = wikidata_entity(r["id"])
        companies.append(parse_entity(entity))
    return companies

@app.get("/lookup", response_model=CompanyInfo)
async def lookup_company(q: str = Query(..., description="Company name — returns best match")):
    # Check cache
    with _cache_lock:
        entry = _cache.get(q.lower())
        if entry and time.time() - entry["ts"] < CACHE_TTL:
            return CompanyInfo(**entry["data"])

    results = wikidata_search(q, 1)
    if not results:
        raise HTTPException(404, f"Company not found: {q}")
    entity = wikidata_entity(results[0]["id"])
    result = parse_entity(entity)

    # Cache
    with _cache_lock:
        _cache[q.lower()] = {"data": result.model_dump(), "ts": time.time()}
        if len(_cache) > 500:
            oldest = min(_cache, key=lambda k: _cache[k]["ts"])
            del _cache[oldest]

    return result
