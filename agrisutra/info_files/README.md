# AgriSutra — Evidence-First AI Sales Executive for SGB Agro Industries

> **Understand the need. Prove the match. Protect the deal.**  
> *No product claim without evidence. No discount without authority. No order without explicit confirmation.*

AgriSutra is a catalogue-grounded conversational sales system for SGB Agro Industries. It is designed to turn a Meta Ads / WhatsApp enquiry into a structured understanding of the customer, a defensible product recommendation, a controlled sales conversation, and—only after customer confirmation—a correctly recorded order.

The product is deliberately more than a chatbot. Its decision engine keeps a live **Need Fingerprint**, asks the next useful question, filters products against mandatory requirements, produces a claim-level **Truth Receipt**, explains why alternatives were excluded, and knows when to stop and transfer the conversation to a human salesperson.

This repository specification defines two deployment modes so the hackathon prototype can be delivered without pretending that production integrations already exist:

| Mode | What it proves | Integrations |
|---|---|---|
| **Challenge / local prototype** | Catalogue search, locally trained language understanding, multi-turn discovery, explainable matching, evidence receipts, simulated lead and mock order | Browser chat simulator; local SQLite; no live customer messaging or payment |
| **Production deployment** | Real WhatsApp enquiries, governed pricing, follow-up, CRM/order workflow, human takeover, operational monitoring | WhatsApp Business Platform Cloud API, approved CRM / inventory / payment systems, PostgreSQL and a worker queue |

See [architecture.md](architecture.md) for the system design and [prod.md](prod.md) for production requirements, deployment, operating procedures and release gates.

---

## 1. The problem

The existing project brief describes roughly **200–250 product enquiries per day** arriving through Meta ads and WhatsApp. The supplied SGB flowchart shows a sales journey that begins with a click-to-WhatsApp enquiry, offers guided product choices, accepts free text, explains products and images, qualifies usage, handles price objections, supports follow-up, and transfers difficult cases to the sales team.

The current product information and flow are distributed across a PDF catalogue and a separate flowchart. A conventional chatbot can easily produce a fluent but unsupported answer when the customer asks about stock, discount, compatibility, load capacity, warranty, or delivery. AgriSutra makes those boundaries explicit and operational.

### Product goals

1. Understand free-text and guided enquiries without forcing every customer through a menu.
2. Preserve conversation context and customer corrections across turns.
3. Recommend only products that satisfy known mandatory requirements.
4. Answer product questions using the approved, versioned catalogue—not model memory.
5. Explain the positive match, the trade-offs, and the reason a product was not selected.
6. Negotiate only within a current, approved pricing policy.
7. Escalate to a salesperson with the conversation and decision context intact.
8. Capture orders only after explicit confirmation and distinguish a demo mock order from a real production order.

## 2. What makes AgriSutra stand out

AgriSutra is built around a **proof-carrying sales decision loop**. The customer gets a natural conversation; the salesperson and evaluator can inspect the evidence and decision path behind it.

### Core innovations

| Capability | What it does | Why it matters |
|---|---|---|
| **Need Fingerprint** | Maintains a live, structured record of the customer's use case, budget, product variant, quantity, must-haves, preferences, unknowns, corrections and provenance | Makes the system state visible and prevents customers from repeating themselves |
| **AskSmart / Question Value Engine** | Selects the next question based on how much it could reduce uncertainty among viable products, weighted by business importance and customer effort | Replaces generic questionnaires with purposeful discovery |
| **Truth Receipt / Proofline** | Attaches each product claim to a product ID, catalogue version, source field and source location; displays missing or conflicting evidence | Makes factual grounding inspectable rather than a promise |
| **Counterfactual Fit Lab** | Shows why a candidate passed or failed and which customer requirement would need to change for it to qualify | Turns “why this product?” and “why not that one?” into visible, useful explanations; never overrides a hard constraint |
| **DealGuard** | Enforces approved price, discount, validity and approval rules before any negotiation promise is sent | Prevents the bot from inventing discounts or committing beyond authority |
| **Human Takeover Packet** | Gives the assigned salesperson the transcript, Need Fingerprint, candidate comparison, Truth Receipts, unresolved questions and reason for handoff | Makes human escalation a continuation of the same sales journey, not a restart |
| **Decision Replay** | Reconstructs the chain from customer message to extracted need, catalogue snapshot, rule decision, evidence and reply | Supports debugging, demonstrations, audit and supervised improvement |
| **Catalogue Change Radar** | Diffs approved catalogue versions and flags open recommendations or quotes affected by a changed price/specification | Stops an old quote or recommendation from silently appearing current after the source changes |

