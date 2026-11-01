import io
import json
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from core.chassis import AgentChassis
from core.config import settings
from core.llm import get_llm
from core.pipeline import run_audit, run_audit_from_extracted
from core.schemas import ExtractedBill

logger = logging.getLogger("claimguard")

load_dotenv()

app = FastAPI(
    title="ClaimGuard",
    description="Nigerian HMO/hospital bill claim auditor on Gemma 4 (agent-factory chassis)",
    version="0.1.0",
)

# allow_credentials + allow_origins=["*"] is invalid per CORS; reflect Vercel/local explicitly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://claimguard-red.vercel.app",
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=[
        "*",
        "X-Factory-API-Key",
        "X-Client-OpenRouter-Key",
        "X-Client-Cerebras-Key",
    ],
)

API_KEY_NAME = "X-Factory-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

CLAIMGUARD_PROFILES = (
    "extractor",
    "price_checker",
    "medical_logic",
    "fraud_scorer",
)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


async def verify_api_key(api_key: str = Depends(api_key_header)):
    master_key = os.getenv("FACTORY_MASTER_KEY") or settings.FACTORY_MASTER_KEY
    if not master_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfiguration: FACTORY_MASTER_KEY environment variable is not set.",
        )
    if not api_key or api_key != master_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials. Invalid or missing API key.",
        )


class AgentRoute(BaseModel):
    destination: str = Field(
        description=(
            "The exact profile name to route to. Must be exactly one of: "
            "'extractor', 'price_checker', 'medical_logic', 'fraud_scorer'."
        )
    )
    reasoning: str = Field(description="A brief 1-sentence explanation of why this agent was chosen.")


def _sniff_mime(data: bytes, filename: str = "") -> str:
    lower = (filename or "").lower()
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


def _resize_image(data: bytes, max_side: int = 1280, filename: str = "") -> tuple[bytes, str]:
    """Normalize to JPEG for Gemma vision; keep a correct mime type always."""
    fallback_mime = _sniff_mime(data, filename)
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")
        w, h = img.size
        scale = min(1.0, max_side / max(w, h))
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        logger.warning("Pillow resize failed (%s); passing original bytes as %s", e, fallback_mime)
        return data, fallback_mime


def _load_sample_sidecar(filename: str) -> Optional[ExtractedBill]:
    """Load matching data/sample_bills/<stem>.json when present."""
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    if not stem:
        return None
    sidecar = os.path.join("data", "sample_bills", f"{stem}.json")
    if not os.path.isfile(sidecar):
        return None
    with open(sidecar, encoding="utf-8") as f:
        return ExtractedBill.model_validate(json.load(f))


@app.get("/")
def service_info():
    """Render hosts the API only; the UI lives on Vercel."""
    return {
        "service": "ClaimGuard API",
        "status": "OK",
        "health": "/health",
        "docs": "/docs",
        "frontend": "https://claimguard-red.vercel.app",
    }


@app.get("/health")
def health_check():
    return {
        "status": "OK",
        "service": "ClaimGuard",
        "model": settings.LLM_MODEL,
        "profiles": list(CLAIMGUARD_PROFILES),
    }


@app.post("/supervisor", dependencies=[Depends(verify_api_key)])
async def factory_supervisor(user_payload: dict, request: Request):
    user_input = user_payload.get("input", "")
    client_openrouter_key = request.headers.get("X-Client-OpenRouter-Key")
    client_cerebras_key = request.headers.get("X-Client-Cerebras-Key")

    llm = get_llm(
        temperature=0,
        client_openrouter_key=client_openrouter_key,
        client_cerebras_key=client_cerebras_key,
    )

    # Offline / mock path: keyword route without structured LLM
    if not settings.LLM_API_KEY and not settings.LLM_FALLBACK_BASE_URL:
        low = (user_input or "").lower()
        if any(k in low for k in ("price", "tariff", "overcharge", "naira")):
            dest, reason = "price_checker", "Keyword route: pricing/tariff."
        elif any(k in low for k in ("diagnosis", "clinical", "medical", "fit")):
            dest, reason = "medical_logic", "Keyword route: clinical consistency."
        elif any(k in low for k in ("fraud", "score", "verdict", "reject")):
            dest, reason = "fraud_scorer", "Keyword route: fraud scoring."
        else:
            dest, reason = "extractor", "Default route: extraction."
        worker = AgentChassis(
            profile_name=dest,
            client_openrouter_key=client_openrouter_key,
            client_cerebras_key=client_cerebras_key,
        )
        try:
            worker_output = worker.run(user_input)
        except Exception as e:
            worker_output = f"Chassis ready ({dest}). Detail: {e}"
        return {
            "status": "Success",
            "supervisor": {"delegated_to": dest, "reasoning": reason},
            "worker_output": worker_output,
        }

    structured_router = llm.with_structured_output(AgentRoute, method="json_mode")
    routing_prompt = f"""You are the ClaimGuard Floor Supervisor.
Assign the request to exactly one specialist profile based on PRIMARY ACTION.

Respond with JSON keys:
- destination: one of {list(CLAIMGUARD_PROFILES)}
- reasoning: one sentence

Floor plan:
- extractor: OCR / read bill or prescription images into structured line items
- price_checker: compare items to sample Nigerian tariffs
- medical_logic: judge clinical fit of items vs diagnosis
- fraud_scorer: explain fraud score / verdict narrative

User Request: {user_input}"""

    try:
        decision = structured_router.invoke(routing_prompt)
        destination = decision.destination
        if destination not in CLAIMGUARD_PROFILES:
            destination = "extractor"
        worker = AgentChassis(
            profile_name=destination,
            client_openrouter_key=client_openrouter_key,
            client_cerebras_key=client_cerebras_key,
        )
        worker_output = worker.run(user_input)
        return {
            "status": "Success",
            "supervisor": {
                "delegated_to": destination,
                "reasoning": decision.reasoning,
            },
            "worker_output": worker_output,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supervisor Routing Failed: {str(e)}")


@app.post("/upload", dependencies=[Depends(verify_api_key)])
async def upload_document(file: UploadFile = File(...)):
    """
    Accept a bill image or text/csv/pdf note for the workspace.
    RAG/Chroma removed — files are acknowledged only (use POST /audit for claim audit).
    """
    filename = file.filename or "upload.bin"
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB).")
    return {
        "status": "Success",
        "filename": filename,
        "bytes": len(content),
        "message": (
            f"Received {filename}. Vector RAG is disabled in ClaimGuard; "
            "POST the image to /audit for a full claim audit."
        ),
    }


