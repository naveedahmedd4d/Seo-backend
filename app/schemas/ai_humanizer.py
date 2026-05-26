from typing import Dict, List, Literal

from pydantic import BaseModel, Field

Tone = Literal["formal", "casual", "professional", "friendly"]


class HumanizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000)
    tone: Tone = "professional"


class PatternHitSchema(BaseModel):
    category: str
    matched: str
    start: int
    end: int


class DiffSegment(BaseModel):
    type: Literal["equal", "insert", "delete"]
    text: str


class HumanizeResponse(BaseModel):
    humanized: str
    ai_score_before: float
    ai_score_after: float
    score_improved: bool
    tone: Tone
    score_breakdown_before: Dict[str, float]
    score_breakdown_after: Dict[str, float]
    patterns_found: List[PatternHitSchema]
    replacements_applied: List[Dict[str, str]]
    diff_segments: List[DiffSegment]