### The memorable demonstration

A judge changes one requirement—such as the trolley configuration or budget—and watches the Need Fingerprint update, the candidate set recompute, and the old Truth Receipt become superseded. The judge then asks for a fact that the catalogue does not contain. AgriSutra marks the field as unknown and offers a human handoff rather than bluffing. The final reveal is the Decision Replay: the judge can inspect the exact message, profile revision, catalogue version, failed / passed requirements and evidence behind the response. This is the product's defining “wow” moment: **the system can show its reasoning boundary, not merely its answer.**

These capabilities are an engineering proposal, not a claim of patentability or guaranteed novelty. Any intellectual-property claim requires a separate prior-art search and legal assessment.

---

## 3. Scope and delivery strategy

### Challenge prototype

Build and demonstrate the complete decision workflow in a browser-based simulator. Use a locally trained intent classifier, catalogue-derived entity aliases, deterministic extraction and validation rules, local retrieval, explicit state transitions, and template-based response composition. Persist demo sessions and mock orders locally.

**Do not represent this build as connected to WhatsApp, a live CRM, inventory, payment or delivery systems.** Those integrations belong to the production phase unless the organisers explicitly require and authorise them for the challenge.

### Production system

Add the WhatsApp Business Platform adapter, public HTTPS webhook endpoint, approved message templates, consent and opt-out handling, CRM adapter, approved price/discount policy, inventory source, order management and human-agent console. Use the safeguards and operational gates in [prod.md](prod.md).

### Challenge-rule caveat

The existing README supplied with the project records an apparent conflict: common challenge rules reportedly prohibit existing LLM / hosted AI-service APIs and require local model training, while the text-track wording reportedly permits existing AI APIs. The actual organiser challenge brief was **not** among the three supplied files, so that conflict cannot be independently resolved here. The safe default is therefore a local, trainable baseline with no external generative model. Keep the NLU and response-composer interfaces swappable, and obtain written clarification from the organiser before changing that constraint.

---

## 4. SGB catalogue included in the supplied source

The following is the product information visible in `SGB_Agro_Industries_Product_Catalogue_Updated.pdf`. Prices are transcribed from that file; the PDF does not state a price-effective date, tax treatment, stock status, delivery charge or discount policy. Treat prices as **catalogue-listed values that require commercial approval before production quotation**.

| Product family | Variant / product | Listed price | Facts present in the catalogue |
|---|---|---:|---|
| Brush cutter trolley attachment | Sidepack trolley | ₹3,800 | Heavy-duty mild-steel construction; dual-wheel guiding trolley; intended for sidepack brush cutters; converts a brush cutter to push-type operation |
| Brush cutter trolley attachment | Backpack trolley | ₹4,300 | Heavy-duty mild-steel construction; dual-wheel guiding trolley; intended for backpack brush cutters; converts a brush cutter to push-type operation |
| Wheel barrow | Dumper Wheel Barrow | ₹8,500 | Heavy-duty construction; dumper/tipping mechanism; stable wheel support; described for soil, manure, farm produce and construction materials |
| Wheel barrow | Normal Wheel Barrow | ₹7,500 | Strong frame; balanced wheel design; described for agricultural materials, construction supplies and farm produce |
| Brush cutter with trolley | SGB Varsha Brush Cutter with Trolley | ₹14,000 | 52 cc; 2.1 HP; push-type trolley configuration; running time stated as 1.5 hours “as provided”; **no engine warranty** |
| Brush cutter | SGB 143R Stable Brush Cutter (GL 45) | ₹16,000 | 2-stroke; GL 45 model; 2.1 HP; stable carburetor; 6-month engine warranty |
| Brush cutter | SGB 143R WALBRO Brush Cutter (GL45 143R) | ₹21,000 | 2-stroke; GL45 143R model; 2.1 HP; WALBRO carburetor; 1-year engine warranty; running time stated as 3 hours “as provided” |

