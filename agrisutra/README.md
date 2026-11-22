# 🌱 AgriSutra — Evidence-First Sales Assistant for SGB Agro Industries

> **Understand the need. Prove the match. Protect the deal.**
> No product claim without evidence. No discount without authority. No order without explicit confirmation.

AgriSutra is a **WhatsApp sales bot** with a browser cockpit. It turns a WhatsApp enquiry into a structured customer need, a defensible recommendation from
SGB's real seven-product catalogue, and — only after explicit confirmation — a clearly marked **mock** order.
Everything runs locally: a locally trained intent model, local BM25 retrieval, deterministic templates, SQLite.
No hosted LLM or external API is used.

The full specification lives in [`info_files/`](info_files/) (README, architecture, production blueprint).
This build implements milestones **M0–M6** plus the **WhatsApp channel (M7, Cloud API)**: customers chat on
WhatsApp, and salespeople watch and take over from the cockpit. CRM, inventory and payment adapters (rest of M7)
and production hardening (M8) remain the documented roadmap.

---

## Quick start

```bash
pip install -r requirements.txt
python run.py            # http://localhost:8000  (Windows: double-click run.bat)
```

```bash
python tests/run_all_tests.py        # 165 tests
python scripts/train_nlu.py          # retrain intent model + held-out report
python scripts/evaluate_asksmart.py  # AskSmart vs fixed-questionnaire baseline
python scripts/evaluate_sentences.py # natural-sentence conversations through the full engine
python scripts/extract_catalogue_images.py  # re-extract product photos from the official PDF
python scripts/whatsapp_simulator.py # chat with the WhatsApp bot from your terminal (no Meta account needed)
```

The intent model trains automatically on first start (≈1 s) and is cached in `models/` (git-ignored).

---

## The 3-minute demo (Tests tab → "Judge demo script")

| # | Customer says | What to show |
|---|---|---|
| 1 | *I need a brush cutter trolley* | Agent asks **attachment vs complete machine** — AskSmart tab shows why (blocking, highest information gain). |
| 2 | *I already have a backpack brush cutter* | Need Fingerprint fills; Backpack Trolley (₹4,300) recommended with page-1 citations; asks make/model because no fitment table exists. |
| 3 | *Will this fit my exact model?* | Exact fitment **UNKNOWN** — no "universal fit" promise; Truth Receipt shows `MISSING`. |
| 4 | *Actually, I have a sidepack machine* | `backpack` superseded (history kept), Sidepack Trolley (₹3,800) wins, old receipt superseded, counterfactual explains why Backpack is now excluded. |
| 5 | *Is the normal wheel barrow 95 litres?* | "95 litre" is only in page-3 SEO tags → `UNAPPROVED`, not stated as capacity. |
| 6 | *Can I get a discount?* | DealGuard → **NO_POLICY**; listed price restated; request queued to sales with a takeover packet. |
| 7 | *I'll take it* → **Confirm this preview** | Immutable preview v1 (MOCK) → mock order `MOCK-SGB-…`; a second confirmation is rejected. |

Finish on the **Replay** tab: every turn's intent, rule override, profile revision, catalogue version,
candidate gates, evidence statuses, DealGuard outcome and side effects.

More traps on the Tests tab: 43 cc artwork conflict, "looks good" ≠ consent, stock/delivery, out-of-catalogue,
no safe match, follow-up, opt-out, human takeover, duplicate webhook delivery.

---

## What's built (mapped to the spec)

