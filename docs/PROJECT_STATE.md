# OpsPilot AI — Project State (Handoff Document)

**Purpose of this file:** everything a fresh Claude session needs to continue this project without the full chat history. This captures *state, decisions, and lessons learned* — not full file contents, since those go stale the moment you make your next edit. **The GitHub repo is the living source of truth for actual code**; this document is the narrative around it.

**Meta-lesson (2026-07-08):** a prior version of this document claimed Phase 7 (MCP server) was "✅ built" and described a `docs/adr/` folder and `OpsPilot_AI_Roadmap.md` file that never existed in this repo — it was written in a separate zip-based chat session and never reconciled against `git log`. Trust `git log`/`git status` over this document's claims; update this file whenever a claim turns out stale rather than letting it compound.

---

## 1. Project identity

**OpsPilot AI** — an agentic AWS infrastructure investigation assistant, built as a portfolio/CV project to demonstrate full-stack + AI/cloud engineering skill to hiring managers.

- **Repo:** `github.com/ahmad2474/opspilot-ai`, branch `main` (remote `origin`, already connected and in sync — nothing to set up)
- **Owner's environment:** Windows PC, VS Code integrated terminal
- **Local path:** `C:\Users\DELL\Desktop\opspilot-ai\`

---

## 2. Tech stack (exact versions in use)

| Layer | Stack |
|---|---|
| Backend | FastAPI, boto3, Pydantic v2 / pydantic-settings, structured logging (contextvars-based request-ID tracing) |
| Agent | OpenAI Agents SDK (`openai-agents==0.2.8`, `openai>=1.99.6,<2`), `OpenAIChatCompletionsModel` wrapping OpenAI-compatible free-tier providers |
| LLM providers | Groq (primary, model `openai/gpt-oss-120b`) → Gemini Flash → NVIDIA NIM, automatic fallback |
| MCP | Official MCP Python SDK, `mcp[cli]>=1.27,<2` (installed: 1.28.1) |
| Frontend | Next.js `14.2.35` (App Router, TypeScript), Tailwind `3.4.14` + `@tailwindcss/typography`, `react-markdown` + `remark-gfm` |
| Infra | AWS Free Plan, manual console provisioning (zero ongoing spend) |
| CI | GitHub Actions — backend: ruff + pytest + Docker build; frontend: eslint + build |

---

## 3. Core architectural principles (don't violate these when extending)

1. **Service-layer split:** `tools/` (thin, framework-specific wrappers) → `services/` (pure business logic, zero LLM/agent imports) → `aws/client.py` (the *only* place `boto3.client()` is called). This is what makes the investigation logic unit-testable without mocking an LLM, and what makes chat/dashboard/MCP all structurally agree — they call the same `services/` functions.
2. **Read-only, always.** No tool anywhere performs a write/mutating AWS action. IAM policy enforces this too, not just app-level convention.
3. **Never hardcode.** Region, instance IDs, model names — everything comes from `app/core/config.py` (env-driven `Settings`), never inline.
4. **Naming avoids PyPI package shadowing.** The orchestration folder is `app/agent/` (singular), not `app/agents/`, to avoid shadowing the installed `agents` package. Same reasoning applied to `app/mcp/` (nested under `app/`, never a top-level `mcp/` directory) for the installed `mcp` package.
5. **Flag AWS cost implications before suggesting anything new.** This project runs on AWS's Free Plan specifically so a card is never charged; RDS is the one service that draws real credit (not Always Free) and needs to be stopped when idle — and RDS auto-restarts after 7 days stopped (unlike EC2, which stays stopped indefinitely), worth remembering if it's been more than a week.

---

## 4. Complete file structure (as of this handoff, verified against the actual repo)

```
opspilot-ai/
├── .github/workflows/ci.yml
├── .gitignore
├── README.md                          # includes accepted-risk/known-limitations notes inline (no separate ADR file)
├── AWS_ZeroSpend_Setup_Guide.md
├── docker-compose.yml
├── docs/
│   └── PROJECT_STATE.md               # this file
│
├── opspilot-backend/
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── .env.example / .env (gitignored)
│   ├── requirements.txt                # runtime deps, incl. mcp[cli]>=1.27,<2
│   ├── requirements-dev.txt            # pytest, ruff, httpx — dev only
│   ├── pyproject.toml                  # ruff + pytest config
│   ├── app/
│   │   ├── main.py                     # FastAPI app, routes, middleware
│   │   ├── core/
│   │   │   ├── config.py               # env-driven Settings (load_dotenv() lives here — see lessons learned #1)
│   │   │   └── logging.py              # request-ID contextvar + middleware
│   │   ├── aws/client.py               # only place boto3.client() is called
│   │   ├── models/                     # Pydantic schemas
│   │   │   └── ec2.py, cloudwatch.py, cloudtrail.py, dashboard.py, resources.py, chat.py (incl. TraceStep)
│   │   ├── services/                   # business logic, zero LLM/agent imports
│   │   │   └── ec2_service.py, cloudwatch_service.py, cloudtrail_service.py
│   │   │       s3_service.py, lambda_service.py, dynamodb_service.py, sns_service.py, rds_service.py
│   │   ├── tools/                      # thin @function_tool wrappers (Agents SDK)
│   │   │   └── ec2_tools.py, cloudwatch_tools.py, cloudtrail_tools.py
│   │   │       s3_tools.py, lambda_tools.py, dynamodb_tools.py, sns_tools.py, rds_tools.py
│   │   ├── agent/
│   │   │   ├── providers.py            # provider name -> OpenAIChatCompletionsModel
│   │   │   └── orchestrator.py         # Agent definition, instructions, run_chat_turn(), trace extraction
│   │   ├── mcp/
│   │   │   ├── __init__.py
│   │   │   └── server.py               # FastMCP server, reuses services/ directly — 3rd consumer of the service layer
│   │   └── api/routes/
│   │       └── health.py, chat.py, resources.py (EC2), dashboard.py (other 6 services)
│   └── tests/
│       └── test_ec2_service.py, test_cloudwatch_service.py, test_cloudtrail_service.py
│           test_health.py, test_mcp_server.py
│
└── opspilot-frontend/
    ├── package.json, tsconfig.json, next.config.mjs, tailwind.config.ts, postcss.config.mjs
    ├── .eslintrc.json, .env.local.example
    ├── app/
    │   ├── layout.tsx, globals.css, page.tsx (redirects to /chat)
    │   └── chat/page.tsx, resources/page.tsx
    └── components/
        └── NavBar.tsx, ChatPanel.tsx, ReasoningTrace.tsx
            ResourcesPanel.tsx, ServiceCards.tsx, Sparkline.tsx, StatusBadge.tsx
            lib/api.ts                  # typed fetch client