### Catalogue integrity notes — do not silently “fix” these

- **Normal Wheel Barrow capacity:** “95 litre” appears in the SEO tags on catalogue page 3, not in a product specification table. Do not advertise 95 L as a confirmed capacity until SGB approves it as a specification.
- **143R Stable and 143R WALBRO engine capacity:** their product artwork on pages 5 and 6 displays “43 cc”, but the specification tables do not list engine capacity. Store the artwork statement as an image-source observation and flag it for SGB verification; do not silently promote it to an approved specification.
- **Running time:** 1.5 hours (Varsha) and 3 hours (WALBRO) are explicitly marked “as provided”. The source does not give operating conditions, fuel quantity, duty cycle or test method. Do not present these figures as guaranteed runtime.
- **Warranty:** the source gives no engine warranty for Varsha, 6 months for 143R Stable and 1 year for 143R WALBRO. It does not specify warranty terms for the trolley attachments or wheel barrows. “Not stated” is not the same as “no warranty”.
- **Compatibility:** the trolley copy says sidepack/backpack brush cutters generally. It does not provide machine makes, clamp dimensions or a fitment matrix. Ask a human to verify exact machine compatibility when it matters.
- **Commercial facts absent:** stock, discount authority, delivery timelines, freight, GST inclusion, payment options and current price validity are not present in the catalogue. These must remain unknown until an approved source is integrated.
- **Product distinction:** the Sidepack and Backpack Trolleys are attachments. The Varsha product is described as a brush cutter supplied/configured with a push-type trolley. Do not conflate these offers.

### Catalogue image source map

The six-page PDF includes product photos/artwork: page 1 (sidepack and backpack trolleys), page 2 (dumper wheel barrow), page 3 (normal wheel barrow), page 4 (Varsha brush cutter with trolley), page 5 (143R Stable), page 6 (143R WALBRO). Extract and publish approved product images as separate assets during implementation; keep the source page and asset hash in the catalogue record.

---

## 5. End-to-end customer journey

The supplied `SGB_Sales_Chatbot_Flowchart.docx` shows the following structure:

1. Customer arrives from a Meta ad / WhatsApp entry point.
2. The system sends an approved welcome message and offers guided routes: **Wheel Barrow**, **Brush Cutter Trolley**, **Other Products / Enquiry**, or free text.
3. Wheel Barrow branches to **Normal / Dumper**; Brush Cutter Trolley branches to **Sidepack / Backpack**.
4. The system shares relevant product information and images, then learns the customer's use case and requirements.
5. It checks interest, answers questions and handles objections. Any discount or special price must be verified against authorised policy; unsupported or excessive requests go to a salesperson.
6. When the customer is not ready, the system can schedule a policy-compliant follow-up. When support or clarification is required, a human takes over with context.
7. When the customer is ready, the production flow collects order and delivery details, invokes the configured order/payment process, records the order in CRM and sends an appropriate confirmation.

The prototype simulates steps 1–7 in a browser and creates a **mock order only**. It does not collect real delivery addresses or process payments.

### Suggested live demo script using the supplied catalogue

1. **Start with ambiguity:** “I need a brush cutter trolley.” AgriSutra asks whether the customer already owns a brush cutter and wants an attachment, or wants a complete brush cutter with trolley.
2. **Resolve the variant:** the customer says “I already have a backpack brush cutter.” The Need Fingerprint stores `configuration=backpack`; the engine filters to the Backpack Trolley (catalogue-listed ₹4,300). The reply cites the page 1 source facts: mild-steel construction, dual-wheel guiding trolley and push-type use. It then asks for the machine make/model because an exact fitment matrix is not in the supplied catalogue.
3. **Challenge the system:** “Will this fit my exact model?” AgriSutra marks exact compatibility `UNKNOWN`, shows the missing source field in the Truth Receipt and offers a salesperson handoff. It does not say “universal fit”.
4. **Change the requirement:** the customer says “Actually, I have a sidepack machine.” The previous backpack value becomes superseded, the candidate changes to Sidepack Trolley (catalogue-listed ₹3,800), and Decision Replay shows the change.
5. **Test catalogue honesty:** ask “Is the normal wheel barrow 95 litres?” The agent explains that 95 litre appears in the catalogue's SEO tags, not its specification table, so it needs SGB confirmation before being stated as capacity.
6. **Try to negotiate:** ask for a discount. DealGuard returns `NO_POLICY` because the supplied files contain no discount approval matrix; the request is routed to a human.
7. **Close safely:** inspect an order preview, then create a clearly marked mock order only after explicit confirmation.