| Milestone | Implementation |
|---|---|
| **M0 Product truth** | [`data/catalogue/sgb_catalogue.json`](data/catalogue/sgb_catalogue.json) — the 7 SGB variants, each fact with page/location and status. 95 L (SEO tag) and 43 cc (artwork) kept as flagged *observations*, not facts. Runtime "as provided" qualifiers kept. Content-hash catalogue version. [`app/catalogue/ingest.py`](app/catalogue/ingest.py) |
| **M1 Conversation core** | Word+char TF-IDF → Logistic Regression trained locally ([`app/nlu/model.py`](app/nlu/model.py)) + high-precision rules for risky intents ([`app/nlu/predict.py`](app/nlu/predict.py)). Append-only Need Fingerprint with provenance, confidence, supersession ([`app/dialogue/need_fingerprint.py`](app/dialogue/need_fingerprint.py)). |
| **M2 AskSmart** | `criticality × information gain × coverage − effort − privacy`, partitions over live candidates ([`app/dialogue/question_value.py`](app/dialogue/question_value.py)). |
| **M3 Recommendation** | PASS/FAIL/UNKNOWN/CONFLICTED per requirement → ELIGIBLE / UNVERIFIED / EXCLUDED; preference fit only from comparable known preferences (no fake %); hypothetical Counterfactual Fit ([`app/decision/fit.py`](app/decision/fit.py)). |
| **M4 Proofline** | Claim-level Truth Receipts (SUPPORTED / MISSING / CONFLICTED / UNAPPROVED) with file, page, field, catalogue version; only SUPPORTED claims are stated ([`app/catalogue/evidence.py`](app/catalogue/evidence.py)). Per-turn decision traces → Decision Replay. |
| **M5 Sales controls** | DealGuard with no-discount default ([`app/sales/dealguard.py`](app/sales/dealguard.py), [`data/policy/price_policy.json`](data/policy/price_policy.json)); versioned immutable previews; confirmation bound to the current preview; idempotency keys ([`app/sales/orders.py`](app/sales/orders.py)). |
| **M6 Handoff** | Takeover packet (transcript, need, products + reasons, receipt, DealGuard, open questions), ownership lock, bot pause/resume, agent replies ([`app/handoff/service.py`](app/handoff/service.py)). |
| Cockpit | Vanilla JS dual pane: chat simulator with guided chips, product cards, consent button; Need / Candidates / Truth Receipt / AskSmart / Replay / Handoff / Tests tabs ([`app/web/`](app/web/)). |

### API (`/api/v1`, OpenAPI at `/docs`)

`POST /sessions` · `POST /sessions/{id}/messages` (`client_message_id` = idempotency key) ·
`GET /sessions/{id}/summary | need-fingerprint | evidence | replay | messages | mock-order` ·
`POST /sessions/{id}/order-confirmation` (`preview_id`) · `POST /sessions/{id}/handoff | agent-message | resume-bot` ·
`GET /products[?family=]` · `GET /products/{id}/evidence` · `GET /catalogue/report` · `GET /handoffs`

---

## Measured results

Reported only from runs in this repo; re-run the scripts to reproduce.

| Check | Result | Source |
|---|---|---|
| Intent macro-F1, held-out (43 utterances) | **0.94** with rules · 0.91 model only | `scripts/train_nlu.py` → `data/evaluation/intent_report.json` |
| False order confirmations on held-out | **0** | same |
| AskSmart vs fixed questionnaire (7 oracle customers) | **2.71** vs 4.71 mean questions; 7/7 resolved by both | `scripts/evaluate_asksmart.py` → `data/evaluation/asksmart_report.json` |
| Natural-sentence conversations, first-seen | **34 / 42** fully understood (81%) before any fixes; 42 / 42 now (kept as regression tests) | `scripts/evaluate_sentences.py` → `data/evaluation/sentences_report.json` |
| General conversation (no product named), unseen phrasings | **13 / 20** on first sight; 20 / 20 after fixes. Before the conversation layer, only 2 of 30 everyday messages got a relevant reply | `data/evaluation/conversation_heldout.json`, `tests/test_conversation.py` |
| Open questions, recommendations, checkout (unseen) | **9 / 15** on first sight; 15 / 15 after fixes | `data/evaluation/conversation_heldout.json` (round 3), `tests/test_answers_checkout.py` |
| Test suite | **165 passing**: open questions & checkout, general conversation, WhatsApp channel, product images, natural sentences, catalogue truth, NLU safety, fingerprint, AskSmart, hard constraints, 18 adversarial scenarios, API contracts, full demo | `tests/` |

**Caveats, stated plainly:** the training and held-out utterances were authored for this prototype (no
second-reviewer labelling yet) and the held-out set is small; AskSmart is evaluated with simulated truthful
customers, not a user study.

---

## Run it on WhatsApp

The same sales engine answers on WhatsApp through Meta's **WhatsApp Business Platform (Cloud API)**.
The adapter lives in [`app/channels/whatsapp/`](app/channels/whatsapp/).

