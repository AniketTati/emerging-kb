"""FastAPI dependencies."""

from __future__ import annotations

from kb.logging import workspace_id_var


def current_workspace_id() -> str:
    """Resolve the workspace_id for the current request from the contextvar
    populated by `WorkspaceMiddleware`.
    """
    ws_id = workspace_id_var.get()
    if ws_id is None:  # middleware not mounted — programmer error
        raise RuntimeError("workspace_id contextvar unset; mount WorkspaceMiddleware")
    return ws_id