This sequence demonstrates product disambiguation, state correction, source-level evidence, uncertainty handling, negotiation controls and consent-bound order creation with facts grounded in the actual SGB files.

---

## 6. Recommended technology baseline

| Layer | Prototype recommendation | Production extension |
|---|---|---|
| Customer channel | React + TypeScript + Vite browser simulator | WhatsApp Business Platform Cloud API adapter; optional operator dashboard |
| API / business logic | Python + FastAPI, modular monolith | Same core, horizontally scalable API and worker processes |
| NLU | Locally trained word/character TF-IDF + Logistic Regression baseline; deterministic slot rules | Larger approved local models / validated multilingual models if permitted and justified by evaluation |
| Retrieval | Local BM25 or TF-IDF over catalogue fields and approved FAQs | Same retrieval with versioned catalogue; optional additional retrieval only after grounding tests |
| Dialogue policy | Explicit Python state machine | Same tested policy service |
| Data | SQLite | PostgreSQL; Redis/task queue only where asynchronous workload requires it |
| Evidence / audit | Structured JSON objects and local event log | Append-only audit/event storage, retention controls and access restrictions |
| Testing | pytest + frontend component / end-to-end tests | CI pipeline, load tests, integration contract tests and production monitoring |

**Do not add a generative LLM simply to make the demo sound clever.** The system's differentiator is the verifiable decision workflow. Under the stricter challenge interpretation, all prediction models must be trained locally on reviewed project data. Any language-generation component must remain deterministic and catalogue-grounded.

---

## 7. Repository structure to implement

```text
agrisutra/
├── README.md
├── architecture.md
├── prod.md
├── backend/
│   ├── app/main.py
│   ├── api/                  # sessions, messages, products, evidence, orders, handoff
│   ├── channels/             # browser simulator; production WhatsApp adapter
│   ├── nlu/                  # training, intent, slot extraction, confidence
│   ├── dialogue/             # state machine, Need Fingerprint, question selection
│   ├── catalogue/            # ingestion, validation, versioning, retrieval, evidence
│   ├── decision/             # hard constraints, ranking, counterfactual fit
│   ├── sales/                # response templates, DealGuard, order workflow
│   ├── handoff/              # assignment, context packet, pause/resume
│   ├── integrations/         # CRM, inventory, payment interfaces
│   ├── persistence/          # models, repositories, migrations
│   └── observability/        # metrics, structured logs, decision replay
├── frontend/
│   ├── src/chat/             # customer simulator
│   ├── src/cockpit/           # operator/judge decision cockpit
│   └── src/components/        # evidence, product cards, Need Fingerprint
├── data/
│   ├── catalogue/source/      # original approved SGB files; access restricted
│   ├── catalogue/normalized/  # generated, versioned product records
│   ├── assets/products/       # approved, extracted product images
│   ├── training/              # reviewed and labelled local training data
│   └── evaluation/            # held-out, adversarial and regression cases
├── models/                    # versioned local model artefacts; not user data
├── scripts/                   # ingest, validate, train, evaluate, seed demo
├── tests/                     # unit, integration, safety, replay and end-to-end
├── deploy/                    # Docker, reverse proxy, CI and environment templates
├── .env.example
├── .gitignore
├── requirements.txt
└── package.json
```

The tree is a target layout, not a claim that the application code already exists. Keep challenge mode and production adapters behind interfaces so the same tested sales engine can run in both.

---

## 8. Build order and release milestones

