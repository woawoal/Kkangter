"""사진 자동 인식: 라인업/교환 이미지 → 시리즈·캐릭터·가진것/원하는것 추출 → 카탈로그 매칭.

설계 원칙
- 모델은 '읽은 그대로'만 출력한다 (이름표 텍스트, 모자 동물, 표시). 카탈로그 매칭은 서버가 한다.
  → 카탈로그가 커져도 프롬프트가 길어지지 않고, 모델을 바꿔도 매칭 로직은 그대로.
- GPT로 만든 교환 이미지는 이름표 글자가 깨져 있는 경우가 많다(예: 洋迁愿 → 详迁愿).
  → 이름 텍스트는 퍼지 매칭, 모자 동물(한국어 한 단어)은 별칭과 매칭해 보완한다.
- 결과는 '확정'이 아니라 '후보'. 사용자가 고른 값을 VisionParse.accepted 에 남겨 정확도를 잰다.
"""
import base64
import hashlib
import io
import json
import re
import time
from difflib import SequenceMatcher

import httpx
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..models import Character, Series

MAX_SIDE = 1568  # 이보다 크면 모델 쪽에서 어차피 축소된다

PROMPT = """당신은 블라인드 박스(랜덤깡) 인형 교환 이미지를 읽는 도우미입니다.
이미지를 보고 아래 JSON 하나만 출력하세요. 설명이나 코드블록 없이 JSON만.

{
  "image_type": "lineup_sheet" | "real_photo" | "other",
  "series_text": "이미지에 적힌 시리즈/브랜드 이름 그대로 (없으면 빈 문자열)",
  "series_guess": "글자가 없을 때 그림체로 추정한 시리즈명 (모르면 빈 문자열)",
  "characters": [
    {
      "label_text": "캐릭터 아래 이름표 글자 그대로 (중국어면 중국어로, 가려져 안 보이면 빈 문자열)",
      "animal": "모자/탈의 동물이나 특징을 한국어 한 단어로 (예: 토끼, 강아지, 고양이, 곰, 양, 돼지, 쥐)",
      "colors": "주요 색 한두 개 (예: 분홍, 하늘색)",
      "position": "위치 (예: 1행 2열)",
      "mark": "have" | "want" | "none",
      "is_secret": true | false
    }
  ],
  "notes": "그 밖에 거래 조건 문구가 있으면 한국어로 요약"
}

mark 판단 기준:
- have(내가 가진 것/줄 것): HAVE, ME, 보유, 줄게요, 양도, 有 등 표시나 보라색 등으로 '내 것'을 강조한 원
- want(원하는 것/받을 것): WISH, WANT, YOU, 구해요, 받을게요, 求 등 표시
- 표시가 없거나 X 표시면 none
- 실물 사진(real_photo)이면 보이는 인형을 모두 나열하고 mark는 none
is_secret: 이름표 옆에 隐藏, SECRET, 시크릿 표시가 있으면 true
이름표가 원 안에 가려져도 캐릭터 자체는 반드시 characters에 포함하세요."""


class VisionError(RuntimeError):
    pass


def prepare(raw: bytes) -> tuple[bytes, str]:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)
    data = out.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise VisionError("모델 응답에서 JSON을 찾지 못했어요")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise VisionError("모델 응답 JSON 파싱 실패") from e


