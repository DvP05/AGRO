"""Text normalisation before NLU: spelling, Hinglish, number words, money shorthand.

The customer's original text is always stored unchanged; only the NLU sees the
normalised form, and the trace records both.
"""

import difflib
import re

# Multi-word / compound fixes applied first (order matters).
PHRASES = [
    (r"\bwheel[\s-]*barr?o?w?s?\b", "wheel barrow"),
    (r"\bwheelbarr?ows?\b", "wheel barrow"),
    (r"\bbrush[\s-]*cutt?ers?\b", "brush cutter"),
    (r"\bbrushcutt?ers?\b", "brush cutter"),
    (r"\btroll(y|ey|ie)s?\b", "trolley"),
    (r"\bside[\s-]+pack\b", "sidepack"),
    (r"\bback[\s-]+pack\b", "backpack"),
    (r"\bgrass[\s-]*cutt?ers?\b", "grass cutter"),
    (r"\bweed[\s-]*cutt?ers?\b", "weed cutter"),
    (r"\bcarb(urettor|uretor|urator|urater)s?\b", "carburetor"),
    (r"\bwarrant(y|ee|ie)s?\b", "warranty"),
    (r"\bguarant(ee|y)\b", "warranty"),
]

# Hinglish / regional words → English equivalents the slot rules understand.
HINGLISH = {
    "mujhe": "i", "muje": "i", "hume": "we", "chahiye": "need", "chahie": "need", "chaiye": "need",
    "mitti": "soil", "matti": "soil", "ret": "sand", "reti": "sand", "gobar": "manure", "khad": "manure",
    "khaad": "manure", "eet": "bricks", "eent": "bricks", "ghaas": "grass", "ghas": "grass",
    "jhadi": "bushes", "jhadiyan": "bushes", "kaatna": "cut", "katne": "cut", "kaatne": "cut",
    "uthane": "carry", "uthana": "carry", "kitna": "how much", "kitne": "how much", "sasta": "cheaper",
    "mehenga": "too expensive", "mahanga": "too expensive", "hazar": "thousand", "hajar": "thousand",
    "haan": "yes", "nahi": "no", "theek": "ok", "aur": "and", "ke": "", "liye": "for", "ka": "", "ki": "",
}

NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20, "a couple of": 2, "a couple": 2}

# Domain words worth spell-correcting (tokens of length ≥ 6 only, close matches only).
VOCAB = ["wheelbarrow", "brushcutter", "wheel", "barrow", "trolley", "brush", "cutter", "sidepack", "backpack", "dumper", "warranty", "walbro",
         "carburetor", "stable", "varsha", "tipping", "discount", "delivery", "compare", "confirm", "quantity",
         "budget", "manure", "cement", "bricks", "weeds", "bushes", "orchard", "attachment", "machine", "machines",
         "complete", "purchase", "available", "capacity", "stroke", "engine", "running"]
_PROTECTED = set(VOCAB) | {"tipped", "dumped", "cutting", "cutters", "better", "matter", "rather", "barrel",
                           "border", "number", "summer", "winter", "garden", "carrier", "worker", "workers",
                           "farmer", "farmers", "bunder", "wheels", "rubber", "timber", "stables", "trouble"}


def _fix_token(tok: str) -> str:
    if len(tok) < 6 or tok in _PROTECTED or not tok.isalpha():
        return tok
    if any(tok.startswith(v) for v in VOCAB if len(v) >= 5):     # inflections: confirmed, trolleys, compared
        return tok
    m = difflib.get_close_matches(tok, VOCAB, n=1, cutoff=0.84)
    return m[0] if m else tok


def _money(m: re.Match) -> str:
    num = float(m.group(1).replace(",", ""))
    unit = m.group(2)
    mult = 100000 if unit.startswith("lakh") or unit.startswith("lac") else 1000
    return str(int(num * mult))


def normalize(text: str) -> str:
    t = " " + text.lower().replace("’", "'").replace("₹", " rs ") + " "
    t = re.sub(r"\brs\s*\.", "rs ", t)
    for pat, rep in PHRASES:
        t = re.sub(pat, rep, t)
    # spelling fixes on remaining domain-like tokens (before Hinglish so "khaad" etc. survive)
    t = re.sub(r"[a-z]+", lambda m: _fix_token(m.group(0)), t)
    t = re.sub(r"\b[a-z]+\b", lambda m: HINGLISH.get(m.group(0), m.group(0)), t)
    for pat, rep in PHRASES:                       # re-apply after spelling fixes ("wheel barow" → "wheel barrow")
        t = re.sub(pat, rep, t)
    for w, n in sorted(NUMBER_WORDS.items(), key=lambda x: -len(x[0])):
        t = re.sub(rf"\b{w}\b", str(n), t)
    # money shorthand: 15k, 15 thousand, 1.5 lakh, 15 hazar
    t = re.sub(r"\b(\d+(?:\.\d+)?)\s*(k|thousand|lakhs?|lacs?)\b", _money, t)
    return re.sub(r"\s+", " ", t).strip()
