"""临时真实HTTP：分享→独立CLI审核→跨玩家双守卫→重启→撤回。"""
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
    with tempfile.TemporaryDirectory(prefix="echoworld-evolution-") as directory:
        database = Path(directory) / "demo.sqlite3"
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            port = bound.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        environment = {**os.environ, "ECHOWORLD_NPC_MODE": "template", "ECHOWORLD_NARRATION_MODE": "template",
            "ECHOWORLD_REDIS_URL": "", "ECHOWORLD_MILVUS_URI": "",
            "ECHOWORLD_CHECKPOINT_PATH": str(Path(directory) / "checkpoints.sqlite3")}
        command = [sys.executable, "-X", "utf8", "-m", "echoworld.server", "--offline", "--port", str(port),
                   "--database", str(database), "--env-file", str(Path(directory) / "absent.env")]
        process = None

        def request(player, path, body=None, method=None):
            req = Request(url + "/api/v1/worlds/world-" + player + path,
                data=json.dumps(body).encode() if body is not None else None, method=method,
                headers={"Authorization": "Bearer demo-" + player, "Content-Type": "application/json"})
            try:
                with urlopen(req, timeout=10) as response:
                    raw = response.read().decode()
                    return response.status, raw if path.endswith("/stream") else json.loads(raw)
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
                    if request("alice", "/state")[0] == 200:
                        return
                except (URLError, TimeoutError):
                    pass
                time.sleep(.1)
            raise RuntimeError("TEMP_SERVER_TIMEOUT")

        def stop():
            nonlocal process
            if process and process.poll() is None:
                if os.name == "nt":
                    # Windows venv launcher可能另启Python子进程；只结束本次Popen的进程树。
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            process = None

        def admin(*args):
            result = subprocess.run([sys.executable, "-X", "utf8", "-m", "echoworld.evolution_admin",
                "--database", str(database), *args], cwd=Path(__file__).parent.parent,
                env=environment, capture_output=True, encoding="utf-8", timeout=15)
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)

        try:
            start()
            before = request("bob", "/state")[1]
            payload = {"npc_id": "venti", "topic": "quest", "suggestion": "希望其他新手也能看到标准任务指引",
                       "consent": True, "request_id": "sharing-example"}
            status, candidate = request("alice", "/knowledge/candidates", payload)
            assert status == 200 and candidate["status"] == "pending"
            assert request("bob", "/knowledge/candidates")[1]["candidates"] == []
            assert request("bob", "/knowledge/public")[1]["entries"] == []
            reviewed = admin("approve", candidate["id"], "--reviewer", "offline-verifier", "--note", "核验标准任务FAQ")
            assert reviewed["result"]["version"] == 1
            for npc in ("venti", "varka"):
                status, answer = request("bob", "/chat", {"npc_id": npc, "message": "任务攻略怎么做", "request_id": "qa"})
                assert status == 200 and "已审核公共提示" in answer["reply"]
                assert answer["public_knowledge_sources"][0]["version"] == 1
                assert "没有通行许可" in answer["rule_reply"]
                assert "希望其他新手" not in answer["reply"]
            assert request("bob", "/state")[1] == before
            for helpful in (True, False):
                assert request("bob", "/knowledge/public/faq_quest/feedback", {"version": 1, "helpful": helpful})[0] == 200
            report = admin("report")["result"]
            assert report["feedback"] == {"helpful": 0, "not_helpful": 1}
            stop()
            start()
            assert request("bob", "/knowledge/public")[1]["entries"][0]["version"] == 1
            stream = request("bob", "/chat/stream", {"npc_id": "varka", "message": "任务攻略", "request_id": "stream1"})[1]
            assert "event: done" in stream and "已审核公共提示" in stream
            assert request("alice", "/knowledge/candidates/" + candidate["id"], method="DELETE")[0] == 200
            assert request("bob", "/knowledge/public")[1]["entries"] == []
            old = request("bob", "/chat", {"npc_id": "varka", "message": "任务攻略怎么做", "request_id": "qa"})[1]
            assert old["reply_policy"] == "historical_public_faq_hidden"
            assert request("bob", "/state")[1] == before
            print(json.dumps({"ok": True, "mode": "actual_http_temporary_sql_offline",
                "checks": ["explicit_share_pending", "private_candidate_isolation", "cli_review_with_live_server",
                    "both_guards_cross_player_faq", "no_game_state_changes", "versioned_feedback_dedup",
                    "restart_persists_publication", "sse_verified_faq", "withdraw_immediate_visibility",
                    "old_receipt_hides_invalid_faq"], "paid_model_calls": 0}, ensure_ascii=False))
        finally:
            stop()


if __name__ == "__main__":
    main()
