"""
Bob on the Floor -- Device 3: Memory (retrieve-procedure)

Input:  Schema 2 object (Anomaly flag). Only `description` is used.
Output: Schema 3 object (Procedure result).

Retrieval strategy: BM25 keyword search (rank_bm25) over a small local
corpus of placeholder manual/ticket "pages" (corpus.json). This is the
fastest-to-stand-up-correctly option per the brief -- no embedding model,
no network calls at query time, fully deterministic and inspectable.

This module exposes:
    retrieve(schema2_input: dict) -> dict   # returns a Schema 3 object
    validate_schema3(obj: dict) -> None     # raises if invalid

Run directly to execute the example Schema 2 fixture and print + validate
the resulting Schema 3 output.
"""

import json
import re
import os
import sys
from abc import ABC, abstractmethod

from rank_bm25 import BM25Okapi
import jsonschema

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS_PATH = os.getenv("CORPUS_PATH", os.path.join(HERE, "corpus.json"))
SCHEMA3_PATH = os.path.join(HERE, "schema3.json")

EXCERPT_MAX_LEN = 300

# Very small stopword list -- BM25 handles term weighting fine without a
# big list; this just strips the highest-frequency noise words so short
# queries aren't dominated by "on", "the", "above", etc.
STOPWORDS = {
    "a", "an", "the", "on", "in", "at", "of", "for", "to", "over", "above",
    "is", "was", "with", "and", "or", "than", "by", "from",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str):
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if t not in STOPWORDS]


def _load_corpus():
    with open(CORPUS_PATH, "r") as f:
        return json.load(f)


class ProcedureStore(ABC):
    """Abstract base for procedure retrieval backends."""

    @abstractmethod
    def query(self, description: str, anomaly_type: str = None):
        """Returns (best_entry dict, confidence float, all_scores list)."""

    @abstractmethod
    def reload(self) -> int:
        """Reload the corpus from its source. Returns new corpus size."""

    @property
    @abstractmethod
    def corpus_size(self) -> int:
        """Number of entries in the loaded corpus."""


class MemoryIndex(ProcedureStore):
    """BM25 index over a local corpus.json file. Default ProcedureStore."""

    def __init__(self, corpus_path: str = CORPUS_PATH):
        self._corpus_path = corpus_path
        self.corpus = []
        self._docs_tokenized = []
        self.bm25 = None
        self._build_index()

    def _build_index(self) -> None:
        with open(self._corpus_path, "r") as f:
            self.corpus = json.load(f)
        self._docs_tokenized = []
        for entry in self.corpus:
            blob = " ".join([
                entry.get("title", ""),
                entry.get("text", ""),
                " ".join(entry.get("anomaly_types", [])),
                entry.get("source_doc", ""),
            ])
            self._docs_tokenized.append(_tokenize(blob))
        self.bm25 = BM25Okapi(self._docs_tokenized)

    def reload(self) -> int:
        """Re-read corpus file and rebuild BM25 index. Returns new corpus size."""
        self._build_index()
        return len(self.corpus)

    @property
    def corpus_size(self) -> int:
        return len(self.corpus)

    def query(self, description: str, anomaly_type: str = None):
        """Returns (best_entry, confidence, all_scores) for a text query.

        If anomaly_type is provided, matching corpus entries get a small
        score boost -- this mirrors a metadata filter you'd get for free
        from a real vector DB's payload filtering, without requiring one.
        """
        q_tokens = _tokenize(description)
        scores = list(self.bm25.get_scores(q_tokens))

        if anomaly_type:
            for i, entry in enumerate(self.corpus):
                if anomaly_type in entry.get("anomaly_types", []):
                    scores[i] *= 1.15  # light metadata boost, not a hard filter

        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        best_idx = ranked[0]
        best_score = scores[best_idx]

        # Squash unbounded BM25 score into (0, 1). k controls how quickly
        # confidence saturates; tuned by eye against this corpus's score
        # range (typical good matches land ~4-10).
        k = 6.0
        confidence = best_score / (best_score + k) if best_score > 0 else 0.0
        confidence = round(float(min(confidence, 0.99)), 3)

        return self.corpus[int(best_idx)], confidence, scores


MATCH_CONFIDENCE_THRESHOLD = 0.30


def _make_excerpt(text: str) -> str:
    text = text.strip()
    if len(text) <= EXCERPT_MAX_LEN:
        return text
    cut = text[: EXCERPT_MAX_LEN - 1].rsplit(" ", 1)[0]
    return cut + "\u2026"  # ellipsis


_INDEX = None


def _get_index():
    global _INDEX
    if _INDEX is None:
        _INDEX = MemoryIndex()
    return _INDEX


def retrieve(schema2_input: dict) -> dict:
    """Schema 2 (dict) -> Schema 3 (dict). Only uses `description` and,
    if present, `anomaly_type` as an optional metadata boost."""
    description = schema2_input.get("description", "")
    anomaly_type = schema2_input.get("anomaly_type")

    index = _get_index()
    best_entry, confidence, _scores = index.query(description, anomaly_type)

    matched = confidence >= MATCH_CONFIDENCE_THRESHOLD

    output = {
        "matched": matched,
        "procedure_id": best_entry["procedure_id"],
        "source_doc": best_entry["source_doc"],
        "page": best_entry["page"],
        "excerpt": _make_excerpt(best_entry["text"]),
        "suggested_fix_nl": best_entry["suggested_fix_nl"],
        "confidence": confidence,
    }
    return output


def validate_schema3(obj: dict) -> None:
    with open(SCHEMA3_PATH, "r") as f:
        schema = json.load(f)
    jsonschema.validate(instance=obj, schema=schema)
    # Extra checks beyond what JSON Schema conveniently expresses:
    assert len(obj["excerpt"]) < 300, "excerpt must be <300 chars"
    assert 0.0 <= obj["confidence"] <= 1.0, "confidence out of range"


if __name__ == "__main__":
    test_input = {
        "device_id": "conveyor-02",
        "timestamp": "2026-09-25T14:03:00Z",
        "anomaly_type": "vibration_drift",
        "severity": 0.72,
        "signature": {},
        "description": "vibration on conveyor motor 2 drifting 38% above baseline over 12 min",
    }

    result = retrieve(test_input)
    validate_schema3(result)

    print(json.dumps(result, indent=2))
    print("\n[OK] output validates against schema3.json", file=sys.stderr)
