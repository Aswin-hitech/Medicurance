from __future__ import annotations

from typing import Any, Mapping

from flask import jsonify


def api_response(
    *,
    data: Any = None,
    message: str = "",
    status_code: int = 200,
    error: str | None = None,
    meta: Mapping[str, Any] | None = None,
):
    payload: dict[str, Any] = {
        "success": 200 <= status_code < 400,
        "message": message,
    }
    if data is not None:
        payload["data"] = data
    if error:
        payload["error"] = error
    if meta:
        payload["meta"] = dict(meta)
    return jsonify(payload), status_code