**What a customer gets on WhatsApp:** natural replies in WhatsApp formatting (*bold*, _italic_, comparison
tables as lists), the product **photo** from the official catalogue with its listed price, **reply buttons**
(≤3 short options) or a **list** (longer options), and an order preview whose **Confirm order** button is bound to
that exact preview version. An old button can never confirm a newer preview.

**Safety built in (architecture.md §15):**
- `X-Hub-Signature-256` HMAC is checked on the raw body before anything is parsed. Unsigned webhooks are
  refused unless `WHATSAPP_ALLOW_UNSIGNED=true` (local testing only).
- Webhooks are stored in an inbox and deduplicated by message ID, so Meta's retries never advance a conversation
  twice. `200 OK` is returned at once and the dialogue runs in the background.
- Each customer's messages are processed **one at a time, in WhatsApp-timestamp order**, even when they
  arrive in a burst.
- Nothing is sent while a salesperson owns the chat, and nothing is sent outside the **24-hour customer-service
  window**, because approved templates aren't set up. Every attempt, sent or suppressed, lands in the outbox
  and shows in the cockpit.
- Phone numbers are masked in the cockpit and kept out of audit logs. Failed events are retried on restart.
- **Dry run is the default:** replies are recorded exactly as they would be sent, and nothing leaves the
  machine until you set `WHATSAPP_SEND_MODE=live`.

### Try it without a Meta account

```bash
python run.py
python scripts/whatsapp_simulator.py          # a fake phone that sends signed webhooks; type numbers to tap buttons
```

Then open the cockpit → **WhatsApp** tab, pick the conversation and watch it live. To reply as a salesperson,
use **Handoff → Take over**; your reply is delivered to the customer's WhatsApp.

### Connect a real WhatsApp number

