"""Production-ready AI agent for Day 12 Lab."""
import asyncio
import json
import logging
import signal
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_cost, get_usage, record_usage
from app.rate_limiter import check_rate_limit
from app.redis_store import redis_status
from app.session_store import append_message, create_session_id, delete_session, load_session
from utils.mock_llm import ask as llm_ask


logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)

START_TIME = time.time()
_is_ready = False
_is_shutting_down = False
_in_flight_requests = 0
_request_count = 0
_error_count = 0


def log_event(event: str, **fields) -> None:
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, default=str))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _is_ready, _is_shutting_down
    status = redis_status()
    if not status["ok"] and status["required"]:
        raise RuntimeError(
            f"Redis is required but unavailable: {status['error']}")

    _is_ready = True
    _is_shutting_down = False
    log_event(
        "startup",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        redis=status["backend"],
        instance=settings.instance_id,
    )

    yield

    _is_shutting_down = True
    _is_ready = False
    deadline = time.time() + settings.graceful_shutdown_timeout_seconds
    while _in_flight_requests > 0 and time.time() < deadline:
        await asyncio.sleep(0.1)
    log_event("shutdown", in_flight_requests=_in_flight_requests)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    global _in_flight_requests, _request_count, _error_count
    if _is_shutting_down and request.url.path != "/health":
        return JSONResponse(status_code=503, content={"detail": "Server is shutting down"})

    start = time.time()
    _in_flight_requests += 1
    _request_count += 1
    try:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        duration_ms = round((time.time() - start) * 1000, 1)
        log_event(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=duration_ms,
        )
        return response
    except Exception:
        _error_count += 1
        raise
    finally:
        _in_flight_requests -= 1


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field("default-user", min_length=1, max_length=80)
    session_id: str | None = Field(default=None, max_length=120)


class AskResponse(BaseModel):
    session_id: str
    user_id: str
    question: str
    answer: str
    model: str
    served_by: str
    storage: str
    usage: dict
    timestamp: str


@app.get("/", tags=["Info"])
def root():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "endpoints": {
            "ask": "POST /ask",
            "history": "GET /sessions/{session_id}/history",
            "health": "GET /health",
            "ready": "GET /ready",
            "metrics": "GET /metrics",
        },
    }


@app.post("/ask", response_model=AskResponse, tags=["Agent"])
async def ask_agent(body: AskRequest, request: Request, _auth_user: str = Depends(verify_api_key)):
    user_id = body.user_id or _auth_user
    session_id = body.session_id or create_session_id()

    rate = check_rate_limit(user_id)
    input_tokens = max(1, len(body.question.split()) * 2)
    estimated_input_cost = estimate_cost(input_tokens, 0)
    check_budget(user_id, estimated_input_cost)

    append_message(session_id, "user", body.question)
    log_event(
        "agent_call",
        user_id=user_id,
        session_id=session_id,
        q_len=len(body.question),
        client=str(request.client.host) if request.client else "unknown",
    )

    answer = llm_ask(body.question)
    output_tokens = max(1, len(answer.split()) * 2)
    check_budget(user_id, estimate_cost(0, output_tokens))
    append_message(session_id, "assistant", answer)
    usage = record_usage(user_id, input_tokens, output_tokens)
    usage["rate_limit_remaining"] = rate["remaining"]

    storage = redis_status()["backend"]
    return AskResponse(
        session_id=session_id,
        user_id=user_id,
        question=body.question,
        answer=answer,
        model=settings.llm_model,
        served_by=settings.instance_id,
        storage=storage,
        usage=usage,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/sessions/{session_id}/history", tags=["Agent"])
def get_history(session_id: str, _auth_user: str = Depends(verify_api_key)):
    session = load_session(session_id)
    history = session.get("history", [])
    if not history:
        raise HTTPException(
            status_code=404, detail="Session not found or expired")
    return {"session_id": session_id, "messages": history, "count": len(history)}


@app.delete("/sessions/{session_id}", tags=["Agent"])
def remove_session(session_id: str, _auth_user: str = Depends(verify_api_key)):
    delete_session(session_id)
    return {"deleted": session_id}


@app.get("/health", tags=["Operations"])
def health():
    status = redis_status()
    return {
        "status": "ok" if status["ok"] else "degraded",
        "version": settings.app_version,
        "environment": settings.environment,
        "instance_id": settings.instance_id,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "in_flight_requests": _in_flight_requests,
        "checks": {
            "llm": "mock" if not settings.openai_api_key else settings.llm_model,
            "redis": status,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready", tags=["Operations"])
def ready():
    status = redis_status()
    if not _is_ready or _is_shutting_down:
        raise HTTPException(status_code=503, detail="Application is not ready")
    if not status["ok"] and status["required"]:
        raise HTTPException(status_code=503, detail="Redis is not ready")
    return {
        "ready": True,
        "instance_id": settings.instance_id,
        "storage": status["backend"],
    }


@app.get("/metrics", tags=["Operations"])
def metrics(user_id: str = "default-user", _auth_user: str = Depends(verify_api_key)):
    return {
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "error_count": _error_count,
        "in_flight_requests": _in_flight_requests,
        "budget": get_usage(user_id),
    }


def _handle_signal(signum, _frame) -> None:
    global _is_shutting_down, _is_ready
    _is_shutting_down = True
    _is_ready = False
    log_event("SIGTERM", signum=signum, in_flight_requests=_in_flight_requests)


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


if __name__ == "__main__":
    log_event("serve", host=settings.host, port=settings.port)
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        timeout_graceful_shutdown=settings.graceful_shutdown_timeout_seconds,
    )
