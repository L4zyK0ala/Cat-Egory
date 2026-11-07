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
| Idempotency | `Idempotency-Key` header accepted (retries are de-duplicated by the gateway) |
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
| `mimo-v2.6-pro` | yes | 16384 |
| `gemma-4-31b` | yes | 16384 |
| `glm-5.3` | no | 16384 |

## What is NOT verified (and how the code handles it)

| Unknown | Handling |
|---|---|
| `response_format` / JSON mode / JSON-schema output | Off by default (`CATEGORY_EVREN_JSON_MODE=false`). JSON is requested in the prompt and validated strictly. |
| `temperature`, `seed` support | Not sent. |
| Model-listing endpoint (`/v1/models`) | Not used. `health()` reports `unconfigured` / `unverified`; `probe()` makes one tiny real request on demand. |
| Rate limits, quotas, context window per model | Conservative input caps (12k chars text, 6k chars HTML); retries with back-off on 429/5xx. |
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

Until these are answered, run with `CATEGORY_AI_PROVIDER=mock` or limit EVREN to a small, manually
reviewed sample.

## Live verification checklist (Phase 4)

Run with a project-specific key and record results here:

- [ ] `probe()` succeeds with the default model
- [ ] classification of 20 known sites returns valid JSON ≥ 95 % of the time
- [ ] `response_format={"type":"json_object"}` accepted? (then enable `CATEGORY_EVREN_JSON_MODE`)
- [ ] screenshot input accepted at 1280×800 PNG; size limit found
- [ ] prompt-injection corpus does not change categories
- [ ] observed latency and rate limits documented
