"""Recommendation core (M3): hard constraints → ranking → Counterfactual Fit.

Each product is checked against every known requirement as PASS / FAIL /
UNKNOWN / CONFLICTED. Any hard FAIL excludes the product; any hard UNKNOWN or
CONFLICTED makes it UNVERIFIED. Preference ranking runs on eligible products only.
"""

import uuid

from app.catalogue.ingest import CatalogueStore
from app.dialogue.need_fingerprint import NeedFingerprint

ALGORITHM_VERSION = "fit-v1"
PASS, FAIL, UNKNOWN, CONFLICTED = "PASS", "FAIL", "UNKNOWN", "CONFLICTED"
ELIGIBLE, UNVERIFIED, EXCLUDED = "ELIGIBLE", "UNVERIFIED", "EXCLUDED"

PREFERENCE_WEIGHTS = {"carburetor_preference": 1.0, "intended_use": 1.0, "dumper_not_needed": 0.5}

USE_SYNONYMS = {"cement": "construction materials", "sand": "construction materials", "bricks": "construction materials",
                "gravel": "construction materials", "construction": "construction materials",
                "compost": "manure", "fertiliser": "agricultural materials", "fertilizer": "agricultural materials",
                "fodder": "farm produce", "garden": "agricultural materials", "weeds": "weed",
                "bushes": "cutting"}

TROLLEY_AMBIGUOUS_POOL ={"BCT-SIDE", "BCT-BACK", "VARSHA-TROLLEY"}


def _hours(v: str) -> float:
    import re
    m = re.search(r"(\d+(?:\.\d+)?)\s*hour", str(v))
    return float(m.group(1)) if m else 0.0


def _src(fact: dict | None, field: str) -> dict | None:
    if not fact:
        return None
    return {"field": field, "page": fact["source"]["page"], "location": fact["source"]["location"]}


def _check(criterion: str, label: str, result: str, customer_value, product_value, source=None, reason="") -> dict:
    return {"criterion": criterion, "label": label, "result": result, "customer_value": customer_value,
            "product_value": product_value, "source": source, "reason": reason}