```

---

## 5. AWS account state (real resources currently provisioned)

| Resource | Identifier | Notes |
|---|---|---|
| EC2 | `i-02eaea0572f34f5b5`, t3.micro, us-east-1d | Tags: Project=opspilot, Name=opspilot-agent-target |
| S3 | `opspilot-demo-ahmad-2026` | |
| DynamoDB | `opspilot-investigations` (partition key `id`, on-demand) | **Code is written and wired in (Phase 8), but writes are currently blocked** — the `opspilot-app` IAM policy only grants `ListTables`/`DescribeTable`; needs `PutItem`+`Scan` scoped to this table's ARN added before investigations actually persist. See Section 6. |
| SNS | `opspilot-alerts` | |
| Lambda | `opspilot-function`, python3.14 runtime | Placeholder, dashboard card only |
| RDS | `opspilot-db`, MySQL, db.t4g.micro | **Kept stopped** — the one service drawing real credit; auto-restarts after 7 days stopped, check periodically |
| Region | `us-east-1` | All resources, one region by design |
| IAM | `opspilot-app` user, custom read-only policy (see setup guide Section 4) | App never uses admin credentials |

---

## 6. Completed phases

| Phase | What | Status |
|---|---|---|
| 1 | FastAPI skeleton, EC2/CloudWatch tools, agent + provider fallback, Docker | ✅ |
| 2 | Resources dashboard, structured logging w/ request IDs | ✅ |
| 3 | Multi-step investigation (status checks + CloudTrail), reasoning trace UI | ✅ |
| 4 | Dashboard breadth (7 services), full chat/dashboard tool parity | ✅ |
| 5 | Recommendations | Skipped — roadmap's own "cut first if time-constrained" |
| 6 | CI (green), README | ✅ / demo GIF skipped by explicit choice |
| 7 | MCP server exposing the same services/ layer | ✅ built **and verified** (2026-07-08) — `app/mcp/server.py` registers 11 tools across all 8 services; `tests/test_mcp_server.py` (12 tests) passes against the real installed `mcp==1.28.1` package (not mocked); a manual stdio JSON-RPC smoke test (`initialize` → `tools/list`) confirmed a real MCP client sees all 11 tools |
| 8 | RAG — investigation memory | ⚠️ **code complete, blocked on one manual IAM change** (2026-07-08) — `app/services/investigation_service.py` (Gemini `gemini-embedding-001` embeddings + DynamoDB persistence + brute-force cosine similarity), wired as `find_similar_past_investigations` into both the Agents SDK chat tools and the MCP server, plus automatic post-turn persistence in `orchestrator.py`. Verified live against the real backend: the embedding call succeeds end-to-end; the `PutItem` call correctly fails with `AccessDeniedException` because the IAM policy hasn't been updated yet (see Section 6a). Chat degrades gracefully — a failed save never breaks the chat reply, it just logs a warning. |

**Not yet started:** Langfuse (observability) — see Section 8.

### 6a. Required IAM policy addition for Phase 8

The `opspilot-app` IAM user needs a new statement added (don't loosen the existing one — keep it scoped to just this table, matching the project's least-privilege pattern):

```json
{
  "Effect": "Allow",
  "Action": [
    "dynamodb:PutItem",
    "dynamodb:Scan"
  ],
  "Resource": "arn:aws:dynamodb:us-east-1:476141958109:table/opspilot-investigations"
}
```

Add this as a second statement in the same policy documented in `AWS_ZeroSpend_Setup_Guide.md` Section 4. Until this is added, Phase 8 runs in a fully degraded (no-op) mode — chat still works, nothing persists.

---

## 7. Key bugs fixed / lessons learned (don't repeat these)

1. **Groq's `llama-3.3-70b-versatile` produced malformed tool-call syntax** on multi-tool prompts (a `<function=...>` tag, not valid JSON) — switched to `openai/gpt-oss-120b`, which Groq's own docs use for tool-calling examples.
2. **`openai` version conflict:** `openai-agents==0.2.8` requires `openai>=1.99.6,<2`; an earlier pin of `openai==1.58.1` broke `pip install`. Fixed — watch for this if `openai-agents` is ever upgraded.
3. **`.env` silently not loading** — twice. Root cause: `config.py` needs an explicit `load_dotenv()` call; boto3/pydantic-settings don't do this automatically. This fix got *reverted* twice when files were re-shipped for unrelated changes (a working copy without the manual patch overwrote the fixed one). If AWS creds ever mysteriously stop loading again, check `config.py` for `load_dotenv()` first.
4. **RDS creation KMS error** ("specified KMS key does not exist") — happens with Standard/full-configuration create; fix is disabling encryption (not needed for a demo DB) or explicitly picking the default `aws/rds` key from the dropdown instead of whatever's pre-filled.
5. **Next.js 14 EOL (Oct 2025)** — `npm audit` surfaces real, current CVEs. Bumped to `next@14.2.35` (final 14.x patch) + `postcss@8.5.10` (root dependency + npm `overrides` to force it inside Next's own bundled copy too). Remaining findings are an accepted, documented risk (see README's "Known limitations & accepted risks") — none of the vulnerable code paths (Server Actions, Middleware, Image Optimization, WebSockets) are used here, and the app only runs locally. **Revisit before any public deployment.**
6. **`next lint` needs `eslint` + `eslint-config-next` as explicit devDependencies** plus a checked-in `.eslintrc.json`, or it hits an interactive setup prompt that hangs in CI.
7. **Chat markdown wasn't rendering** — `ChatPanel.tsx` displayed raw text instead of parsing the agent's actual (correct) markdown output. Fixed with `react-markdown` + `remark-gfm` + `@tailwindcss/typography`.
8. **Agent inconsistently skipped tools on broad "list everything" questions** — LLM tool selection isn't deterministic by default. Fixed by explicitly instructing the agent to call all 7 read tools for inventory-style questions and report "none found" rather than silently omitting a service.
9. **`mcp[cli]>=1.27` requires `pydantic>=2.11`**, conflicting with the repo's prior `pydantic==2.10.3` pin — bumped to `pydantic==2.13.4`. If you ever see a pip `ResolutionImpossible` mentioning both `mcp` and `pydantic`, this is why.
10. **On Windows, a `pip install --upgrade` that gets interrupted mid-uninstall** (e.g. a locked file in a package being replaced) can leave a package **half-uninstalled but with its files still on disk under a `~name` directory**, while dependent packages it pulled in (e.g. a bumped `pydantic`) are already installed — `pip show <pkg>` will report nothing installed even though the code still imports fine. Always re-run `pip install -r requirements.txt` and `pip check` after any interrupted install to confirm a consistent state, and clean up leftover `~name`/`~name-*.dist-info` directories in `site-packages`.
11. **Gemini's `text-embedding-004` model is deprecated/404s as of mid-2026** — current models for this API key are `gemini-embedding-001` (stable, what we use), `gemini-embedding-2`, `gemini-embedding-2-preview`. If embeddings ever start 404ing, call `GET /v1beta/models` and grep for `embedContent` in `supportedGenerationMethods` to see what's actually available before assuming the code is broken.
12. **Never pass a Google Generative Language API key as a `?key=` query param** — httpx (and most HTTP clients) embed the full request URL, including query string, in exception messages on a non-2xx response, so a transient API error becomes a credential leaked straight into your logs. Use the `x-goog-api-key` header instead — same auth, nothing sensitive ends up in the URL. (`investigation_service._embed` does this correctly; if you add another Google REST call, match the pattern.)

---

## 8. Rejected approaches (don't resurrect these)

- **A separate `opspilot-v2` repo** to pad project count for the CV — rejected. Two near-identical repos read as duplication to a technical interviewer, not genuine breadth. Decision: keep everything in this one repo; extend it in place.
- **A user-uploaded "Project_Blueprint.docx"** proposing (a) an MCP server whose tools returned hardcoded fake data disguised as live AWS calls, (b) a "RAG" implementation that was actually just keyword substring matching, and (c) a resume-language table coaching inflated technical claims disproportionate to the real implementation — rejected outright, both as a technical regression (fake data replacing a working real MCP server) and as something inappropriate to build toward a CV.

---

## 9. Next steps

### RAG — investigation memory (code complete, one manual step left)
- Built 2026-07-08: `app/services/investigation_service.py`, `app/tools/investigation_tools.py`, wired into the orchestrator (auto-save every turn) and the MCP server (`find_similar_past_investigations` tool).
- Embedding approach — **decided:** Gemini `gemini-embedding-001` (free API endpoint, reuses the already-configured `GEMINI_API_KEY`, avoids adding `torch`/`sentence-transformers` to keep the image lean).
- No dedicated vector DB — brute-force cosine similarity over embeddings pulled from DynamoDB at query time, confirmed appropriate at this scale.
- **Remaining:** add the IAM policy statement in Section 6a above. Until then, `save_investigation` fails with `AccessDeniedException` on every turn (logged as a warning, chat still works — verified live) and `find_similar_past_investigations` will fail the same way once there's anything to scan.
- Once the IAM change is live, do one more live verification: ask a question, confirm the item lands in the table (`aws dynamodb scan --table-name opspilot-investigations`), then ask a similar question and confirm the agent actually calls `find_similar_past_investigations` and surfaces the match.

### Langfuse — observability (not started, legitimate future addition)
- Real, open-source LLM tracing tool — a genuine "LLMOps" CV line if implemented honestly (unlike the rejected blueprint's version).
- Check current free-tier/self-host details before committing — hasn't been researched yet in this project.

---

## 10. How to continue in a new session

**Recommended: use Claude Code** (VS Code extension, terminal, or desktop app) opened directly on the `opspilot-ai` folder. It reads the real files directly — no more zip round-tripping through chat — and a fresh Claude Code session only needs this document for the *narrative* (why decisions were made, what's fixed, what's next); it can inspect actual current file contents itself. **Before trusting any "✅ done" claim in this document, cross-check it against `git log` — this document has drifted from reality before (see the meta-lesson at the top).**

**Alternative: a new claude.ai chat** — attach this document (ideally as a Project file if this conversation is inside a Claude Project, so all future chats in it see it automatically), and point it at `github.com/ahmad2474/opspilot-ai` (main branch) as the current source of truth for real file contents — better than trusting a static dump that goes stale after the next commit.
