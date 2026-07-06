"""
Company Info API
Searches companies via Wikidata — free, no API keys, global coverage.
Returns: name, description, industry, employees, founded, HQ, website.
"""

import subprocess, json as _json
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Company Info API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

WIKIDATA_API = "https://www.wikidata.org/w/api.php"

PROPERTY_LABELS = {
    "P571": "founded",
    "P1128": "employees",
    "P1129": "employees",
    "P452": "industry",
    "P159": "headquarters",
    "P856": "website",
    "P17": "country",
    "P749": "parent_company",
    "P2139": "revenue",
    "P414": "stock_exchange",
}


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
    """Run curl and return JSON."""
    cmd = ["curl", "-s", "--connect-timeout", "8", "--max-time", "12", url]
    if params:
        from urllib.parse import urlencode
        cmd[-1] += "?" + urlencode(params)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"curl failed: {result.stderr[:100]}")
    return _json.loads(result.stdout)


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
    """Parse Wikidata entity into CompanyInfo."""
    labels = entity.get("labels", {})
    name = labels.get("en", {}).get("value", "Unknown")
    desc = entity.get("descriptions", {}).get("en", {}).get("value")

    claims = entity.get("claims", {})

    def claim_value(pid: str):
        if pid not in claims:
            return None
        v = claims[pid][0]["mainsnak"]["datavalue"]["value"]
        if isinstance(v, dict) and "id" in v:
            # Entity reference — get label from entity
            q = v["id"]
            return entity.get("entities",{}).get(q,{}).get("labels",{}).get("en",{}).get("value", q)
        if isinstance(v, dict) and "amount" in v:
            return int(float(v["amount"]))
        if isinstance(v, dict) and "time" in v:
            return v["time"][1:11]  # +2020-01-01 → 2020-01-01
        if isinstance(v, dict) and "text" in v:
            return v["text"]
        return str(v)[:100]

    employees = claim_value("P1128") or claim_value("P1129")
    founded = claim_value("P571")
    industry_v = claim_value("P452")
    hq_v = claim_value("P159")
    country_v = claim_value("P17")

    # Resolve labels for referenced entities (industry, HQ, country)
    # Try to get from entity if embedded, otherwise keep Q-ID
    def resolve_id(q): return entity.get("entities",{}).get(q,{}).get("labels",{}).get("en",{}).get("value", q) if q and isinstance(q,str) and q.startswith("Q") else q

    industry = resolve_id(industry_v)
    hq = resolve_id(hq_v)
    country = resolve_id(country_v)
    website = claim_value("P856")

    return CompanyInfo(
        name=name,
        description=desc,
        industry=industry,
        employees=employees,
        founded=founded,
        headquarters=hq,
        website=website,
        country=country,
        wikidata_id=entity.get("id"),
    )


@app.get("/health")
async def health():
    return {"status": "ok", "source": "Wikidata"}


@app.get("/")
async def root():
    return {"service": "Company Info API", "version": "1.0.0"}


@app.get("/search", response_model=list[CompanyInfo])
async def search_companies(
    q: str = Query(..., description="Company name, e.g. 'Apple', 'Google', 'Tesla'"),
    limit: int = Query(5, ge=1, le=10),
):
    """Search for companies by name."""
    results = wikidata_search(q, limit)
    companies = []
    for r in results:
        entity = wikidata_entity(r["id"])
        companies.append(parse_entity(entity))
    return companies


@app.get("/lookup", response_model=CompanyInfo)
async def lookup_company(
    q: str = Query(..., description="Company name — returns best match"),
):
    """Look up a single company — returns the best match."""
    results = wikidata_search(q, 1)
    if not results:
        raise HTTPException(404, f"Company not found: {q}")
    entity = wikidata_entity(results[0]["id"])
    return parse_entity(entity)
