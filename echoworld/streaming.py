"""基础SSE帧；事件级推送，不是模型Token流或断线续读。"""
import json


def sse_frame(event: str, payload: dict) -> str:
    if event not in {"start", "result", "done", "error"}:
        raise ValueError("未知事件")
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