1. In [Meta for Developers](https://developers.facebook.com/), create an app → add **WhatsApp**. Meta provides a
   test business number. Add your own phone as a test recipient.
2. Expose this server over HTTPS, for example with a tunnel (`cloudflared tunnel --url http://localhost:8000` or
   `ngrok http 8000`). Meta only calls public HTTPS URLs.
3. Fill in `.env` (see `.env.example`):
   - `WHATSAPP_VERIFY_TOKEN`: any random string you choose.
   - `WHATSAPP_APP_SECRET`: App settings → Basic → App secret.
   - `WHATSAPP_ACCESS_TOKEN`: a system-user token with `whatsapp_business_messaging`. Keep it out of git.
   - `WHATSAPP_PHONE_NUMBER_ID`: from WhatsApp → API Setup.
   - `PUBLIC_BASE_URL`: your tunnel URL, so product photos can be linked. If unset, photos are uploaded to Meta.
   - `WHATSAPP_SEND_MODE=live`
4. WhatsApp → Configuration → Webhook: set the callback URL to `https://<your-tunnel>/webhooks/whatsapp` with
   your verify token, then **subscribe to the `messages` field**.
5. Restart `python run.py`. The cockpit's WhatsApp tab lists any missing setting. Message the test number from
   your phone.

The Graph API version defaults to `v25.0` (`WHATSAPP_API_VERSION`). Check Meta's changelog before going live.
Before using a real customer-facing number, also work through the WhatsApp opt-in, template and
business-verification requirements and the release gates in `info_files/prod.md`. This build sends only replies
inside the 24-hour window and no marketing or follow-up templates.

## Official catalogue & product photos

The official `SGB_Agro_Industries_Product_Catalogue_Updated.pdf` lives in `data/catalogue/source/`. On load its
SHA-256 is computed and shown on every Truth Receipt. `scripts/extract_catalogue_images.py` pulls the photo for each
product out of the PDF into `data/assets/products/` with a manifest (page, embedded image name, image SHA-256). The
catalogue loader warns if the photos came from a different PDF version.

Photos appear on recommendation cards, under fact answers and comparisons, on the order preview and in the Truth
Receipt (click to enlarge). Every fact was re-checked against the PDF text; the artwork was reviewed too, and anything
that appears **only** in artwork or SEO tags is recorded as a flagged observation, never a fact:

| Source item | Treatment |
|---|---|
| 143R Stable / WALBRO artwork shows "43 cc"; spec tables don't | `CONFLICTED` — not stated |
| 143R artwork shows the machine on a push trolley; spec tables don't list one | push-trolley requirement → `UNVERIFIED`, not excluded |
| p.6 artwork headline reads "SGB 143R STABLE" / "WALLBRO" on the WALBRO page | flagged for SGB to correct |
| Varsha artwork lists 2-stroke, spoke-20 wheels, accessory kit | artwork-only — in the receipt, not stated as spec |
| SEO tags: "95 litre", "universal brush cutter trolley", "high load capacity", "rust resistant" | marketing tags — never stated as capacity, fitment or durability |

## Understanding natural sentences

Customers rarely type a product name. The NLU therefore works in three layers (`app/nlu/`):

1. **Normalise** ([`normalize.py`](app/nlu/normalize.py)) — spelling (`whelbarow`, `brushcuter`, `warrenty`), Hinglish
   (`mitti`, `ghaas`, `chahiye`, `hazar`), money shorthand (`15k`, `4 thousand`, `1.5 lakh`) and number words. The original
   text is stored untouched; the cockpit's AskSmart tab shows what was understood.
2. **Read the need from the sentence** ([`predict.py`](app/nlu/predict.py)):
   - use cases → product line: *"shift cement bags"* → wheel barrow; *"the weeds have grown too much"* → brush cutter;
   - descriptions → configuration: *"I carry it on my back"* → backpack; *"it hangs at my side"* → sidepack;
   - ownership vs. need: *"I bought a Honda cutter… want wheels for it"* → trolley attachment, while
     *"I have a 5 acre farm"* is not owning a machine;
   - requirements vs. preferences vs. indifference: *"at least a year of warranty"* (hard), *"the longest warranty"*
     (ranking preference), *"warranty doesn't matter"* (never asked again);
   - questions vs. requests: *"which brush cutter do you have with a 1 year warranty?"* is a product search,
     *"can the dumper carry 100 kg?"* is a fact question (answered as MISSING), *"is the walbro worth it over the stable?"*
     is a comparison (with key differences spelled out).
3. **Intent model + safety rules** — the local classifier is trained on phrases *and* full sentences (289 examples);
   rules still guard confirmation, opt-out, discount and human requests.

## Talking like a person, not a menu

Most real messages don't name a product: *"hi, how are you"*, *"what do you sell?"*, *"which is cheapest"*,
*"the second one"*, *"is it worth it?"*, *"do you deliver to Pune?"*, *"my old machine broke"*, *"thanks"*.
[`app/dialogue/conversation.py`](app/dialogue/conversation.py) recognises these **conversational acts** with
precise rules (no LLM) and answers the way a salesperson would:

- **Small talk & identity:** greets, says how it's doing, admits it's an automated assistant, and offers a person.
  *"Are you a real person?"* is answered as a question; it doesn't trigger a handoff.
- **Catalogue overview, cheapest / most expensive, best seller:** answered from the catalogue with prices. "Best"
  is explained by job rather than invented.
- **References:** *"the first one"*, *"the cheaper one"*, *"tell me more"* resolve against what was just shown.
- **Honest opinion:** *"is it worth it?"* gives a view built only from supported facts, including what's
  unconfirmed (43 cc, 95 L stay unconfirmed here too).
- **Delivery, payment, stock, spare parts, company details:** says plainly that the catalogue doesn't cover it and
  that the sales team confirms it. Nothing is promised.
- **Vague needs** (*"something for my farm"*, *"2 acres of sugarcane"*, *"kuch chahiye khet ke liye"*): asks about the
  **job** (carry loads / cut grass / trolley for my cutter) with starting prices, instead of a product menu.
- **Out-of-range items** (sprayers, tractors): says so and suggests how SGB's range might still help.
- **Thanks / bye / ok:** never closes the enquiry by accident and keeps the next step open. Repeated questions are
  reworded, and comparisons end with a short **"My take"** built from the compared facts.

Rule-backed safety intents (opt-out, confirmation, explicit human or discount requests, cancel) always win over a
conversational act; a weak model-only guess does not.

## Answering, recommending and closing the sale (still no LLM)

The challenge rules forbid a hosted LLM, so the "LLM-like" behaviour is built from catalogue facts and rules:

- **Open questions** ([`app/dialogue/answers.py`](app/dialogue/answers.py)):
  - *"can the walbro cut sugarcane?"* answers from the described uses and is honest when the catalogue doesn't mention it.
  - *"which brush cutter runs longest / is lightest / most powerful?"* ranks supported facts, and says when they tie or are missing.
  - *"can my father use it easily?"* answers from what the catalogue does state (push-type trolley).
  - *"price of walbro and its warranty"*, *"does the varsha come with wheels"* and *"the stable one"* are all understood.
  - A question never becomes a requirement.
- **Recommendations with a reason:** *"My pick is the 143R WALBRO: it runs about 3 hours per the catalogue, the
  longest in the range, which suits long working days. The Varsha is also a good fit if you'd rather save ₹7,000."*
  *"Long hours"*, *"all day"* and large farms (≥ 3 acres) rank by running time.
- **Closing the sale** ([`app/sales/checkout.py`](app/sales/checkout.py)):
  - **Several products in one order:** *"2 dumper barrows and 1 sidepack trolley"* becomes one preview, and you can
    say *"make the dumper 3"* or *"remove the trolley"*.
  - **After confirming**, the bot asks for a **name and town/district**. No street address is collected; the customer
    can skip.
  - It then sends a **final summary** with what happens next, and queues an `ORDER_FOLLOW_UP` handoff so the sales
    team finalises stock, GST, delivery and payment.
  - *"What happens next?"* / *"is my order confirmed?"* repeat the summary.
  - Contact details are stored only on the order and never in the audit log.

## Definition of done (challenge build, from `info_files/README.md` §11)

- [x] Seven supplied variants ingested without inventing missing fields
- [x] Page/field provenance retained for facts and extracted images (page, embedded image name, SHA-256 per photo)
- [x] Guided selection and free text
- [x] Multi-turn state survives follow-ups and corrections
- [x] Unknown / contradictory facts disclosed, not guessed
- [x] Hard constraints before soft ranking
- [x] AskSmart compared with a fixed baseline
- [x] Truth Receipt and "why not" for each recommendation
- [x] No discount without an approved policy record
- [x] Human takeover with transcript + structured context; bot paused while human owns the chat
- [x] Explicit confirmation of the current preview; records marked `MOCK`
- [x] Repeated confirmation / delivery cannot duplicate records
- [x] Session isolation and adversarial grounding tests pass
- [x] Model scores measured on a held-out set

## Known gaps

- Live conversation state is in memory (DB keeps messages, traces, orders and handoffs); a server restart starts conversations fresh.
- Frontend is vanilla JS rather than the spec's suggested React/Vite; the API contract is the same.
- Live conversation state being in memory matters more on WhatsApp: after a restart, a returning customer starts a fresh conversation (their history and orders stay in the DB).
- WhatsApp: no approved message templates (so no follow-ups outside the 24-hour window), text and button input only (voice, images and location get a polite "please type" reply), and a single process (the background worker is in-process, not a separate queue).
- Not built: CRM / inventory / payment adapters, PostgreSQL, a separate worker queue. See `info_files/prod.md`.

## Layout

```text
app/
  api/routes.py            /api/v1 endpoints
  channels/whatsapp/       webhook (verify, signature, inbox), formatter, transport (dry-run / Graph API), policy gates
  catalogue/               ingest (versioned), retrieval (BM25), evidence (Truth Receipts)
  decision/fit.py          hard gates, ranking, counterfactuals
  dialogue/                manager (turn loop), need_fingerprint, question_value (AskSmart)
  handoff/service.py       takeover packet, bot pause/resume
  nlu/                     model.py (local training), predict.py (rules + slots)
  sales/                   dealguard, orders (preview/confirm), responses (templates)
  storage/database.py      SQLite: sessions, messages, traces, orders, handoffs, follow-ups, audit
  web/                     cockpit UI
data/
  catalogue/sgb_catalogue.json   policy/price_policy.json
  training/intents.json          evaluation/intents_heldout.json (+ generated reports)
scripts/                   train_nlu.py, evaluate_*.py, extract_catalogue_images.py, whatsapp_simulator.py
tests/                     unittest suite (run_all_tests.py)
```
