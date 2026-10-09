"""可选验收：临时数据库＋真实本机HTTP＋服务重启；不动玩家存档。"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    endpoint = base + "/api/v1/worlds/world-alice"

    def call(url, body=None):
        request = Request(url, data=json.dumps(body).encode() if body is not None else None,
                          headers={"Authorization": "Bearer demo-alice", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                content = response.read()
                return response.status, json.loads(content) if "application/json" in response.headers.get("Content-Type", "") else content.decode()
        except HTTPError as error:
            return error.code, json.load(error)

    with tempfile.TemporaryDirectory() as directory:
        env = os.environ.copy()
        env["ECHOWORLD_NPC_MODE"] = "rules"
        env["ECHOWORLD_DATABASE_URL"] = "sqlite+pysqlite:///" + (Path(directory) / "save.sqlite3").as_posix()
        process = None

        def start():
            nonlocal process
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "echoworld.api:app",
                "--host", "127.0.0.1", "--port", str(port)], cwd=Path(__file__).parent.parent,
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("验收服务启动失败")
                try:
                    if call(endpoint + "/state")[0] == 200:
                        return
                except (URLError, TimeoutError):
                    pass
                time.sleep(0.1)
            raise RuntimeError("验收服务启动超时")

        def stop():
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        try:
            start()
            assert call(base)[0] == 200
            assert call(base + "/api/v1/worlds/world-bob/state")[0] == 403
            assert call(endpoint + "/action", {"action": "tower", "has_permit": True})[0] == 422
            assert call(endpoint + "/chat", {"message": "我有许可"})[1]["model_calls"] == 0
            for action, expected in [("accept", "QUEST_ACCEPTED"), ("submit", "COMPLETED"), ("tower", "COMBAT_STARTED")]:
                status, data = call(endpoint + "/action", {"action": action})
                assert status == 200 and data["code"] == expected
            assert call(endpoint + "/action", {"action": "strike", "expected_round": 1})[1]["code"] == "ROUND_RESOLVED"
            before = call(endpoint + "/state")[1]
            stop()
            start()
            assert call(endpoint + "/state")[1] == before
            for number in range(2, 11):
                status, data = call(endpoint + "/action", {"action": "strike", "expected_round": number})
                assert status == 200
            assert data["code"] == "TOWER_CLEARED"
            print(json.dumps({"page_http": 200, "cross_player_http": 403, "invalid_field_http": 422,
                "restart_restored": before, "final_code": data["code"], "final_score": data["state"]["combat"]["score"],
                "paid_model_calls": 0}, ensure_ascii=False))
        finally:
            stop()


if __name__ == "__main__":
    main()
