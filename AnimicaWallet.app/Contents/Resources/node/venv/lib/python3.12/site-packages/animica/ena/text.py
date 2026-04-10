from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Iterable, List, Sequence


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "with",
}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def tokenize(text: str) -> List[str]:
    return [t for t in re.findall(r"[A-Za-z0-9_./:-]+", (text or "").lower()) if t]


def keyword_terms(text: str) -> List[str]:
    return [t for t in tokenize(text) if t not in STOPWORDS and len(t) > 2]


def sentence_split(text: str) -> List[str]:
    cleaned = normalize_text(text)
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return [p.strip() for p in parts if p.strip()]


def sha256_hex(data: bytes | str) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def sha3_hex(data: bytes | str) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha3_256(raw).hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    digest = sha3_hex("::".join(parts))
    return f"{prefix}_{digest[:16]}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hashed_embedding(text: str, dimensions: int = 64) -> List[float]:
    vector = [0.0] * dimensions
    for token in keyword_terms(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % dimensions
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        return [value / norm for value in vector]
    return vector


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return float(sum(a * b for a, b in zip(left, right)))


def text_score(query: str, text: str) -> float:
    query_terms = keyword_terms(query)
    if not query_terms:
        return 0.0
    tokens = keyword_terms(text)
    if not tokens:
        return 0.0
    counts = Counter(tokens)
    raw = sum(counts[term] for term in query_terms)
    phrase_boost = 2.0 if normalize_text(query).lower() in normalize_text(text).lower() else 0.0
    return raw + phrase_boost


def summarize_passages(query: str, passages: Iterable[str], max_sentences: int = 5) -> List[str]:
    scored: List[tuple[float, str]] = []
    for passage in passages:
        for sentence in sentence_split(passage):
            score = text_score(query, sentence)
            if score > 0:
                scored.append((score, sentence))
    scored.sort(key=lambda item: (-item[0], len(item[1])))
    seen = set()
    results: List[str] = []
    for _, sentence in scored:
        key = sentence.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(sentence)
        if len(results) >= max_sentences:
            break
    return results


def simhash64(text: str) -> int:
    weights = [0] * 64
    for token in keyword_terms(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        value = int.from_bytes(digest[:8], "big")
        for bit in range(64):
            if value & (1 << bit):
                weights[bit] += 1
            else:
                weights[bit] -= 1
    result = 0
    for bit, weight in enumerate(weights):
        if weight > 0:
            result |= 1 << bit
    return result


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()