def call_model(jpeg: bytes) -> dict:
    if settings.VISION_PROVIDER == "mock":
        return MOCK_RESPONSE
    if not settings.ANTHROPIC_API_KEY:
        raise VisionError("ANTHROPIC_API_KEY 가 설정되지 않았어요")
    body = {
        "model": settings.VISION_MODEL,
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(jpeg).decode()}},
            {"type": "text", "text": PROMPT},
        ]}],
    }
    r = httpx.post("https://api.anthropic.com/v1/messages", json=body, timeout=60, headers={
        "x-api-key": settings.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"})
    if r.status_code != 200:
        raise VisionError(f"비전 API 오류 {r.status_code}: {r.text[:200]}")
    text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    return _extract_json(text)


# ---------------- 카탈로그 매칭 ----------------

def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


def _terms(*fields: str) -> list[str]:
    out = []
    for f in fields:
        out += [_norm(t) for t in re.split(r"[,/]", f or "") if _norm(t)]
    return out


def _sim(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def match_series(db: Session, texts: list[str]) -> tuple[Series | None, float]:
    best, score = None, 0.0
    for s in db.scalars(select(Series).where(Series.is_active.is_(True))
                        .options(selectinload(Series.characters))):
        for t in texts:
            for cand in [s.name, *_terms(s.aliases)]:
                sc = _sim(t, cand)
                if sc > score:
                    best, score = s, sc
    return (best, score) if score >= 0.6 else (None, score)


def score_character(c: Character, det: dict) -> float:
    names = [c.name, *_terms(c.aliases)]
    label = det.get("label_text", "")
    animal = _norm(det.get("animal", ""))
    s_label = max((_sim(label, n) for n in names), default=0.0) if label else 0.0
    # 동물/특징 단어가 이름·별칭 토큰과 일치하면 강한 신호 (이름표가 가려졌을 때의 핵심 단서)
    s_animal = 0.0
    if animal:
        if any(animal == n for n in map(_norm, names)):
            s_animal = 0.85
        elif any(animal in n for n in map(_norm, names)):
            s_animal = 0.7
    # 이름표는 읽혔는데 이 캐릭터와 전혀 안 맞으면, 동물만 맞는 건 약한 신호로 낮춘다
    if label and s_label < 0.4:
        s_animal *= 0.75
    s = max(s_label, s_animal)
    if s_label >= 0.5 and s_animal > 0:  # 둘 다 맞으면 가산
        s = min(1.0, s + 0.1)
    if det.get("is_secret") and c.is_secret:
        s = min(1.0, s + 0.1)
    return round(s, 3)


def match(db: Session, raw: dict) -> dict:
    texts = [t for t in (raw.get("series_text", ""), raw.get("series_guess", "")) if t]
    series, s_score = match_series(db, texts) if texts else (None, 0.0)
    pool = series.characters if series else list(db.scalars(
        select(Character).options(selectinload(Character.series))))
    dets = []
    for det in raw.get("characters", [])[:20]:
        cands = sorted(((score_character(c, det), c) for c in pool), key=lambda x: -x[0])
        cands = [(s, c) for s, c in cands if s >= 0.45][:3]
        dets.append({
            "label_text": det.get("label_text", ""),
            "animal": det.get("animal", ""),
            "colors": det.get("colors", ""),
            "position": det.get("position", ""),
            "mark": det.get("mark", "none") if det.get("mark") in ("have", "want") else "none",
            "is_secret": bool(det.get("is_secret")),
            "candidates": [{"id": c.id, "name": c.name, "series": c.series.name, "score": s} for s, c in cands],
            "auto": bool(cands and cands[0][0] >= 0.6),
        })
    marks = {d["mark"] for d in dets}
    post_type = "trade" if {"have", "want"} <= marks else "sell" if "have" in marks else \
        "want" if "want" in marks else ""
    return {
        "image_type": raw.get("image_type", "other"),
        "series": {"id": series.id, "name": series.name, "score": round(s_score, 3)} if series else None,
        "series_text": raw.get("series_text") or raw.get("series_guess") or "",
        "post_type": post_type,
        "detections": dets,
        "notes": raw.get("notes", ""),
        # 카탈로그에 없는 시리즈 → 관리자 일괄추가 형식으로 미리 만들어 둔다 (카탈로그 성장 루프)
        "catalog_suggestion": None if series else "\n".join(
            f"{raw.get('series_text') or raw.get('series_guess') or '새 시리즈'} | "
            f"{d.get('animal') or d.get('label_text') or '?'} | {d.get('label_text', '')}"
            f"{' | y' if d.get('is_secret') else ''}"
            for d in raw.get("characters", [])),
    }


def parse_image(db: Session, raw_bytes: bytes) -> tuple[dict, dict, str, int]:
    jpeg, sha = prepare(raw_bytes)
    t0 = time.monotonic()
    raw = call_model(jpeg)
    ms = int((time.monotonic() - t0) * 1000)
    return raw, match(db, raw), sha, ms


# 테스트/로컬 데모용: 사용자가 보낸 Lullaby 교환 이미지를 사람이 읽은 결과와 같은 형식
MOCK_RESPONSE = {
    "image_type": "lineup_sheet",
    "series_text": "Lullaby FLUFFY BABY",
    "series_guess": "",
    "characters": [
        {"label_text": "绒绒兔", "animal": "토끼", "colors": "분홍", "position": "1행 1열", "mark": "none", "is_secret": False},
        {"label_text": "详迁愿", "animal": "강아지", "colors": "베이지", "position": "1행 2열", "mark": "want", "is_secret": False},
        {"label_text": "喧软软", "animal": "고양이", "colors": "보라", "position": "1행 3열", "mark": "none", "is_secret": False},
        {"label_text": "抱抱糖", "animal": "곰", "colors": "노랑", "position": "2행 1열", "mark": "none", "is_secret": False},
        {"label_text": "", "animal": "양", "colors": "하양", "position": "2행 3열", "mark": "have", "is_secret": False},
        {"label_text": "", "animal": "돼지", "colors": "분홍", "position": "3행 1열", "mark": "have", "is_secret": False},
        {"label_text": "咳咳鼠", "animal": "쥐", "colors": "하늘색", "position": "3행 2열", "mark": "want", "is_secret": False},
        {"label_text": "唤安宝宝", "animal": "토끼", "colors": "크림", "position": "3행 3열", "mark": "none", "is_secret": True},
    ],
    "notes": "교환 구해요, 1:1 챗 요청",
}
