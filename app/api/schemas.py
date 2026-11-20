"""API 请求模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class VoiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    pitch: float = Field(default=1.0, ge=0.5, le=2.0)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    timbre: str = Field(default="warm", pattern="^(warm|bright|deep)$")
    description: str = ""


class VoiceDesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=2, max_length=200, description="例如：低沉的中年男声，语速偏慢")
    name: str = Field(default="", max_length=60)
    preview_text: str = Field(default="你好，我是你设计的音色。", max_length=60)


class VoiceCloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    audio_b64: str | None = Field(default=None, description="参考音频（WAV）base64；为空时用内置样本")


class MediaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=120)
    data_b64: str = Field(min_length=1, description="视频/音频文件 base64")
    kind: str = Field(default="video", pattern="^(video|audio)$")


class DialogueJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="多角色对白", max_length=120)
    script: str = Field(min_length=1, max_length=8000)
    voice_map: dict[str, str] = Field(default_factory=dict, description="角色名 → 音色 id")
    default_voice_id: str | None = None
    gap: float = Field(default=0.25, ge=0.0, le=2.0)


class DubbingJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="视频配音", max_length=120)
    media_id: str
    script: str = Field(min_length=1, max_length=8000)
    voice_id: str


class AudiobookJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="有声书", max_length=120)
    chapters: list[dict] = Field(min_length=1, max_length=50)
    voice_id: str