@app.post("/run/{agent_profile}", dependencies=[Depends(verify_api_key)])
async def execute_agent(agent_profile: str, user_payload: dict, request: Request):
    try:
        client_openrouter_key = request.headers.get("X-Client-OpenRouter-Key")
        client_cerebras_key = request.headers.get("X-Client-Cerebras-Key")
        factory_instance = AgentChassis(
            profile_name=agent_profile,
            client_openrouter_key=client_openrouter_key,
            client_cerebras_key=client_cerebras_key,
        )
        persona = factory_instance.profile.get("personality") or {}
        allocated_tools = factory_instance.profile.get("tools") or []
        user_input = user_payload.get("input", "")
        output = factory_instance.run(user_input)
        return {
            "status": "Success",
            "activated_profile": agent_profile,
            "meta": {
                "role": persona.get("role")
                or factory_instance.profile.get("role")
                or "Unknown Role",
                "allocated_tools_count": len(allocated_tools),
            },
            "output": output,
        }
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Profile '{agent_profile}' does not exist on this floor.",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _audit_upload(file: UploadFile) -> dict:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB).")

    lower = file.filename.lower()
    # JSON ground-truth path (offline demo / tests without vision LLM)
    if lower.endswith(".json"):
        data = json.loads(raw.decode("utf-8"))
        extracted = ExtractedBill.model_validate(data)
        report = run_audit_from_extracted(extracted)
        return report.model_dump()

    if not any(lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
        raise HTTPException(
            status_code=400,
            detail="Upload a bill image (.jpg/.png) or a sample .json ground-truth file.",
        )

    sidecar = _load_sample_sidecar(file.filename)

    # Offline / CI: no LLM configured → rules path from matching sample JSON
    if not settings.LLM_API_KEY and not settings.LLM_FALLBACK_BASE_URL and not settings.GROQ_API_KEY:
        if sidecar is not None:
            report = run_audit_from_extracted(sidecar, model="rules-only")
            return report.model_dump()
        raise HTTPException(
            status_code=503,
            detail="Vision LLM not configured and no matching sample JSON sidecar found.",
        )

    image_bytes, mime = _resize_image(raw, filename=file.filename)
    try:
        report = await run_audit(image_bytes, mime=mime)
        return report.model_dump()
    except Exception as e:
        logger.exception("Vision /audit failed for %s: %s", file.filename, e)
        # Demo never 500s on known sample photos — fall back to matching JSON.
        if sidecar is not None:
            report = run_audit_from_extracted(
                sidecar,
                model=f"{settings.LLM_MODEL}+json-fallback",
            )
            payload = report.model_dump()
            payload["vision_fallback"] = True
            payload["vision_error"] = str(e)[:300]
            return payload
        raise HTTPException(
            status_code=500,
            detail=f"Vision extract failed: {e}",
        )


@app.post("/audit", dependencies=[Depends(verify_api_key)])
async def audit_claim(file: UploadFile = File(...)):
    """POST multipart bill image → AuditReport JSON."""
    return await _audit_upload(file)


@app.post("/claimguard/audit", dependencies=[Depends(verify_api_key)])
async def audit_claim_alias(file: UploadFile = File(...)):
    """Alias matching docs/agent.md path."""
    return await _audit_upload(file)


