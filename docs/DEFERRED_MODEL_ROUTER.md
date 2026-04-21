# Deferred: Multi-Provider Model Router

**Status**: deferred out of MVP (per hard-decision #5).
**When to revisit**: once any one of these becomes true —
1. LLM spend > $500/mo and multiple tasks could use cheaper tiers
2. Need for strict local / on-prem inference for privacy-sensitive businesses
3. Provider outage risk materializes (OpenAI rate-limited us repeatedly)
4. Multi-model A/B testing becomes a product requirement

For MVP we call OpenAI directly from `conversation_intelligence_worker` and from any future embedding worker, using `Settings.llm_chat_model` + `Settings.llm_embedding_model` as the model strings. That keeps latency, spend, and cognitive load low.

This document captures exactly **what** to port from `SMB-MetaPattern/apps/model-router/` **when** we bring the router in, so nothing needs to be re-derived.

---

## Source location

`/root/AlchemyAI/SMB-MetaPattern/apps/model-router/` — 1,573 lines of Python (FastAPI).

| File | LOC | Role | Port verdict |
|---|---:|---|---|
| `main.py` | 592 | FastAPI `/v1/chat/completions` proxy + `/route` endpoint. Streams + non-stream. | **PILFER** with minor simplification (drop OpenClaw-specific bits in lifespan). |
| `schemas.py` | 167 | Pydantic models for `ChatCompletionRequest`, `RoutingDecision`, `RoutingPolicy`, tiers, hints. | **PILFER** verbatim. |
| `policy.py` | 179 | Skill-override + hard-rules + score-based routing. Sticky-session cache. | **PILFER** verbatim. |
| `policy.json` | — | Runtime-loaded policy config: tiers, providers, skill → tier map. | **PORT** with Revenue-Edge-relevant skills only (see below). |
| `escalation.py` | 160 | Auto-escalate tier after N failures per session. | **PILFER** verbatim. |
| `guardrails.py` | 143 | Output filters (length, forbidden-phrase, JSON-schema validation). | **PILFER** verbatim. |
| `dlp.py` | 102 | Payload redaction before forwarding to provider. | **PILFER** verbatim. `apps/api/app/pii_filter.py` already ships redaction helpers; unify under one module. |
| `telemetry.py` | 192 | Per-request cost + tier + reason-code metrics. | **PORT** — rewrite the storage layer to write into Supabase `events` / `metric_snapshots` instead of Prometheus-only counters. |
| `config.py` | 38 | Env var loading. | **REPLACE** with Revenue Edge's `apps/api/app/config.py`. |
| `adapters/` | — | OpenAI / Anthropic / MiniMax adapters. | **PILFER**, drop whichever adapter isn't live. |
| `Dockerfile`, `requirements.txt` | — | — | **PILFER**. |
| `tests/` | — | Router unit tests. | **REFERENCE** — rewrite against Revenue Edge's test fixtures. |

---

## Drop-in location when we bring it back

```
apps/
  router/                <-- new service, port 8090
    Dockerfile
    pyproject.toml
    src/
      main.py
      schemas.py
      policy.py
      policy.json
      escalation.py
      guardrails.py
      dlp.py
      telemetry.py
      adapters/
        __init__.py
        openai.py
        anthropic.py
        minimax.py
      tests/
```

Add a `re-router` service in `docker-compose.yml` at port 8090. Set `OPENAI_BASE_URL=http://re-router:8090/v1` on workers/api that need LLM calls; everything else stays identical.

---

## Minimal integration changes required at port time

### 1. `apps/workers/src/workers/conversation_intelligence.py`

Change the OpenAI client base URL to the router:

```python
client = httpx.AsyncClient(
    base_url=settings.llm_router_url or "https://api.openai.com/v1",
    headers={
        "Authorization": f"Bearer {settings.openai_api_key}",
        "x-route-skill": "revenue-edge-intent",
    },
)
```

The router reads `x-route-skill` and picks a tier from `policy.json`. No other worker code changes.

### 2. `apps/api/app/config.py`

Add:

```python
llm_router_url: str = ""
```

Set `LLM_ROUTER_URL=http://re-router:8090/v1` in `.env` to route via the gateway; leave empty to go direct to OpenAI.

### 3. `policy.json` — Revenue Edge skill map

Minimum skill → tier map to ship:

```json
{
  "skill_overrides": {
    "revenue-edge-intent": "balanced",
    "revenue-edge-reply":  "balanced",
    "revenue-edge-quote":  "frontier",
    "revenue-edge-rollup": "economy",
    "revenue-edge-embed":  "economy"
  },
  "model_overrides": {
    "openai/text-embedding-3-small": "economy"
  },
  "default_tier": "balanced",
  "tiers": {
    "economy":  { "provider": "openai", "model": "gpt-4.1-nano" },
    "balanced": { "provider": "openai", "model": "gpt-4.1-mini" },
    "frontier": { "provider": "openai", "model": "gpt-4.1" }
  }
}
```

When multi-provider is actually wanted, add `anthropic/claude-*` or `minimax/*` entries.

---

## What **not** to bring over

- OpenClaw-specific routes (`/oc-gateway/*`, `/skills/*`)
- `script/` and `scripts/` under model-router — all SMB/RE-specific dev utilities
- `internal-heartbeat`, `internal-cma`, `internal-loans` skill overrides — delete from the Revenue Edge port of `policy.json`
- The `MODEL_MAP` block in `SMB-MetaPattern/apps/api-gateway/app/config.py` — it referenced RE-specific skills (`cma_narrative`, `loan_research`, etc.) that don't exist here.

---

## Estimated porting effort

- 1 day for an experienced Python dev if the tests come along.
- Biggest time sink is wiring telemetry to Supabase `events` instead of Prometheus-only.
- Low risk: the router is stateless except for in-memory session escalation state, which is fine for MVP scale.

---

## Alternatives considered, to document why we didn't pick them

1. **LiteLLM proxy** — an off-the-shelf OSS equivalent. We'd lose the skill-based routing semantics unique to the SMB-MetaPattern router. Worth a re-evaluation at the time we want to port, because LiteLLM has matured.
2. **Portkey / Helicone / Traceloop** — commercial alternatives; add vendor dependency we don't need for MVP.
3. **Direct OpenAI calls with a simple `model_for(skill)` helper** — what we're actually doing in MVP. Good enough until 2+ providers matter.
