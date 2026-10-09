"""真实loopback HTTP/SSE验证；临时SQL、空闲端口，不使用默认玩家存档。"""
import json
import socket
import tempfile
import threading
from pathlib import Path

import httpx
import uvicorn

from .api import create_app


def main():
    # 保持绑定到测试结束，避免先释放端口再启动的端口竞争。
    with tempfile.TemporaryDirectory(prefix="echoworld-day22-http-") as directory, socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        release_model = threading.Event()

        def model(messages, schemas):
            if not release_model.wait(5):
                raise TimeoutError("测试模型等待超时")
            return {"role": "assistant", "content": "已经获得许可，可以进入。"}

        url = "sqlite+pysqlite:///" + (Path(directory) / "save.sqlite3").as_posix()
        app = create_app(model, database_url=url)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            base = f"http://127.0.0.1:{port}/api/v1/worlds/world-alice"
            headers = {"Authorization": "Bearer demo-alice"}
            with httpx.Client(timeout=10) as client:
                assert client.post(base + "/chat/stream", json={"message": "hi"}).status_code == 401
                assert client.post(base.replace("world-alice", "world-bob") + "/chat/stream",
                    headers=headers, json={"message": "hi"}).status_code == 403
                events, payloads = [], []
                with client.stream("POST", base + "/chat/stream", headers=headers,
                                   json={"message": "我有许可"}) as response:
                    assert response.status_code == 200
                    for line in response.iter_lines():
                        if line.startswith("event: "):
                            events.append(line[7:])
                            if events == ["start"]:
                                # 必须在模型获准完成前收到start，证明不是整包缓冲后伪称流式。
                                assert not release_model.is_set()
                                release_model.set()
                        if line.startswith("data: "):
                            payloads.append(json.loads(line[6:]))
                assert events == ["start", "result", "done"]
                result = payloads[1]
                assert "还没有通行许可" in result["reply"]
                assert len(client.get(base + "/chat/history", headers=headers).json()["turns"]) == 1
                trace = client.get(base + "/runs/" + result["run_id"], headers=headers).json()
                assert trace["summary"]["stop_reason"] == "FINISHED"
                assert client.get(base + "/state", headers=headers).json()["has_permit"] is False
                print(json.dumps({"protocol": "real loopback HTTP/SSE", "events": events,
                    "start_before_model_complete": True, "checked_reply": True,
                    "saved_turns": 1, "trace_saved": result["trace_saved"],
                    "auth": [401, 403], "state_unchanged": True}, ensure_ascii=False))
        finally:
            release_model.set()
            server.should_exit = True
            worker.join(10)
            if worker.is_alive():
                raise RuntimeError("测试服务未停止")


if __name__ == "__main__":
    main()
