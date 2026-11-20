"""媒体层测试：WAV 混音、时长对齐、视频混流与封面。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from app.media.audio import (  # noqa: E402
    concat_with_gaps,
    fit_duration,
    mix_dialogue,
    normalize_loudness,
    wav_info,
)
from app.media.tts import synthesize  # noqa: E402
from app.media.video import (  # noqa: E402
    extract_audio,
    make_placeholder_video,
    probe,
    replace_audio,
    thumbnail,
)

WORK = Path("data/test_media")


@pytest.fixture(scope="module", autouse=True)
def workdir():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    yield
    shutil.rmtree(WORK, ignore_errors=True)


@pytest.fixture(scope="module")
def short_wavs() -> list[Path]:
    paths = []
    for index in range(3):
        path = WORK / f"seg_{index}.wav"
        synthesize("测试音频片段。", path)
        paths.append(path)
    return paths


def test_wav_info(short_wavs: list[Path]) -> None:
    info = wav_info(short_wavs[0])
    assert info["channels"] == 1
    assert info["sample_width"] == 2
    assert info["duration"] > 0.5
    with pytest.raises(FileNotFoundError):
        wav_info(WORK / "missing.wav")


def test_concat_with_gaps(short_wavs: list[Path]) -> None:
    merged = concat_with_gaps(short_wavs, WORK / "merged.wav", gap=0.2)
    total = sum(wav_info(path)["duration"] for path in short_wavs)
    assert merged["segments"] == 3
    assert abs(merged["duration"] - (total + 0.4)) < 0.2


def test_mix_dialogue_timeline(short_wavs: list[Path]) -> None:
    segments = [
        {"path": short_wavs[0], "text": "第一句", "speaker": "林夕"},
        {"path": short_wavs[1], "text": "第二句", "speaker": "周野"},
        {"path": short_wavs[2], "text": "第三句", "speaker": "旁白"},
    ]
    mixed = mix_dialogue(segments, WORK / "dialogue.wav", gap=0.25)
    assert mixed["timeline"][0]["start"] == 0
    assert mixed["timeline"][1]["start"] > mixed["timeline"][0]["end"]
    assert [item["speaker"] for item in mixed["timeline"]] == ["林夕", "周野", "旁白"]
    assert abs(mixed["duration"] - mixed["timeline"][-1]["end"]) < 0.05


def test_fit_duration_up_and_down(short_wavs: list[Path]) -> None:
    source = short_wavs[0]
    duration = wav_info(source)["duration"]

    faster = fit_duration(source, WORK / "faster.wav", target_seconds=duration / 2)
    assert faster["factor"] == pytest.approx(2.0, abs=0.2)
    assert abs(faster["duration"] - duration / 2) < 0.25

    slower = fit_duration(source, WORK / "slower.wav", target_seconds=duration * 2)
    assert slower["factor"] == pytest.approx(0.5, abs=0.1)
    assert abs(slower["duration"] - duration * 2) < 0.4

    with pytest.raises(ValueError):
        fit_duration(source, WORK / "x.wav", target_seconds=0)


def test_normalize_loudness(short_wavs: list[Path]) -> None:
    result = normalize_loudness(short_wavs[0], WORK / "normalized.wav")
    assert result["duration"] > 0.3
    assert wav_info(WORK / "normalized.wav")["channels"] == 1


def test_video_pipeline() -> None:
    video = make_placeholder_video(WORK / "demo.mp4", seconds=3.0, width=320, height=180)
    assert video["size_bytes"] > 5_000
    info = probe(WORK / "demo.mp4")
    codecs = {stream["codec"] for stream in info["streams"]}
    assert {"h264", "aac"} <= codecs
    assert abs(info["duration"] - 3.0) < 0.4

    audio = synthesize("配音测试。", WORK / "voice.wav")
    dubbed = replace_audio(WORK / "demo.mp4", WORK / "voice.wav", WORK / "dubbed.mp4")
    assert dubbed["size_bytes"] > 5_000
    assert abs(dubbed["duration"] - min(3.0, audio["duration"])) < 0.5

    extracted = extract_audio(WORK / "dubbed.mp4", WORK / "extracted.wav", fmt="wav")
    assert extracted["duration"] > 0.5

    cover = thumbnail(WORK / "dubbed.mp4", WORK / "cover.jpg", at=0.4)
    assert cover["size_bytes"] > 500
    assert cover["width"] == 320


def test_probe_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        probe(WORK / "nope.mp4")
