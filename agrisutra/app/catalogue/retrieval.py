"""Local BM25 lexical retrieval over catalogue names, aliases and approved facts.

Retrieval only finds candidate records and source fields; it never writes an answer.
"""

import math
import re
from collections import Counter

from app.catalogue.ingest import CatalogueStore

STOPWORDS = {"the", "a", "an", "is", "it", "of", "for", "and", "to", "i", "me", "my", "do", "you",
             "have", "this", "that", "with", "what", "does", "can", "in", "on", "be", "are"}


def tokenise(text: str) -> list[str]:
    text = text.lower().replace("wheelbarrow", "wheel barrow").replace("trolly", "trolley")
    return [t for t in re.findall(r"[a-z0-9]+", text) if t not in STOPWORDS]


class CatalogueRetriever:
    """BM25 (k1=1.5, b=0.75) over one document per product."""

    def __init__(self, store: CatalogueStore, k1: float = 1.5, b: float = 0.75):
        self.store = store
        self.k1, self.b = k1, b
        self.docs: list[dict] = []
        self._build_index()

    def _build_index(self) -> None:
        for p in self.store.get_all_products():
            parts = [p["name"], p["short_name"], p["family_label"], " ".join(p["aliases"])]
            for key, fact in p["facts"].items():
                if fact.get("status") == "APPROVED":
                    parts.append(f"{key.replace('_', ' ')} {fact['value']}")
            tokens = tokenise(" ".join(parts))
            self.docs.append({"product_id": p["product_id"], "tokens": tokens, "tf": Counter(tokens)})
        n = len(self.docs)
        df = Counter(t for d in self.docs for t in set(d["tokens"]))
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.avgdl = sum(len(d["tokens"]) for d in self.docs) / max(n, 1)

    def search(self, query: str, top_k: int = 5, product_ids: set[str] | None = None) -> list[dict]:
        q = tokenise(query)
        results = []
        for d in self.docs:
            if product_ids is not None and d["product_id"] not in product_ids:
                continue
            dl = len(d["tokens"])
            score = 0.0
            for t in q:
                f = d["tf"].get(t, 0)
                if not f:
                    continue
                score += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if score > 0:
                results.append({"product_id": d["product_id"], "score": round(score, 3)})
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    def get_product_for_query(self, query: str, min_score: float = 1.0) -> dict | None:
        hits = self.store.find_by_alias(query)
        if hits:
            return hits[0]
        res = self.search(query, top_k=1)
        if res and res[0]["score"] >= min_score:
            return self.store.get_product(res[0]["product_id"])
        return None
