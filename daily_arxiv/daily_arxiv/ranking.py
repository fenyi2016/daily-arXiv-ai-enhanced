from __future__ import annotations

from typing import Dict, Iterable, List


MEDICAL_KEYWORDS = [
    "medical",
    "medicine",
    "clinical",
    "patient",
    "patients",
    "hospital",
    "radiology",
    "radiological",
    "radiograph",
    "radiography",
    "x-ray",
    "xray",
    "ct scan",
    "computed tomography",
    "mri",
    "magnetic resonance",
    "ultrasound",
    "pathology",
    "histopathology",
    "biomedical",
    "healthcare",
    "diagnosis",
    "diagnostic",
    "disease",
    "diseases",
    "tumor",
    "tumour",
    "cancer",
    "lesion",
    "retinal",
    "retina",
    "fundus",
    "dermoscopy",
    "microscopy",
    "endoscopy",
    "colonoscopy",
    "surgery",
    "surgical",
    "pneumonia",
    "covid",
]

TOP_CONFERENCE_KEYWORDS = {
    "CVPR": ["cvpr"],
    "ICCV": ["iccv"],
    "ECCV": ["eccv"],
    "NeurIPS": ["neurips", "nips"],
    "ICLR": ["iclr"],
    "ICML": ["icml"],
    "AAAI": ["aaai"],
    "IJCAI": ["ijcai"],
}

ACCEPTANCE_KEYWORDS = [
    "accepted",
    "acceptance",
    "to appear in",
    "camera ready",
    "oral",
    "spotlight",
    "proceedings of",
]

INNOVATION_TERMS = {
    "state-of-the-art": 12,
    "sota": 10,
    "novel": 8,
    "new": 3,
    "first": 6,
    "unified": 5,
    "generalist": 6,
    "foundation model": 7,
    "foundation models": 7,
    "breakthrough": 8,
    "significant improvement": 6,
    "strong baseline": 2,
    "outperform": 6,
    "outperforms": 6,
    "surpass": 6,
    "surpasses": 6,
    "achieves": 3,
    "achieve": 3,
    "new benchmark": 5,
}


def normalize_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    return " ".join(str(value).split())


def collect_text(item: Dict) -> str:
    ai = item.get("AI", {})
    parts = [
        item.get("title", ""),
        item.get("summary", ""),
        item.get("comment", ""),
        " ".join(item.get("categories", [])) if isinstance(item.get("categories"), list) else item.get("categories", ""),
        ai.get("tldr", ""),
        ai.get("motivation", ""),
        ai.get("method", ""),
        ai.get("result", ""),
        ai.get("conclusion", ""),
    ]
    return normalize_text(" ".join(str(part) for part in parts)).lower()


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def is_medical_paper(item: Dict) -> bool:
    text = collect_text(item)
    return contains_any(text, MEDICAL_KEYWORDS)


def detect_top_conference(comment: str) -> str:
    lowered = normalize_text(comment).lower()
    if not lowered:
        return ""

    for conference, keywords in TOP_CONFERENCE_KEYWORDS.items():
        if contains_any(lowered, keywords):
            return conference
    return ""


def innovation_score(item: Dict) -> int:
    text = collect_text(item)
    score = 0
    for term, weight in INNOVATION_TERMS.items():
        if term in text:
            score += weight
    return min(score, 25)


def build_priority_metadata(item: Dict) -> Dict:
    score = 0
    reasons: List[str] = []

    conference = detect_top_conference(item.get("comment", ""))
    comment_text = normalize_text(item.get("comment", "")).lower()
    if conference:
        score += 35
        reasons.append(f"顶会相关: {conference}")
        if contains_any(comment_text, ACCEPTANCE_KEYWORDS):
            score += 15
            reasons.append(f"顶会接收: {conference}")

    if item.get("code_url"):
        score += 20
        reasons.append("已开源")

    code_stars = int(item.get("code_stars", 0) or 0)
    if code_stars > 0:
        star_bonus = min(code_stars // 50, 10)
        if star_bonus > 0:
            score += star_bonus
            reasons.append(f"代码热度: {code_stars} stars")

    novelty_bonus = innovation_score(item)
    if novelty_bonus > 0:
        score += novelty_bonus
        reasons.append(f"创新性信号: +{novelty_bonus}")

    return {
        "priority_score": score,
        "priority_reasons": reasons,
        "top_conference": conference,
        "is_medical": is_medical_paper(item),
    }


def sort_papers(papers: List[Dict]) -> List[Dict]:
    def sort_key(item: Dict):
        return (
            -int(item.get("priority_score", 0) or 0),
            -int(item.get("code_stars", 0) or 0),
            normalize_text(item.get("title", "")).lower(),
        )

    return sorted(papers, key=sort_key)
