"""对已启动的服务做端到端冒烟验证。

    python scripts/smoke_http.py [port]
"""

from __future__ import annotations

import sys
import time

import httpx

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8000}"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(name)


def wait_job(client: httpx.Client, job_id: str, timeout: float = 180.0) -> dict:
    deadline = time.time() + timeout
    data: dict = {}
    while time.time() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["status"] in {"succeeded", "failed", "cancelled"}:
            return data
        time.sleep(0.2)
    raise TimeoutError(job_id)


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=120.0, trust_env=False) as c:
        print(f"目标：{BASE}")

        health = c.get("/api/health").json()
        check("健康检查", health["status"] == "ok", health["engine"])

        catalog = c.get("/api/catalog").json()
        check("音色库", len(catalog["voices"]) >= 4, f"{len(catalog['voices'])} 个")
        check("示例素材", bool(catalog["media"]), f"{len(catalog['media'])} 个")

        designed = c.post("/api/voices/design", json={"description": "低沉的中年男声，语速偏慢"})
        check("声音设计", designed.status_code == 201 and designed.json()["pitch"] < 1.0, designed.text[:60])
        voice_id = designed.json()["id"] if designed.status_code == 201 else catalog["voices"][0]["id"]

        preview = c.get(f"/api/voices/{voice_id}/preview")
        check("音色试听", preview.status_code == 200 and len(preview.content) > 5000, f"{len(preview.content):,} B")

        voices = c.get("/api/voices").json()["items"]
        dialogue = c.post(
            "/api/jobs/dialogue",
            json={
                "title": "冒烟 · 对白",
                "script": "[林夕] 你好。\n[周野] 你好啊。",
                "voice_map": {"林夕": voices[0]["id"], "周野": voices[1]["id"]},
                "default_voice_id": voices[0]["id"],
            },
        )
        check("创建对白任务", dialogue.status_code == 202, dialogue.text[:60])
        job = wait_job(c, dialogue.json()["job_id"])
        check("对白完成", job["status"] == "succeeded", job.get("error") or "")
        kinds = {a["kind"] for a in job.get("assets", [])}
        check("对白产物", {"audio", "srt"} <= kinds, str(sorted(kinds)))
        audio = next(a for a in job["assets"] if a["kind"] == "audio")
        check("音频可下载", len(c.get(f"/api/assets/{audio['id']}").content) > 10_000)

        media = catalog["media"][0]
        dubbing = c.post(
            "/api/jobs/dubbing",
            json={"title": "冒烟 · 视频配音", "media_id": media["id"], "script": "这是一段冒烟测试配音。", "voice_id": voice_id},
        )
        check("创建视频配音", dubbing.status_code == 202, dubbing.text[:60])
        job = wait_job(c, dubbing.json()["job_id"])
        check("视频配音完成", job["status"] == "succeeded", job.get("error") or "")
        video = next((a for a in job["assets"] if a["kind"] == "video"), None)
        check("成片可下载", bool(video) and len(c.get(f"/api/assets/{video['id']}").content) > 20_000)
        if video:
            check("时长对齐", abs((video.get("duration") or 0) - (job["output"].get("source_duration") or 0)) < 1.2,
                  f"{video.get('duration')} vs {job['output'].get('source_duration')}")

        book = c.post(
            "/api/jobs/audiobook",
            json={
                "title": "冒烟 · 有声书",
                "voice_id": voice_id,
                "chapters": [{"title": "第一章", "text": "第一章正文。"}, {"title": "第二章", "text": "第二章正文。"}],
            },
        )
        check("创建有声书", book.status_code == 202, book.text[:60])
        job = wait_job(c, book.json()["job_id"])
        check("有声书完成", job["status"] == "succeeded", job.get("error") or "")
        check("章节清单", any(a["kind"] == "chapters" for a in job.get("assets", [])))

        bad = c.post("/api/jobs/dialogue", json={"script": "  "})
        check("空剧本返回 422", bad.status_code == 422, str(bad.status_code))
        missing = c.post("/api/jobs/dubbing", json={"media_id": "nope", "script": "x", "voice_id": voice_id})
        check("素材不存在返回 404", missing.status_code == 404, str(missing.status_code))

        metrics = c.get("/api/metrics").json()
        check("指标可读", metrics["jobs"]["succeeded"] >= 3 and metrics["audio"]["seconds"] > 0)

    print()
    if failures:
        print(f"✗ 失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("✓ 全部冒烟检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
