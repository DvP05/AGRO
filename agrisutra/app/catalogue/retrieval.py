"""Catalogue-grounded retrieval using local TF-IDF lexical search."""

import re
import math
from collections import Counter
from typing import Any

from app.catalogue.ingest import CatalogueStore


class CatalogueRetriever:
    """Local TF-IDF retrieval over catalogue products and FAQs."""

    def __init__(self, store: CatalogueStore):
        self.store = store
        self.docs: list[dict] = []       # {id, text, product_id, type}
        self.tf_idf: list[dict] = []     # pre-computed tf-idf vectors
        self.idf: dict[str, float] = {}
        self._build_index()

    def _tokenise(self, text: str) -> list[str]:
        return re.findall(r'[a-z0-9]+', text.lower())

    def _build_index(self):
        """Build TF-IDF index from catalogue descriptions and FAQs."""
        self.docs = []
        for p in self.store.get_all_products():
            # Product description document
            desc_parts = [
                p["name"], p["category"],
                " ".join(p["applications"]),
                " ".join(p["limitations"]),
            ]
            specs = p.get("specifications", {})
            for k, v in specs.items():
                if isinstance(v, dict):
                    desc_parts.append(f"{k} {v.get('value', '')} {v.get('unit', '')}")
                else:
                    desc_parts.append(f"{k} {v}")
            self.docs.append({
                "id": f"prod-{p['product_id']}",
                "name": p["name"],
                "text": " ".join(str(x) for x in desc_parts),
                "product_id": p["product_id"],
                "type": "product",
            })
            # FAQ documents
            for i, faq in enumerate(p.get("faq", [])):
                self.docs.append({
                    "id": f"faq-{p['product_id']}-{i}",
                    "text": f"{faq['q']} {faq['a']}",
                    "product_id": p["product_id"],
                    "type": "faq",
                    "question": faq["q"],
                    "answer": faq["a"],
                })

        # Compute IDF
        n = len(self.docs)
        df: dict[str, int] = Counter()
        tokenised = []
        for doc in self.docs:
            tokens = self._tokenise(doc["text"])
            tokenised.append(tokens)
            for t in set(tokens):
                df[t] += 1
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}

        # Compute TF-IDF vectors
        self.tf_idf = []
        for tokens in tokenised:
            tf = Counter(tokens)
            total = len(tokens) or 1
            vec = {}
            for t, count in tf.items():
                vec[t] = (count / total) * self.idf.get(t, 1.0)
            self.tf_idf.append(vec)

    def _cosine_sim(self, v1: dict[str, float], v2: dict[str, float]) -> float:
        common = set(v1) & set(v2)
        if not common:
            return 0.0
        dot = sum(v1[t] * v2[t] for t in common)
        norm1 = math.sqrt(sum(x ** 2 for x in v1.values()))
        norm2 = math.sqrt(sum(x ** 2 for x in v2.values()))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    def search(self, query: str, top_k: int = 5, min_score: float = 0.05) -> list[dict]:
        """Search the catalogue index. Returns ranked results with scores."""
        tokens = self._tokenise(query)
        tf = Counter(tokens)
        total = len(tokens) or 1
        q_vec = {t: (c / total) * self.idf.get(t, 1.0) for t, c in tf.items()}

        results = []
        for i, doc in enumerate(self.docs):
            score = self._cosine_sim(q_vec, self.tf_idf[i])
            if score >= min_score:
                results.append({**doc, "score": round(score, 4)})
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def search_faq(self, query: str, top_k: int = 3) -> list[dict]:
        """Search only FAQ entries."""
        all_results = self.search(query, top_k=20, min_score=0.03)
        faq_results = [r for r in all_results if r["type"] == "faq"]
        return faq_results[:top_k]

    def search_products(self, query: str, top_k: int = 5) -> list[dict]:
        """Search only product descriptions."""
        all_results = self.search(query, top_k=20, min_score=0.03)
        prod_results = [r for r in all_results if r["type"] == "product"]
        return prod_results[:top_k]

    def get_product_for_query(self, query: str) -> dict | None:
        """Return the best matching product, or None if no good match."""
        results = self.search_products(query, top_k=1)
        if results and results[0]["score"] > 0.1:
            return self.store.get_product(results[0]["product_id"])
        return None
