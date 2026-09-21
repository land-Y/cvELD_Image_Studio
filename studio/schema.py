from __future__ import annotations
import re
import secrets
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_RE = re.compile(r"^[0-9a-f]{32}$")

class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["generate", "edit", "local"] = "generate"
    prompt: str = Field(min_length=1, max_length=4096)
    negative_prompt: str = Field(default="", max_length=2048)
    width: int = Field(default=1024, ge=256, le=4096)
    height: int = Field(default=1024, ge=256, le=4096)
    steps: int = Field(default=40, ge=1, le=80)
    cfg: float = Field(default=1.0, ge=1.0, le=8.0, allow_inf_nan=False)
    seed: int = Field(default=-1, ge=-1, le=4294967295)
    count: int = Field(default=1, ge=1, le=99)
    batch_size: int = Field(default=1, ge=1, le=10)
    sampler: Literal["euler", "euler_karras", "euler_exponential"] = "euler"
    transparent: bool = False
    references: list[str] = Field(default_factory=list, max_length=10)
    mask_id: str | None = None
    preserve_outside: bool = True
    mask_feather: int = Field(default=0, ge=0, le=64)
    reference_resolution: Literal[512, 768, 1024] = 768
    profile: Literal["balanced", "low_memory"] = "balanced"
    use_kv_cache: bool = True
    cache_text: bool = True

    @field_validator("width", "height")
    @classmethod
    def multiples_of_32(cls, v: int) -> int:
        if v % 32:
            raise ValueError("幅と高さは32の倍数で指定してください。")
        return v

    @field_validator("prompt")
    @classmethod
    def nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("プロンプトを入力してください。")
        return v.strip()

    @field_validator("references")
    @classmethod
    def validate_ids(cls, v: list[str]) -> list[str]:
        if any(not ID_RE.fullmatch(x) for x in v):
            raise ValueError("参照画像IDが不正です。")
        if len(v) != len(set(v)):
            raise ValueError("参照画像IDを重複させないでください。")
        return v

    @field_validator("mask_id")
    @classmethod
    def validate_mask(cls, v: str | None) -> str | None:
        if v is not None and not ID_RE.fullmatch(v):
            raise ValueError("マスクIDが不正です。")
        return v

    @model_validator(mode="after")
    def coherent(self) -> "GenerationRequest":
        if self.width * self.height > 4_600_000:
            raise ValueError("この構成では出力を約460万画素以下にしてください。")
        if self.mode == "generate" and (self.references or self.mask_id):
            raise ValueError("テキスト生成モードでは参照画像を指定できません。")
        if self.mode != "generate" and not self.references:
            raise ValueError("編集モードには参照画像が必要です。")
        if self.mode == "local":
            if not self.mask_id:
                raise ValueError("部分編集には白黒マスクが必要です。")
            if len(self.references) > 9:
                raise ValueError("部分編集ではマスクが1枠を使うため、参照画像は最大9枚です。")
        elif self.mask_id:
            raise ValueError("マスクを使用するには部分編集モードを選んでください。")
        if self.profile == "low_memory" and self.reference_resolution > 768:
            raise ValueError("省VRAMモードの参照解像度は512または768にしてください。")
        return self

    def seeds(self) -> list[int]:
        first = secrets.randbits(32) if self.seed == -1 else self.seed
        return [(first + i) % (2**32) for i in range(self.count * self.batch_size)]
