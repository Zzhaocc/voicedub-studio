"""HTTP 接口测试。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _wait_job(client: TestClient, job_id: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["status"] in {"succeeded", "failed", "cancelled"}:
            return data
        time.sleep(0.2)
    raise AssertionError(f"任务未结束：{job_id}")


def test_health_and_catalog(client: TestClient) -> None:
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["engine"] == "local-engine"

    catalog = client.get("/api/catalog").json()
    assert len(catalog["voices"]) >= 4
    assert catalog["media"], "应有一段示例视频"
    assert catalog["demo"]["dialogue"]
    assert catalog["demo"]["audiobook"]["chapters"]


def test_voice_design_and_preview(client: TestClient) -> None:
    resp = client.post("/api/voices/design", json={"description": "低沉的中年男声，语速偏慢"})
    assert resp.status_code == 201, resp.text
    voice = resp.json()
    assert voice["kind"] == "designed"
    assert voice["pitch"] < 1.0
    assert voice["design"]["matched_keywords"]

    preview = client.get(f"/api/voices/{voice['id']}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("audio/wav")
    assert len(preview.content) > 5000

    assert client.delete(f"/api/voices/{voice['id']}").status_code == 204


def test_preset_cannot_be_deleted(client: TestClient) -> None:
    voices = client.get("/api/voices").json()["items"]
    preset = next(v for v in voices if v["kind"] == "preset")
    assert client.delete(f"/api/voices/{preset['id']}").status_code == 409


def test_clone_voice_without_upload(client: TestClient) -> None:
    resp = client.post("/api/voices/clone", json={"name": "接口克隆音色"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["kind"] == "cloned"
    assert data["analysis"]["f0"] > 50
    client.delete(f"/api/voices/{data['id']}")


def test_dialogue_job_via_api(client: TestClient) -> None:
    voices = client.get("/api/voices").json()["items"]
    payload = {
        "title": "接口对白",
        "script": "[林夕] 你好。\n[周野] 你好啊。",
        "voice_map": {"林夕": voices[0]["id"], "周野": voices[1]["id"]},
        "default_voice_id": voices[0]["id"],
    }
    created = client.post("/api/jobs/dialogue", json=payload)
    assert created.status_code == 202, created.text
    job = _wait_job(client, created.json()["job_id"])
    assert job["status"] == "succeeded", job.get("error")
    kinds = {asset["kind"] for asset in job["assets"]}
    assert {"audio", "srt"} <= kinds
    assert set(job["output"]["speakers"]) == {"林夕", "周野"}


def test_dubbing_job_via_api(client: TestClient) -> None:
    media = client.get("/api/media").json()["items"][0]
    voice = client.get("/api/voices").json()["items"][0]
    created = client.post(
        "/api/jobs/dubbing",
        json={
            "title": "接口配音",
            "media_id": media["id"],
            "script": "这是一段接口测试配音。",
            "voice_id": voice["id"],
        },
    )
    assert created.status_code == 202, created.text
    job = _wait_job(client, created.json()["job_id"])
    assert job["status"] == "succeeded", job.get("error")
    video = next(a for a in job["assets"] if a["kind"] == "video")
    resp = client.get(f"/api/assets/{video['id']}?inline=1")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("video/mp4")
    assert len(resp.content) > 20_000


def test_audiobook_job_via_api(client: TestClient) -> None:
    voice = client.get("/api/voices").json()["items"][0]
    created = client.post(
        "/api/jobs/audiobook",
        json={
            "title": "接口有声书",
            "voice_id": voice["id"],
            "chapters": [
                {"title": "第一章", "text": "第一章正文。"},
                {"title": "第二章", "text": "第二章正文。"},
            ],
        },
    )
    assert created.status_code == 202, created.text
    job = _wait_job(client, created.json()["job_id"])
    assert job["status"] == "succeeded", job.get("error")
    assert job["output"]["chapter_count"] == 2


def test_invalid_inputs(client: TestClient) -> None:
    assert client.post("/api/jobs/dialogue", json={"script": "  "}).status_code == 422
    assert client.post("/api/jobs/audiobook", json={"chapters": [], "voice_id": "x"}).status_code == 422
    assert client.post("/api/voices/design", json={"description": " "}).status_code == 422
    assert (
        client.post("/api/jobs/dubbing", json={"media_id": "nope", "script": "x", "voice_id": "y"}).status_code == 404
    )


def test_metrics(client: TestClient) -> None:
    data = client.get("/api/metrics").json()
    assert data["jobs"]["succeeded"] >= 2
    assert data["audio"]["seconds"] > 0
    assert data["library"]["voices"] >= 4
    assert data["jobs"]["by_kind"]["dialogue"] >= 1
