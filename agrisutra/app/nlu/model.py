"""Locally trained intent model: word + character TF-IDF → Logistic Regression.

No hosted model or external API is used. The model is trained from the
reviewed utterances in data/training/intents.json and cached under models/.
"""

import hashlib
import json
import os
from datetime import datetime, timezone

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TRAIN_PATH = os.path.join(ROOT, "data", "training", "intents.json")
HELDOUT_PATH = os.path.join(ROOT, "data", "evaluation", "intents_heldout.json")
MODEL_PATH = os.path.join(ROOT, "models", "intent_model.joblib")

FEATURE_CONFIG = {
    "word_ngram_range": (1, 2),
    "char_ngram_range": (2, 5),
    "C": 8.0,
    "class_weight": "balanced",
}


def load_training_data(path: str = TRAIN_PATH) -> tuple[list[str], list[str], str]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    texts, labels = [], []
    for intent, examples in raw["examples"].items():
        for ex in examples:
            texts.append(ex.lower())
            labels.append(intent)
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()[:10]
    return texts, labels, f"{raw.get('version', 'intents')}-{digest}"


def build_pipeline() -> Pipeline:
    features = FeatureUnion([
        ("word", TfidfVectorizer(analyzer="word", ngram_range=FEATURE_CONFIG["word_ngram_range"],
                                 token_pattern=r"(?u)\b\w+\b", sublinear_tf=True)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=FEATURE_CONFIG["char_ngram_range"],
                                 sublinear_tf=True)),
    ])
    clf = LogisticRegression(C=FEATURE_CONFIG["C"], class_weight=FEATURE_CONFIG["class_weight"],
                             max_iter=3000)
    return Pipeline([("features", features), ("clf", clf)])


def train(save: bool = True) -> dict:
    texts, labels, data_version = load_training_data()
    pipe = build_pipeline()
    pipe.fit(texts, labels)
    bundle = {
        "pipeline": pipe,
        "model_version": f"intent-lr-{data_version}",
        "training_data_version": data_version,
        "feature_config": {k: list(v) if isinstance(v, tuple) else v for k, v in FEATURE_CONFIG.items()},
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_examples": len(texts),
        "labels": sorted(set(labels)),
    }
    if save:
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        joblib.dump(bundle, MODEL_PATH)
    return bundle


_BUNDLE: dict | None = None


def get_model() -> dict:
    """Load the cached model; retrain if missing or if the training data changed."""
    global _BUNDLE
    if _BUNDLE is not None:
        return _BUNDLE
    _, _, data_version = load_training_data()
    if os.path.exists(MODEL_PATH):
        try:
            bundle = joblib.load(MODEL_PATH)
            if bundle.get("training_data_version") == data_version:
                _BUNDLE = bundle
                return _BUNDLE
        except Exception:
            pass
    _BUNDLE = train(save=True)
    return _BUNDLE


def predict_proba(text: str) -> list[tuple[str, float]]:
    bundle = get_model()
    pipe = bundle["pipeline"]
    probs = pipe.predict_proba([text.lower()])[0]
    ranked = sorted(zip(pipe.classes_, probs), key=lambda x: x[1], reverse=True)
    return [(label, float(p)) for label, p in ranked]
