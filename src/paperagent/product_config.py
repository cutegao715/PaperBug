"""Validated, non-secret product settings. Environment wins over JSON."""
import json
import os
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

SECTION_RATIOS = {"workflow": .12, "skills": .08, "memories": .15,
    "evidence_preview": .10, "saved_references": .07, "recent_messages": .28,
    "project_memory": .12, "connector_state": .03}
class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")
class Model(Strict):
    protocol: Literal["openai", "anthropic"] = "openai"
    base_url: str = ""
    model: str = ""
class Models(Strict):
    primary: Model = Field(default_factory=Model)
    fast: Model = Field(default_factory=Model)
class Tools(Strict):
    disabled: list[str] = Field(default_factory=list)
class Features(Strict):
    radar_cloud_notify: bool = False
class Budget(Strict):
    context_input_tokens: int = Field(default=24000, gt=0)
    section_ratios: dict[str, float] = Field(default_factory=lambda: dict(SECTION_RATIOS))
    @model_validator(mode="after")
    def ratios_valid(self):
        import math
        ratios = {**SECTION_RATIOS, **self.section_ratios}
        if set(ratios) != set(SECTION_RATIOS) or any(not math.isfinite(v) or v < 0 for v in ratios.values()) or sum(ratios.values()) > 1.000001:
            raise ValueError("Invalid context section ratios")
        self.section_ratios = ratios
        return self
class Product(Strict):
    product: str = "PaperAgent"
    models: Models = Field(default_factory=Models)
    tools: Tools = Field(default_factory=Tools)
    features: Features = Field(default_factory=Features)
    budget: Budget = Field(default_factory=Budget)
def _merge(base, update):
    for k,v in update.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict): _merge(base[k], v)
        else: base[k] = v
def load():
    data = Product().model_dump()
    _merge(data, json.loads(Path(__file__).with_name("product.json").read_text(encoding="utf-8")))
    if os.environ.get("PAPERAGENT_PRODUCT_CONFIG"):
        _merge(data, json.loads(Path(os.environ["PAPERAGENT_PRODUCT_CONFIG"]).read_text(encoding="utf-8")))
    for tier, prefix in (("primary", "PAPERAGENT_LLM_"), ("fast", "PAPERAGENT_FAST_LLM_")):
        for key in ("protocol", "base_url", "model"):
            if prefix+key.upper() in os.environ: data["models"][tier][key] = os.environ[prefix+key.upper()]
    if "PAPERAGENT_CONTEXT_INPUT_TOKENS" in os.environ:
        data["budget"]["context_input_tokens"] = os.environ["PAPERAGENT_CONTEXT_INPUT_TOKENS"]
    return Product.model_validate(data)