class FitEngine:
    def __init__(self, store: CatalogueStore):
        self.store = store

    # ── Candidate pool ───────────────────────────────────────

    def pool(self, fp: NeedFingerprint, family_hint: str | None = None) -> list[dict]:
        fam = fp.get("product_family")
        if fam:
            return self.store.get_all_products()           # family is checked as a hard requirement
        if family_hint == "trolley_ambiguous":
            return [p for p in self.store.get_all_products() if p["product_id"] in TROLLEY_AMBIGUOUS_POOL]
        return self.store.get_all_products()

    # ── Per-product evaluation ───────────────────────────────

    def evaluate_product(self, p: dict, known: dict) -> dict:
        checks: list[dict] = []
        facts = p["facts"]

        fam = known.get("product_family")
        if fam is not None:
            checks.append(_check("product_family", "Product family", PASS if p["family"] == fam else FAIL,
                                 fam, p["family"], {"field": "family", "page": p["price"]["source"]["page"], "location": "product title"},
                                 "" if p["family"] == fam else f"{p['short_name']} is a {p['family_label']}"))

        need = known.get("need_type")
        if need is not None and p["family"] in ("brush_cutter_trolley_attachment", "brush_cutter_machine"):
            is_attachment = p["family"] == "brush_cutter_trolley_attachment"
            ok = (need == "attachment") == is_attachment
            checks.append(_check("need_type", "Attachment or complete machine", PASS if ok else FAIL, need,
                                 "attachment" if is_attachment else "complete machine",
                                 _src(facts.get("product_type"), "facts.product_type.value"),
                                 "" if ok else ("this is a complete machine" if is_attachment is False else
                                                "this is an attachment for a machine you already own")))

        cfg = known.get("trolley_configuration")
        if cfg is not None and p["family"] == "brush_cutter_trolley_attachment":
            f = facts.get("configuration")
            ok = f and f["value"] == cfg
            checks.append(_check("trolley_configuration", "Brush cutter configuration", PASS if ok else FAIL,
                                 cfg, f["value"] if f else None, _src(f, "facts.configuration.value"),
                                 "" if ok else f"built for {f['value'] if f else 'another'} brush cutters"))

        if known.get("exact_fitment_required") and p["family"] == "brush_cutter_trolley_attachment":
            model = known.get("machine_model") or "your machine"
            checks.append(_check("exact_fitment", "Exact machine fitment", UNKNOWN, model, None, None,
                                 "the catalogue has no make/model fitment matrix"))

        dumper = known.get("dumper_required")
        if dumper is True and p["family"] == "wheel_barrow":
            f = facts.get("tipping_mechanism")
            ok = f and not f.get("negative")
            checks.append(_check("dumper_required", "Dumper / tipping needed", PASS if ok else FAIL, True,
                                 f["value"] if f else None, _src(f, "facts.tipping_mechanism.value"),
                                 "" if ok else "no dumper mechanism on this variant"))

        cap = known.get("capacity_required_litres")
        if cap is not None and p["family"] == "wheel_barrow":
            obs = self.store.get_observation(p["product_id"], "capacity")
            checks.append(_check("capacity_required_litres", "Required capacity", UNKNOWN, f"{cap} L",
                                 obs["value"] + " (unverified SEO tag)" if obs else None,
                                 {"field": "observations.capacity", "page": obs["source"]["page"], "location": obs["source"]["location"]} if obs else None,
                                 "capacity is not in an approved specification"))

        push = known.get("needs_push_trolley")
        if push is not None and p["family"] == "brush_cutter_machine":
            f = facts.get("configuration")
            is_push = bool(f and "push" in f["value"])
            ok = is_push == push
            art = self.store.get_observation(p["product_id"], "push_trolley")
            if push and not is_push and art:
                # Artwork shows a trolley the spec table doesn't list: neither PASS nor FAIL until SGB confirms.
                checks.append(_check("needs_push_trolley", "Push-type trolley", UNKNOWN, push,
                                     "trolley shown in artwork only",
                                     {"field": "observations.push_trolley", "page": art["source"]["page"],
                                      "location": art["source"]["location"]},
                                     "a trolley appears in the artwork but not in the specification table"))
                ok = None
            if ok is not None:
                checks.append(_check("needs_push_trolley", "Push-type trolley", PASS if ok else FAIL, push,
                                 f["value"] if f else "no push-type trolley listed", _src(f, "facts.configuration.value") or
                                 {"field": "facts", "page": p["price"]["source"]["page"], "location": "specification table"},
                                 "" if ok else ("push-type trolley is listed only for the Varsha model" if push else
                                                "this model is supplied with a push-type trolley")))

        wmin = known.get("warranty_min_months")
        if wmin is not None and p["family"] == "brush_cutter_machine":
            f = facts.get("engine_warranty")
            if not f:
                checks.append(_check("warranty_min_months", "Minimum engine warranty", UNKNOWN, wmin, None, None,
                                     "warranty is not stated"))
            else:
                ok = f.get("months", 0) >= wmin
                checks.append(_check("warranty_min_months", "Minimum engine warranty", PASS if ok else FAIL,
                                     f"≥ {wmin} months", f["value"], _src(f, "facts.engine_warranty.value"),
                                     "" if ok else ("no engine warranty is listed" if not f.get("months")
                                                    else f"engine warranty is {f['value']}")))

        budget = known.get("budget")
        if budget is not None:
            price = p["price"].get("amount")
            if price is None:
                checks.append(_check("budget", "Budget", UNKNOWN, budget, None, None, "price not listed"))
            else:
                ok = price <= budget
                checks.append(_check("budget", "Budget", PASS if ok else FAIL, f"≤ ₹{budget:,}", f"₹{price:,}",
                                     {"field": "price.amount", "page": p["price"]["source"]["page"], "location": "price label"},
                                     "" if ok else f"listed at ₹{price:,}, ₹{price - budget:,} over budget"))

        results = [c["result"] for c in checks]
        if FAIL in results:
            status = EXCLUDED
        elif UNKNOWN in results or CONFLICTED in results:
            status = UNVERIFIED
        else:
            status = ELIGIBLE

        pref_fit, pref_details = self._preference_fit(p, known) if status != EXCLUDED else (None, [])
        return {"product_id": p["product_id"], "name": p["name"], "short_name": p["short_name"],
                "family": p["family"], "price": p["price"].get("amount"), "status": status, "checks": checks,
                "preference_fit": pref_fit, "preference_details": pref_details}

    def _preference_fit(self, p: dict, known: dict) -> tuple[float | None, list[str]]:
        total, matched, details = 0.0, 0.0, []
        carb = known.get("carburetor_preference")
        f = p["facts"].get("carburetor")
        if carb and f:                                   # comparable only where the catalogue states a carburetor
            w = PREFERENCE_WEIGHTS["carburetor_preference"]
            total += w
            if carb in f["value"].lower():
                matched += w
                details.append(f"matches carburetor preference ({f['value']})")
            else:
                details.append(f"carburetor is {f['value']}, not {carb}")
        if known.get("warranty_preference") == "longest" and p["family"] == "brush_cutter_machine":
            f = p["facts"].get("engine_warranty")
            if f:
                total += 1.0
                matched += min(f.get("months", 0), 12) / 12
                details.append(f"engine warranty: {f['value']} (you asked for the longest)")
        if known.get("run_time_preference") == "longest" and p["family"] == "brush_cutter_machine":
            f = p["facts"].get("run_time")
            hours = _hours(f["value"]) if f else 0.0
            total += 1.0
            matched += min(hours, 3.0) / 3.0
            details.append(f"running time: {f['value']} as provided (you need long working hours)" if f
                           else "running time not stated in the catalogue")
        use = known.get("intended_use")
        f = p["facts"].get("described_uses")
        if use and f:
            w = PREFERENCE_WEIGHTS["intended_use"]
            total += w
            # A customer's material maps to the catalogue's wording: cement/sand are "construction materials".
            hit = []
            for u in (x.strip() for x in use.split(",") if x.strip()):
                term = USE_SYNONYMS.get(u, u)
                if term.split()[0] in f["value"]:
                    hit.append(u if term == u or u.startswith(term) else f"{u} (as {term})")
            if hit:
                matched += w
                details.append(f"catalogue describes use for {', '.join(hit)}")
            else:
                details.append(f"catalogue does not list {use} among described uses")
        if known.get("dumper_required") is False and p["family"] == "wheel_barrow":
            w = PREFERENCE_WEIGHTS["dumper_not_needed"]
            total += w
            if p["facts"].get("tipping_mechanism", {}).get("negative"):
                matched += w
                details.append("standard model; you said tipping isn't needed")
            else:
                details.append("has a dumper mechanism you said you don't need")
        if total == 0:
            return None, []
        return round(matched / total, 2), details

    # ── Full run ─────────────────────────────────────────────

    def recommend(self, fp: NeedFingerprint, family_hint: str | None = None) -> dict:
        known = fp.known()
        pool = self.pool(fp, family_hint)
        evals = [self.evaluate_product(p, known) for p in pool]
        eligible = [e for e in evals if e["status"] == ELIGIBLE]
        unverified = [e for e in evals if e["status"] == UNVERIFIED]
        excluded = [e for e in evals if e["status"] == EXCLUDED]

        def sort_key(e):
            return (-(e["preference_fit"] if e["preference_fit"] is not None else -1), e["price"] or 0)
        eligible.sort(key=sort_key)
        unverified.sort(key=sort_key)

        viable = eligible + unverified
        for e in excluded:
            e["counterfactual"] = self.counterfactual(e, viable)

        return {
            "recommendation_id": f"rec_{uuid.uuid4().hex[:10]}",
            "fingerprint_revision": fp.revision,
            "catalogue_version": self.store.version,
            "algorithm_version": ALGORITHM_VERSION,
            "pool_size": len(pool),
            "family_hint": family_hint,
            "evaluations": eligible + unverified + excluded,
            "eligible": [e["product_id"] for e in eligible],
            "unverified": [e["product_id"] for e in unverified],
            "excluded": [e["product_id"] for e in excluded],
        }

    @staticmethod
    def counterfactual(ev: dict, viable: list[dict]) -> dict:
        """Hypothetical only — never mutates the Need Fingerprint."""
        fails = [c for c in ev["checks"] if c["result"] == FAIL]
        alt = viable[0] if viable else None
        if len(fails) == 1:
            f = fails[0]
            pv, cv = f["product_value"], f["customer_value"]
            would = {
                "budget": f"if your budget were at least {pv}",
                "dumper_required": "if a standard (non-tipping) barrow were acceptable",
                "needs_push_trolley": "if a regular machine without trolley were acceptable" if cv is True
                else "if a push-type trolley machine were acceptable",
                "warranty_min_months": f"if an engine warranty of '{pv}' were acceptable",
                "trolley_configuration": f"if your brush cutter were a {pv} model",
                "need_type": f"if you wanted a {pv}",
                "product_family": f"if you were looking for a {str(pv).replace('_', ' ')}",
            }.get(f["criterion"], f"if {f['label'].lower()} were {pv}")
        else:
            would = f"only if {len(fails)} requirements changed"
        return {
            "hypothetical": True,
            "failed_criteria": [{"criterion": f["criterion"], "label": f["label"], "reason": f["reason"],
                                 "customer_value": f["customer_value"], "product_value": f["product_value"],
                                 "source": f["source"]} for f in fails],
            "would_qualify": would,
            "current_alternative": {"product_id": alt["product_id"], "name": alt["short_name"]} if alt else None,
        }
