"""In-memory conversation state manager for MTProto bot."""

from typing import Any, Optional


class StateManager:
    """Manages multi-step wizard states (registration, skin upload, etc.) per user."""

    def __init__(self) -> None:
        self._states: dict[int, str] = {}
        self._data: dict[int, dict[str, Any]] = {}

    def set_state(self, user_id: int, state: Optional[str]) -> None:
        if state is None:
            self._states.pop(user_id, None)
            self._data.pop(user_id, None)
        else:
            self._states[user_id] = state
            if user_id not in self._data:
                self._data[user_id] = {}

    def get_state(self, user_id: int) -> Optional[str]:
        return self._states.get(user_id)

    def update_data(self, user_id: int, **kwargs: Any) -> None:
        if user_id not in self._data:
            self._data[user_id] = {}
        self._data[user_id].update(kwargs)

    def get_data(self, user_id: int) -> dict[str, Any]:
        return self._data.get(user_id, {})

    def clear(self, user_id: int) -> None:
        self.set_state(user_id, None)


state_manager = StateManager()
