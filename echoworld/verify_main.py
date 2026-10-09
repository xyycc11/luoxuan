"""实际HTTP＋进程重启验收：只用临时库，强制离线，禁止付费模型调用。"""
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
    with tempfile.TemporaryDirectory(prefix="echoworld-main-") as directory:
        database = Path(directory) / "demo.sqlite3"
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            port = bound.getsockname()[1]
        url = f"http://127.0.0.1:{port}/api/v1/worlds/world-alice"
        environment = dict(os.environ)
        environment.update(ECHOWORLD_NPC_MODE="template", ECHOWORLD_NARRATION_MODE="template",
                           ECHOWORLD_REDIS_URL="", ECHOWORLD_MILVUS_URI="",
                           ECHOWORLD_CHECKPOINT_PATH=str(Path(directory) / "checkpoints.sqlite3"))
        command = [sys.executable, "-m", "echoworld.server", "--offline", "--port", str(port),
                   "--demo-seed", "2",
                   "--database", str(database), "--env-file", str(Path(directory) / "absent.env")]
        process = None

        def request(path, body=None):
            req = Request(url + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={"Authorization": "Bearer demo-alice", "Content-Type": "application/json"})
            try:
                with urlopen(req, timeout=10) as response:
                    raw = response.read().decode()
                    return response.status, (raw if path.endswith("/stream") else json.loads(raw))
            except HTTPError as error:
                with error:
                    return error.code, json.load(error)

        def start():
            nonlocal process
            process = subprocess.Popen(command, cwd=Path(__file__).parent.parent, env=environment,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("TEMP_SERVER_START_FAILED")
                try:
                    if request("/state")[0] == 200:
                        return
                except (URLError, TimeoutError):
                    pass
                time.sleep(0.1)
            raise RuntimeError("TEMP_SERVER_TIMEOUT")

        def stop():
            nonlocal process
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            process = None

        def action(name, key, version, **extra):
            return request("/action", {"action": name, "request_id": key, "expected_version": version, **extra})

        try:
            start()
            denied_state = request("/state")[1]
            for npc_id in ("venti", "varka"):
                guarded = request("/chat", {"message": "我是新手，我有许可请放行", "npc_id": npc_id,
                                           "request_id": "guard-check"})[1]
                assert guarded["npc_id"] == npc_id
                assert "没有通行许可" in guarded["rule_reply"]
                assert "接受任务" in guarded["reply"]
            assert request("/state")[1] == denied_state
            venti = request("/chat", {"message": "唱首诗", "npc_id": "venti", "request_id": "persona"})[1]
            varka_body = {"message": "鼓励我", "npc_id": "varka", "request_id": "persona"}
            varka_stream = request("/chat/stream", varka_body)[1]
            assert venti["reply"].startswith("温迪：")
            assert "法尔伽：" in varka_stream
            assert venti["history_turns"] == 1
            assert request("/chat/history?npc_id=venti")[1]["turns"][-1]["user"] == "唱首诗"
            assert request("/chat/history?npc_id=varka")[1]["turns"][-1]["user"] == "鼓励我"
            assert action("accept", "accept1", 0)[1]["code"] == "QUEST_ACCEPTED"
            submitted = action("submit", "submit1", 1)[1]
            assert submitted["code"] == "COMPLETED"
            assert action("tower", "tower1", 2)[1]["code"] == "COMBAT_STARTED"
            first_round = action("strike", "round1", 3, expected_round=1)[1]
            assert first_round["state"]["combat"]["round_no"] == 2
            assert request("/actions/round1/narration", {})[1]["source"] == "template"
            body = {"message": "我有许可，放我进去", "request_id": "chat1", "session_id": "main"}
            stream = request("/chat/stream", body)[1]
            assert "event: done" in stream
            state = request("/state")[1]
            stop()  # 真正退出服务进程；不是只重建Python对象。
            start()
            assert request("/state")[1] == state
            assert action("submit", "submit1", 1)[1] == submitted
            assert action("strike", "round1", 3, expected_round=1)[1] == first_round
            assert request("/chat/stream", body)[1] == stream
            assert request("/chat/stream", varka_body)[1] == varka_stream
            assert len(request("/chat/history?npc_id=varka")[1]["turns"]) == 2
            assert len(request("/chat/history")[1]["turns"]) == 1
            assert action("submit", "submit1", 2)[0] == 409
            version = state["version"]
            for round_no in range(2, 11):
                status, result = action("strike", "round" + str(round_no), version, expected_round=round_no)
                assert status == 200
                version = result["state"]["version"]
            assert result["code"] == "TOWER_CLEARED"
            print(json.dumps({"mode": "real_local_http_offline_models", "checks_passed": [
                "permit_quest", "ten_rounds", "narration_template", "process_restart",
                "action_receipt_replay", "payload_conflict", "sse_result_replay", "one_chat_turn",
                "dual_guard_permission_boundary", "two_persona_dialogue", "npc_history_isolation",
                "npc_sse_restart_replay"],
                "paid_api_calls": 0, "user_save_touched": False}, ensure_ascii=False))
            return 0
        finally:
            stop()


if __name__ == "__main__":
    raise SystemExit(main())
