from __future__ import annotations

from datetime import datetime, timedelta, timezone

_SESSION_TTL = timedelta(hours=12)
_agent_sessions: dict[int, datetime] = {}


def authorize_agent_session(agent_tg_id: int) -> None:
    _agent_sessions[agent_tg_id] = datetime.now(timezone.utc) + _SESSION_TTL


def revoke_agent_session(agent_tg_id: int) -> None:
    _agent_sessions.pop(agent_tg_id, None)


def is_agent_session_active(agent_tg_id: int) -> bool:
    exp = _agent_sessions.get(agent_tg_id)
    if exp is None:
        return False
    if exp <= datetime.now(timezone.utc):
        _agent_sessions.pop(agent_tg_id, None)
        return False
    return True