| Milestone | Build | Exit condition |
|---|---|---|
| M0 — Product truth | Parse the seven catalogue variants, import images, create validation report and mark unresolved source facts | Every product claim has a source reference; ambiguous facts are flagged |
| M1 — Conversation core | Intent model, slot extraction, Need Fingerprint, correction and contradiction handling | Test conversations retain and update state correctly |
| M2 — AskSmart | Candidate generation and question-value scoring | The next question is relevant to the products that remain viable |
| M3 — Recommendation core | Hard constraints, soft preference ranking, candidate explanations and counterfactual view | No hard-failing product can be recommended |
| M4 — Proofline | Claim-level Truth Receipt, source versioning and Decision Replay | Every customer-visible product fact can be traced to a catalogue field |
| M5 — Sales controls | Objection handling, DealGuard policy checks, order preview and explicit confirmation | No unapproved discount or premature order is created |
| M6 — Handoff | Human context packet, queue, ownership lock and bot pause/resume | Human takeover preserves context and prevents bot-human collisions |
| M7 — Production channel | WhatsApp webhooks, templates, opt-in/out, CRM and order-system adapters | Webhook replay, policy and integration tests pass in a controlled environment |
| M8 — Launch hardening | Security, privacy, monitoring, load, recovery and operator training | All production release gates in `prod.md` are signed off |

For a 24-hour hackathon, prioritise M0–M6 with a clean local demo. Treat M7–M8 as a documented production roadmap unless the official brief and credentials make them mandatory.

---

## 9. Evaluation: measure the decision, not just the chatbot

Report only measured values. Keep a held-out test set and disclose its size and construction.

| Area | Metric / check | Release expectation |
|---|---|---|
| Intent understanding | Macro-F1; confusion matrix for buying intent vs explicit confirmation | Threshold selected on validation data; risky confusion cases fall back safely |
| Requirement capture | Per-slot precision, recall and F1 | Measure critical fields individually; do not hide weak slots in an average |
| Product truth | Exact-field answer accuracy; unsupported-claim rate | Zero unsupported claims in the curated release test suite |
| Recommendation | Mandatory-constraint violations; evidence completeness; reviewer-rated suitability | Zero mandatory-constraint violations in release tests |
| Question selection | Turns to qualification; answerability; customer effort | Compare AskSmart with fixed-question baseline |
| Sales safety | Unapproved discount promises; orders without confirmation; duplicate orders | Zero in release tests |
| Channel reliability | Webhook signature failures, deduplication, queue lag, send failures | Alerting and replay procedures proven before production |
| Human experience | Handoff completeness, agent acceptance, customer satisfaction | Evaluate through staff rehearsal and a small controlled pilot |

Useful product metrics include enquiry-to-qualified-lead rate, recommendation acceptance, quote-to-order conversion, average time to first useful answer, handoff rate and follow-up response. These are operational outcomes, not model-accuracy substitutes.

---

## 10. Research-informed design

The design uses research as a guide for the architecture, not as a claim that AgriSutra reproduces a paper's reported results:

- **Conversational recommendation and preference elicitation:** Christakopoulou, Radlinski and Hofmann's work studies asking users questions to learn preferences and improve recommendations. This supports AskSmart and the Need Fingerprint.
- **Adaptive conversation strategy:** the survey by Jannach et al. identifies question-based preference elicitation, multi-turn strategy, dialogue understanding and evaluation as core areas of conversational recommender systems. It supports evaluating the complete conversation rather than ranking alone.
- **Task-oriented dialogue state:** MultiWOZ provides annotated multi-domain conversations and dialogue belief states; it is useful as a structural reference, not as SGB-specific training data.
- **Evidence-grounded retrieval:** Lewis et al.'s RAG paper motivates retrieving explicit evidence for knowledge-intensive answers. AgriSutra adopts the retrieval/provenance principle but uses local lexical retrieval and deterministic templates in the challenge-safe implementation; it does not require the paper's pretrained generator.
- **Retrieval ranking:** Robertson and Zaragoza's BM25 survey supports a strong local lexical retrieval baseline for catalogue descriptions and FAQs.
- **Evaluation:** the conversational recommender evaluation survey recommends assessing both component quality and the user experience of the whole interactive system.

