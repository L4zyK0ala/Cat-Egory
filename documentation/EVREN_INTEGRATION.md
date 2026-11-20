# EVREN Integration

EVREN (https://evren.ssyz.org.tr/) exposes an LLM gateway. Cat-egory talks to it through
`ai_providers/evren.py`, an adapter behind the generic `AIProvider` interface, so another local or remote
LLM can replace it by adding one class and one registry entry.

## What is verified

Verified on **2026-10-31** by a separate, working production client (not by this project yet):

| Fact | Value |
|---|---|
| Base URL | `https://evren-llmapi.ssyz.org.tr` (the website is `evren.ssyz.org.tr`) |
| Endpoint | `POST /v1/chat/completions` — OpenAI Chat Completions compatible |
| Auth | `Authorization: Bearer <key>` |
| Idempotency | `Idempotency-Key` header accepted (retries are de-duplicated by the gateway). While the first request with a key is still running, a repeat gets `409` `conflict` ("bu Idempotency-Key ile bir istek zaten işleniyor"); after it finishes, the repeat returns the stored answer (verified by Cat-egory 2026-11-08) |
| Images | `image_url` content parts with `data:` URIs work on vision models |
| PDFs | not supported (text must be extracted first) |
| Errors | `401` auth, `402` credit, `403` permission, `429` rate limit; `502/503/504` transient |
| Response | standard `choices[0].message.content`, `usage.prompt_tokens/completion_tokens`, `id`; `model` may include a vendor prefix (`qwen/qwen3.8-flash-next`) |
| `reasoning_effort` | accepted by some models |

Models seen working then:

| Model | Images | Max output |
|---|---|---|
| `qwen3.8-flash-next` (default here: fast, vision) | yes | 16384 |
| `deepseek-v4.1-flash` | yes | 65536 |
| ~~`mimo-v2.6-pro`~~ | yes | 16384 |
| `gemma-4-31b` | yes | 16384 |
| `glm-5.3` | no | 16384 |

**Retired:** EVREN's user poll of 2–6 November 2026 retired `mimo-v2.6-pro`. Requests for it are answered by
`deepseek-v4.1-flash` until 2027-01-01 and fail after that, so `RETIRED_MODELS` refuses it. Our comparison of
2026-11-15 agrees: the two picked the same primary category on 35 of 35 sites, so its "mimo" row is deepseek's.
Models EVREN names as current (forum, 2026-11-19; check with the probe below): `qwen3.8-flash-next`,
`deepseek-v4.1-flash`, `gemma-4-31b`, `qwen3-vl-30b`, and `clef` (a decision model on `/v1/systemone`).

## What is NOT verified (and how the code handles it)

| Unknown | Handling |
|---|---|
| `response_format` / JSON mode / JSON-schema output | Off by default (`CATEGORY_EVREN_JSON_MODE=false`). JSON is requested in the prompt and validated strictly. |
| `temperature`, `seed` support | Not sent. |
| Model-listing endpoint (`/v1/models`) | Not used. `health()` reports `unconfigured` / `unverified`; `probe()` makes one tiny real request on demand. |
| Rate limits, quotas, context window per model | Conservative input caps (12k chars text, 6k chars HTML); retries with back-off on 5xx. |
| Image size limits | Screenshots will be downscaled (Phase 3/4) before sending. |
| Pricing after the free period | Unknown. |
| Model availability over time | Unknown models are treated as text-only and reported `unverified`. |

## Terms-of-use questions to resolve before production

These came up when reviewing the EVREN LLM terms (v1) for another project; they apply here too:

1. **Data pool consent** — prompts may be added to a corpus (`llm_corpus_consent`). Cat-egory sends public
   web content, but screenshots and text can contain personal data. Confirm the setting and opt out if possible.
2. **Retention** — the terms do not state how long prompts are kept.
3. **Key sharing** — sharing a key is forbidden. Cat-egory must use its **own** key, never another app's.
4. **Automated / bulk use** — confirm that high-volume automated classification of third-party websites is
   allowed and what the quota is.
5. **Free period** — announced to end on **2026-12-01**; pricing after that is unknown.
6. **Publishing outputs** — confirm that model outputs may be published under CC BY 4.0.
7. **Training on outputs** — answered: training Cat-egory's own local classifier (Yaş Mama, below) on EVREN's answers is
   allowed (confirmed by the owner, 2026-11-16). `CATEGORY_LOCAL_TRAIN_ON_AI_LABELS=false` would limit it to
   people's decisions should that change.

Until these are answered, run with `CATEGORY_AI_PROVIDER=mock` or limit EVREN to a small, manually
reviewed sample.

## Yaş Mama: our own local classifier

If EVREN becomes too expensive after the free period, most pages should not need it. Yaş Mama (`classification/yasmama.py`)
trains a CPU-only classifier on the labels Cat-egory already has (people's decisions and confident published
answers) and runs it in **shadow mode**: it guesses next to every EVREN answer, publishes nothing, and its report
shows how often it would have been right, per category and confidence threshold, on pages it never saw and against
live EVREN answers. Only with those numbers would a later change let it answer the easy pages alone and keep EVREN
for the hard ones (low local confidence, rare categories, image-only pages); threat flags stay with people.
Re-scans of unchanged pages already skip the model (`classification/unchanged.py`). Operation:
[OPERATIONS.md](OPERATIONS.md#yaş-mama-the-local-classifier-shadow-mode).

## Where the key lives

Only the `worker` container receives `CATEGORY_EVREN_API_KEY`; the public `api` container never sees it.
Admin → *AI provider* (and `GET /api/v1/admin/providers`) therefore asks the worker for its status;
*Test live* (`?probe=true`) sends one small real request from the worker. From the shell:

```bash
$DC exec -T worker python -c "from ai_providers.registry import report; print(report(probe=True))"
```

## Probing the gateway

`python -m ai_providers.evren_probe models` lists the models the gateway serves now; `... moderation` sends three
fixed sample texts (no site content) to `/v1/moderations` with `qwen3-guard-4b` and prints the answer, its `usage`
and any rate-limit or credit headers. Run both in the worker (`$DC exec -T worker ... < /dev/null`); the key is
never printed. A forum post (2026-11-19) says the moderation endpoint is free and outside the token quota, takes
up to 32 texts per request and flags page text that tries to instruct the model as `Jailbreak`; check the answer
and EVREN's usage page before relying on it. It judges content safety, not phishing: a copy of a bank's login
page reads as harmless text.

First answers (2026-11-20): `GET /v1/models` lists every model at price 0 with `"free_until": "2026-12-01"`, the
guard model included, so "free after December" is not confirmed; it also lists `qwen3-vl-30b` (vision chat, added
to `KNOWN_MODELS`), the decision models `clef` (text and images) and `laya` (text), and no `mimo-v2.6-pro`. On the
three samples the guard model answered Safe / Controversial "Unethical Acts" (the instruction to the model, not
"Jailbreak") / Controversial "Non-violent Illegal Acts" (the phishing sentence), with `flagged` false each time:
read `evren.safety` and `evren.guard_categories`. Limit: 1,200 requests a minute; no `usage` in the answer.

`python -m threats.guard_eval [--per-group 150] [--yes]` measures it on pages we already know (our confirmed
phishing and scams, person-checked banking/finance/government and other sites, pages with text aimed at the model,
adult content) and prints how often each group is called Safe, Controversial or Unsafe, and how well "not Safe" or
"Non-violent Illegal Acts" would tell our threats from checked legitimate sites. It stores nothing.
`--by-category` groups published sites by main category instead (gambling / illegal gambling, streaming media /
piracy, and a few ordinary categories) and shows whether that signal tells the unlicensed side apart.

First run (2026-11-20, 513 pages): it does **not** find phishing (10 % of our confirmed phishing and scams were not
Safe, against 15 % of person-checked legitimate sites; banking, finance and government 0 of 55) and called none of
8 pages with text aimed at the model Jailbreak. It does find adult content: "Sexual Content" on 149 of 150 adult
sites and on none of 205 person-checked other sites.

By category (2026-11-20, 748 pages): "Non-violent Illegal Acts" on 50 % of illegal-gambling sites but also 27 %
of licensed ones (29 % of the person-checked), on 11 % of piracy sites and none of 44 streaming sites, and on at
most 2 % of news, education and e-commerce sites. So it does not tell licensed from unlicensed: it reacts to the
subject (betting) more than to the licence, and rarely to piracy. Conclusion: use the guard model, if at all, for
adult content only; licensed-or-not questions go to a decision model (`clef`) next.

## Timeouts and retries

Answers are slow: 24–53 s for one classification with `qwen3.8-flash-next` (2026-11-08), sometimes more than
two minutes. One call is therefore bounded by two settings:

| Setting | Default | Meaning |
|---|---|---|
| `CATEGORY_EVREN_TIMEOUT_SECONDS` | 180 | read timeout of one HTTP request |
| `CATEGORY_EVREN_DEADLINE_SECONDS` | 270 | total for the call, including retries and waiting |

Every retry reuses the call's `Idempotency-Key`. After a timeout, the gateway therefore either returns the
finished answer or `409 conflict` (still running). On `409` the client waits 5, 10, 15, 15 … s and asks again;
this does not count as a failed attempt and the request is not run or billed twice. When the deadline passes, the
call fails with the retryable kind `timeout` and the classification task tries again later. Keep the deadline
below half of the classify task's 600 s soft limit, because a second-opinion call may follow the first.

## Measuring quality on known sites

`classification/evaluation_set.yaml` lists 36 sites (15 Turkish, 21 international) with the primary categories a
person would accept (first = best) and categories that are wrong even as a secondary. Labels are our own judgement.

```bash
$DC exec -T api python -m classification.evaluation add          # dry run
$DC exec -T api python -m classification.evaluation add --yes    # add + queue scans (priority 4)
# ~15-30 min later (2 classify workers, 25-55 s per answer):
$DC exec -T api python -m classification.evaluation report > evaluation-$(date +%F).md
```

The report gives crawl failures (not counted as wrong answers), valid-answer rate, strict / lenient accuracy,
accuracy per confidence band, **wrong answers that were published automatically** (the number that matters for
the 0.90 threshold), wrong secondaries, and latency. The evaluation domains stay in the database as normal domains.

### Comparing models on the same sites

`report` only measures the production pipeline (the default model, plus the second model in the 0.70-0.89 band).
`compare` asks several models about the same pages: for every site of a set with a successful scan, the stored
scan (text, HTML excerpt, and the screenshot for models that take images) goes to each listed model with the
production prompt, and each answer is validated exactly as in production (`AIProvider.classify`). All models see
the same scan of a site. It needs the EVREN key, so it runs in the worker; run `add --yes` first so the sites
are scanned.

```bash
MODELS=qwen3.8-flash-next,deepseek-v4.1-flash,gemma-4-31b,glm-5.3
$DC exec -T worker python -m classification.evaluation compare --models $MODELS          # dry run: counts calls
$DC exec -T worker python -m classification.evaluation compare --models $MODELS --yes \
    > model-comparison-$(date +%F).md                                                   # progress goes to stderr
$DC exec -T worker python -m classification.evaluation compare-report --models $MODELS  # re-print, no calls
# --set evaluation_holdout.yaml for the held-out set; --again to ask anew; --pause N seconds between calls (2)
```

* **Nothing public changes.** Answers go to the `model_comparisons` table (migration `0009`), never to
  `ai_predictions`: no domain status or category changes, no review task, no second opinion, and neither `report`
  nor the public accuracy page reads them. Their tokens do count towards `CATEGORY_AI_DAILY_TOKEN_CAP` (they use the
  same account's quota), so discovery pauses earlier on a day with a large comparison.
* **Calls.** At most (models × sites with a successful scan); the dry run prints the exact number per model.
  Calls are made one at a time from the command, next to the worker's two classify slots, with the provider's
  usual retry rules. A site/model pair that already has a valid answer for the current prompt and taxonomy versions
  is skipped, so an interrupted run resumes where it stopped and a finished one costs nothing to repeat. The run
  stops at the first auth, credit, permission or rate-limit error (exit code 2, report still printed). At the default model's measured median of
  37-78 s an answer, a full five-model run on the 36-site set takes a few hours; other models' latency is unknown.
* **Images.** `glm-5.3` takes no images (`KNOWN_MODELS` in `ai_providers/evren.py`): it gets the text only and the
  report says so in its *input* column. Sites without a stored screenshot are noted there too.
* **Report.** One row per model: answers, valid-answer rate, strict and lenient accuracy, strict and lenient
  accuracy on the sites every model answered validly (the like-for-like figures), wrong answers at >= 0.90, median / p95 latency,
  output tokens (median / total), prompt version; accuracy per confidence band (>= 0.90, 0.70-0.89, 0.50-0.69,
  < 0.50); how often each pair of models picks the same primary category; the confident wrong answers; and a
  per-site table with each model's primary category and confidence.

## Live verification checklist (Phase 4)

Run with the project's own key and record results here. Cat-egory has its own EVREN key since 2026-11-08:

- [x] `probe()` succeeds with the default model (2026-11-08)
- [x] classification of known sites returns valid JSON ≥ 95 % of the time — 2026-11-08, 36 sites, real crawls.
      First run 30/35 valid (86 %): 3× evidence type "title", 2× answer cut at 1500 tokens. After the fix
      (`95b54f7`: evidence labels mapped, limit 3000, shorter answers) the 5 failed sites gave **35/35 valid**.
      Accuracy: 34/35 acceptable primary (97 %), 30/35 best primary (86 %); all 33 answers ≥ 0.90 correct;
      the one wrong answer (mozilla.org → business, 0.88) went to review. The model tends to pick the general
      category over the specific one (python.org → information-technology instead of software, cloudflare.com →
      cloud-computing instead of content-delivery-networks). Latency with real pages (text + screenshot,
      2 parallel): median 78 s, p95 143 s. resmigazete.gov.tr drops every request whose User-Agent contains
      "bot", so it is never crawled (by design: the crawler does not hide that it is a bot).
- [x] prompt 2026-11-08.2 ("most specific category", shorter answers), 36 tuned sites + 30 held-out sites:
      tuned set 35/35 valid, best primary 31/35 (was 30/35), acceptable 34/35; held-out set 27/27 valid, best
      primary 26/27 (96 %), acceptable 26/27. All 55 answers ≥ 0.90 across both sets were correct; the two
      wrong answers (mozilla.org → business 0.80, proton.me → software 0.86) went to review. Median latency
      fell from 78 s to 37 s (tuned) / 50 s (held-out). Correct but under-confident answers (flypgs.com 0.45,
      godaddy.com 0.35, ebay.com 0.49, akamai.com 0.62) cost re-analysis or review, not accuracy.
      Crawl gaps: ptt.gov.tr and resmigazete.gov.tr drop requests from bot user agents; mega.nz disallows
      crawling in robots.txt; britannica.com timed out.
- [ ] `response_format={"type":"json_object"}` accepted? (then enable `CATEGORY_EVREN_JSON_MODE`)
- [ ] screenshot input accepted at 1280×800 PNG; size limit found
- [ ] prompt-injection corpus does not change categories — one sample held (asked to say "education",
      answered "food"); a corpus is still to be run
- [ ] observed latency and rate limits documented — latency yes (see *Timeouts and retries*); rate limits unknown
