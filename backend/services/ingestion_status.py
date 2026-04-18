from datetime import datetime, timezone
from threading import Lock

_lock = Lock()
_state = {
    "active": False,
    "agent_name": None,
    "source": None,
    "started_at": None,
    "last_result": None,
}


def start(agent_name: str, source: str):
    with _lock:
        _state["active"] = True
        _state["agent_name"] = agent_name
        _state["source"] = source
        _state["started_at"] = datetime.now(timezone.utc).isoformat()
        _state["last_result"] = None


def finish(result: dict):
    with _lock:
        _state["active"] = False
        _state["last_result"] = {
            **result,
            "agent_name": _state["agent_name"],
            "source": _state["source"],
            "started_at": _state["started_at"],
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        _state["started_at"] = None


def get_status() -> dict:
    with _lock:
        return dict(_state)
