"""
UW Internal App — FastAPI backend.
Endpoints:
  POST /lookup — body: { "identifier_type": "app_id"|"msid"|"wp_account_id", "value": "..." }
  GET  /uw/{app_id} — get cached UW result for app_id (optional ?run_id=)
  GET  /health — health check
"""
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .profile import profile_from_app_record, profile_from_trino_row
from .resolve import resolve
from .trino_client import get_full_profile, is_configured as trino_configured
from .uw_cache import get_uw_for_app

app = FastAPI(
    title="UW Internal App",
    description="App profile + underwriting check by app_id, msid, or WP account id",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Optional: restrict to internal network / API key (add middleware or dependency)


class LookupRequest(BaseModel):
    identifier_type: str  # "app_id" | "msid" | "wp_account_id"
    value: str


@app.post("/lookup")
def lookup(req: LookupRequest):
    """
    Resolve identifier to app, then return app profile (transposed) + UW check.
    """
    identifier_type = (req.identifier_type or "").strip().lower()
    if identifier_type not in ("app_id", "msid", "wp_account_id"):
        raise HTTPException(400, "identifier_type must be app_id, msid, or wp_account_id")

    app_record = resolve(identifier_type, req.value)
    if not app_record:
        raise HTTPException(404, f"No app found for {identifier_type}={req.value!r}")

    app_id = app_record.get("app_id")
    # Live Trino: use full profile when configured; else transposed app record
    if app_id and trino_configured():
        profile_row = get_full_profile(app_id)
        app_profile = profile_from_trino_row(profile_row) if profile_row else profile_from_app_record(app_record)
    else:
        app_profile = profile_from_app_record(app_record)
    uw = get_uw_for_app(app_id) if app_id else None

    return {
        "app_id": app_id,
        "app_name": app_record.get("app_name"),
        "app_profile": app_profile,
        "uw": {
            "verdict": uw.get("verdict") if uw else None,
            "reasoning": uw.get("reasoning") if uw else None,
            "app_summary": uw.get("app_summary") if uw else None,
            "step1_what_sold": uw.get("step1_what_sold") if uw else None,
            "step2_comparison": uw.get("step2_comparison") if uw else None,
            "non_compliant_subcategories": uw.get("non_compliant_subcategories") if uw else None,
        }
        if uw
        else None,
    }


@app.get("/uw/{app_id}")
def get_uw(app_id: str, run_id: Optional[str] = None):
    """Get cached underwriting result for app_id. Optional run_id to limit to one run."""
    uw = get_uw_for_app(app_id, run_id=run_id)
    if not uw:
        raise HTTPException(404, f"No UW conclusion found for app_id={app_id!r}")
    return uw


@app.get("/health")
def health():
    return {"status": "ok", "trino": "live" if trino_configured() else "stub"}
