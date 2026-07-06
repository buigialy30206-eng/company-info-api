"""
Company Info API
Searches companies via Wikidata — free, no API keys, global coverage.
Returns: name, description, industry, employees, founded, HQ, website.
"""

from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

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


async def wikidata_search(query: str, limit: int = 5) -> list[dict]:
    """Search Wikidata for entities matching query."""
    async with httpx.AsyncClient(timeout=10, headers={"Accept": "application/json"}) as client:
        r = await client.get(WIKIDATA_API, params={
            "action": "wbsearchentities",
            "search": query,
            "language": "en",
            "format": "json",
            "limit": limit,
            "type": "item",
        })
        r.raise_for_status()
        return r.json().get("search", [])


async def wikidata_entity(qid: str) -> dict:
    """Get full entity data for a Q-ID."""
    async with httpx.AsyncClient(timeout=10, headers={"Accept": "application/json"}) as client:
        r = await client.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
        r.raise_for_status()
        data = r.json()
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
    industry = claim_value("P452")
    hq = claim_value("P159")
    website = claim_value("P856")
    country = claim_value("P17")

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
    results = await wikidata_search(q, limit)
    companies = []
    for r in results:
        entity = await wikidata_entity(r["id"])
        companies.append(parse_entity(entity))
    return companies


@app.get("/lookup", response_model=CompanyInfo)
async def lookup_company(
    q: str = Query(..., description="Company name — returns best match"),
):
    """Look up a single company — returns the best match."""
    results = await wikidata_search(q, 1)
    if not results:
        raise HTTPException(404, f"Company not found: {q}")
    entity = await wikidata_entity(results[0]["id"])
    return parse_entity(entity)