See the full references in [architecture.md](architecture.md) and [prod.md](prod.md).

---

## 11. Definition of done for the challenge build

- [ ] The seven supplied product variants are ingested without inventing missing fields.
- [ ] Catalogue page/field provenance is retained for product facts and extracted images.
- [ ] The agent supports guided product selection and natural free-text input.
- [ ] Multi-turn state survives follow-up questions and corrections.
- [ ] Unknown or contradictory product facts are disclosed, not guessed.
- [ ] Hard constraints are applied before soft-preference ranking.
- [ ] AskSmart selects relevant questions and is compared with a simple baseline.
- [ ] Each recommendation has a Truth Receipt and “why not” explanation.
- [ ] No discount is offered without an explicit approved policy record.
- [ ] Human takeover includes the transcript and structured context; the bot pauses while the human owns the chat.
- [ ] Order preview requires a final, unambiguous confirmation; demo records are visibly marked `MOCK`.
- [ ] Repeated confirmation or event delivery cannot create duplicate records.
- [ ] Session isolation and adversarial grounding tests pass.
- [ ] All reported model scores are measured on a held-out set.

## 12. Build documentation

- [Architecture and data contracts](architecture.md)
- [Product requirements, production integrations, deployment and operations](prod.md)

## 13. Key source documents and external references

### Supplied project documents

1. `AgriSutra_README.md` — earlier project design, decision-engine concepts and reported challenge-rule caveat.
2. `SGB_Agro_Industries_Product_Catalogue_Updated.pdf` — six-page product catalogue and product artwork.
3. `SGB_Sales_Chatbot_Flowchart.docx` — WhatsApp sales flow and implementation requirements.

### Research and platform references

- Christakopoulou, K., Radlinski, F. & Hofmann, K. (2016). [Towards Conversational Recommender Systems](https://research.google/pubs/towards-conversational-recommender-systems/). ACM SIGKDD.
- Jannach, D. (2023). [Evaluating conversational recommender systems: A landscape of research](https://doi.org/10.1007/s10462-022-10229-x). *Artificial Intelligence Review*, 56, 2365–2400.
- Gao et al. (2021). [Advances and challenges in conversational recommender systems: A survey](https://doi.org/10.1016/j.aiopen.2021.06.002). *AI Open*, 2, 100–126.
- Budzianowski, P. et al. (2018). [MultiWOZ: A Large-Scale Multi-Domain Wizard-of-Oz Dataset for Task-Oriented Dialogue Modelling](https://aclanthology.org/D18-1547/). EMNLP.
- Lewis, P. et al. (2020). [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401). NeurIPS. Used here for retrieval/provenance motivation, not as a required model stack.
- Robertson, S. & Zaragoza, H. (2009). [The Probabilistic Relevance Framework: BM25 and Beyond](https://doi.org/10.1561/1500000019). *Foundations and Trends in Information Retrieval*.
- Ziegfeld, L., Di Scala, D. & Cremers, A. H. M. (2025). [The effect of preference elicitation methods on the user experience in conversational recommender systems](https://doi.org/10.1016/j.csl.2024.101696). *Computer Speech & Language*.
- [Learning a Strategy for Preference Elicitation in Conversational Recommender Systems](https://doi.org/10.1109/IJCNN60899.2024.10650365) (IJCNN 2024). Studies question selection to maximise information gain; it is a future comparison point, not a component the prototype claims to reproduce.
- Meta. [WhatsApp Business Messaging Policy](https://whatsappbusiness.com/policy/) (policy page states last updated 23 September 2026; re-check before deployment).
- Meta for Developers. [WhatsApp Business Platform overview](https://developers.facebook.com/documentation/business-messaging/whatsapp/overview/) and [Webhook getting started / payload validation](https://developers.facebook.com/docs/graph-api/webhooks/getting-started/).
- Government of India, MeitY. [Digital Personal Data Protection Rules, 2025 — Gazette notification](https://www.meity.gov.in/static/uploads/2025/11/53450e6e5dc0bfa85ebd78686cadad39.pdf).

*External platform rules, policies and legal requirements change. Re-check the linked primary sources immediately before a production release. This documentation is an engineering specification, not legal advice.*
