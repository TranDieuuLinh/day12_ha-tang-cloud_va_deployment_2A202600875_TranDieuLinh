"""Conversation history storage backed by Redis."""
import json
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.redis_store import get_redis


def _key(session_id: str) -> str:
    return f"session:{session_id}"


def load_session(session_id: str) -> dict:
    data = get_redis().get(_key(session_id))
    return json.loads(data) if data else {"history": []}


def save_session(session_id: str, session: dict) -> None:
    get_redis().setex(_key(session_id), settings.session_ttl_seconds, json.dumps(session))


def append_message(session_id: str, role: str, content: str) -> list[dict]:
    session = load_session(session_id)
    history = session.get("history", [])
    history.append(
        {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    history = history[-settings.max_history_messages:]
    session["history"] = history
    save_session(session_id, session)
    return history


def create_session_id() -> str:
    return str(uuid.uuid4())


def delete_session(session_id: str) -> None:
    get_redis().delete(_key(session_id))
