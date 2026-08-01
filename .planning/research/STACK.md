# Stack Research

**Domain:** Adding semantic retrieval + periodic LLM synthesis to an existing single-file Python/SQLite Telegram bot (one user, hundreds-to-low-thousands of short texts)
**Researched:** 2026-08-01
**Confidence:** MEDIUM (web-search-verified against multiple independent sources; no official-docs/Context7 access in this run — see Sources)

## Executive framing

This is a **tiny-data** problem wearing a **big-data** vocabulary. The corpus (journal + notes + tasks + reminders, one user) is hundreds to low thousands of short texts. Nearly every "production RAG stack" article assumes 50K–50M documents and multi-tenant QPS. Applying that tooling here is the single biggest risk this research found — most of the popular options (FAISS, ChromaDB, pgvector, hybrid RRF pipelines) solve problems this project doesn't have, at the cost of new dependencies, new failure modes, and drift from the existing single-file/dual-storage architecture. The recommendations below are deliberately the boring, small option at every layer, with an explicit escalation path if the corpus or requirements outgrow it.

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| numpy | ≥1.26 (any recent) | In-memory cosine similarity over embedding vectors | At "hundreds to low thousands" of vectors, a brute-force dot product against a `(N, dim)` float32 matrix is sub-millisecond — there is no scale problem to solve. It is one new dependency (likely already transitively present) with zero extension-loading risk, zero new services, and it is fully transparent for the debug-mode requirement ("inspect the assembled prompt") — you can print the top-k scores directly. Confidence: HIGH (this is arithmetic, not a claim needing a source). |
| SQLite BLOB column in `bot_memory.db` | existing SQLite 3 (stdlib) | Persist embedding vectors alongside their source row | Matches the existing architecture exactly — `bot_memory.db` already holds notes/journal/memory as the durable store. Add one table, e.g. `embeddings(chat_id, source_type, source_id, model, dim, vector BLOB, updated_at)`, storing `np.ndarray.astype('float32').tobytes()`. No new storage engine to back up, export, or reason about. Confidence: HIGH. |
| OpenAI `text-embedding-3-small` | current OpenAI API (`openai` SDK ≥1.0.0, already a dependency) | Convert text to a vector at write time (journal/note/task/reminder created or edited) and at query time | Zero new secrets (reuses `OPENAI_API_KEY`, already required), zero new SDK (the `openai` client is already in `requirements.txt` and already used for chat), and cost is negligible at this volume: embedding the user's *entire* historical corpus (a few thousand short entries) costs low single-digit cents at $0.02/1M tokens; every future write costs a fraction of a cent. Multilingual quality is a real, verified step up over the deprecated `ada-002` (MIRACL multilingual score 44.0 vs 31.4), which is the bar this project actually needs to clear (current retrieval is 0% — pure substring). Confidence: MEDIUM (pricing/MTEB numbers cross-checked across independent sources, not fetched from OpenAI's own pricing page in this run). |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `sentence-transformers` | ≥3.x (fallback only, not installed by default) | Local, self-hosted multilingual embeddings | Only if empirical testing shows `text-embedding-3-small` doesn't clear the bar on the project's own worked example (Russian "погода" query must match a "жаркий день" entry). Use `intfloat/multilingual-e5-base` (smaller, ~1.1GB, covers Russian) as the first local model to try, `intfloat/multilingual-e5-large` or `BAAI/bge-m3` if quality still isn't enough. **Important usage detail that is easy to get wrong and silently tanks quality:** E5 models require `"query: "` / `"passage: "` prefixes on the input text — omitting them is a documented common mistake. |
| `rank-bm25` | latest (fallback only) | Pure-Python lexical/keyword scoring, no SQLite extension needed | Only if you later decide hybrid (lexical + semantic) retrieval is worth the complexity (see below) and want to avoid the SQLite-extension-loading risk that FTS5+Snowball carries (see Pitfalls). Operates on plain Python token lists — no compiled dependency, no `enable_load_extension` risk. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| A one-line startup check: `sqlite3.connect(":memory:").enable_load_extension` | Verify whether the deployed Python's stdlib `sqlite3` was compiled with loadable-extension support | Run this **before** committing to `sqlite-vec` or `fts5-snowball` (see Pitfalls) — the CPython `sqlite3` module is not guaranteed to have `SQLITE_ENABLE_LOAD_EXTENSION` compiled in on every Linux distro build, and this project's numpy-brute-force recommendation deliberately has zero exposure to this risk. |

## Installation

```bash
# Core (numpy likely already present transitively; make it explicit)
pip install numpy

# openai SDK already required by bot.py — no change needed for embeddings,
# just a new call: client.embeddings.create(model="text-embedding-3-small", input=text)

# Only if local embeddings become necessary (see "When to escalate" below):
pip install sentence-transformers torch --index-url https://download.pytorch.org/whl/cpu
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|--------------------------|
| numpy brute-force cosine | `sqlite-vec` (pip install sqlite-vec, current 0.1.9) | If the corpus grows past roughly tens of thousands of rows, or you want filtered vector search expressed directly in SQL (`WHERE source_type = 'journal'` alongside `MATCH`) rather than filtering in Python. It's a genuinely lightweight, pure-C, no-dependency extension (successor to the FAISS-backed `sqlite-vss`, which is effectively superseded) — not a bad choice, just unnecessary at current scale, and it adds a loadable-extension dependency this project doesn't need yet (see Pitfalls). |
| numpy brute-force cosine | FAISS | Never, for this project. FAISS is an ANN library built for millions of vectors and high query throughput — a different tool at a different layer of abstraction ("comparing PostgreSQL to numpy," per one source). Adopting it here is scale-engineering the roadmap explicitly warns against. |
| numpy brute-force cosine | ChromaDB | Never, for this project as designed. ChromaDB is a full embedded vector database with its own persistence layer, metadata filtering, and dependency footprint (onnxruntime, its own storage format) — it duplicates the storage decision the project has already made (dual JSON + SQLite) with a third store. One source states ChromaDB starts winning over numpy brute-force "around 5,000 items" — this project is below that line and the win is about query latency at write-heavy scale, not correctness. |
| numpy brute-force cosine | pgvector | Only if this project ever migrates off SQLite to Postgres for reasons unrelated to retrieval (e.g. genuine multi-user scale). Standing up Postgres solely to get a vector column, for one user, is the clearest form of scale-engineering the question anticipated — explicitly do not do this. |
| OpenAI `text-embedding-3-small` | Cohere `embed-multilingual-v3.0` / Voyage `voyage-3` | If, after empirical testing, OpenAI's cross-lingual Russian↔English matching genuinely fails on real queries and a self-hosted model is undesirable. Cohere's non-English/non-Latin-script retrieval is reported as meaningfully stronger (~15–20% on some benchmarks) and Voyage leads some retrieval benchmarks — but both cost 3–6x more per token (Cohere ~$0.10–0.12/1M, Voyage ~$0.06/1M vs OpenAI $0.02/1M) and, more importantly, require a **new API key, new provider relationship, and new secret to manage** in a project whose `env` file already juggles four provider credentials. At this volume the absolute dollar cost of any of these is trivial (cents); the real cost is operational surface area for a single user. Not recommended as a first move. |
| OpenAI `text-embedding-3-small` | Local multilingual model (BGE-M3, multilingual-e5) | If cross-lingual quality genuinely fails, or if the user later decides journal content should never leave the box for embedding purposes. Note this privacy argument is weaker than it looks: the same journal text is *already* sent to OpenAI or Groq for every chat completion, so embedding it via OpenAI's API is not a new category of exposure — it's an incremental data flow, not a new one. The stronger argument for going local is avoiding a second paid API surface, not privacy. |
| Dense-only semantic search (v1) | Hybrid BM25 + dense with Reciprocal Rank Fusion | Only if, after shipping dense-only semantic search, real usage shows exact-term queries (names, specific words) get lost among semantically-similar-but-wrong results. At this document count, brute-force dense search already scores *every* document — there's no ranking cutoff pressure hiding exact matches the way there is at web scale. Hybrid+RRF is a proven technique (RRF beat both BM25-only and dense-only on a public benchmark, NDCG 0.7068 vs 0.6983 vs 0.6953) but it is meaningfully more moving parts: a synced FTS5 index, a working Russian tokenizer, and fusion logic. That's real engineering cost for a benefit that may not be observable with one user's few hundred queries a month. |
| APScheduler in-memory jobs + existing `job_log` catch-up pattern | APScheduler `SQLAlchemyJobStore` | If this project ever needs a scheduler that survives process crashes *without* relying on the app being restarted afterward, or if `job_log`-based catch-up proves insufficient in practice. Not recommended now: `python-telegram-bot`'s `JobQueue` wraps its own `AsyncIOScheduler` instance, and swapping its job store is not a documented, first-class integration point — doing so would mean reaching into a library internal, which cuts against "follow existing patterns." The existing `job_log` + `restore_all_jobs()` mechanism already solves exactly this class of problem in production today (deadline alerts, idle nudges, weekly digest) — a periodic synthesis job is simply one more entry in that same, already-proven pattern. |
| — | APScheduler 4.0 | Do not adopt. As of mid-2026 it is still alpha (current stable is 3.11.x; the last observed 4.0 pre-release predates that), explicitly documented as not for production use, with breaking changes possible between pre-releases and no automatic migration path from 3.x job stores. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|--------------|
| FAISS, ChromaDB, pgvector, or any dedicated vector database | All are built for corpora orders of magnitude larger than "hundreds to low thousands," one user. Each adds a dependency, a failure mode, and (for ChromaDB/pgvector) a second storage engine that fragments the project's deliberately simple dual-store architecture. This is the textbook shape of the "scale engineering" the project explicitly rules out. | numpy brute-force cosine over vectors stored as BLOBs in the existing `bot_memory.db`. |
| `LaBSE` (Language-agnostic BERT Sentence Embedding) as the retrieval embedding model | LaBSE was purpose-built and trained for **bitext mining / translation-pair alignment** (its headline metric is Tatoeba bi-text retrieval accuracy across 112 languages), not for general asymmetric query→passage retrieval or topical clustering. It will find a Russian sentence that is a literal translation of an English one, but is not trained for the "query about a topic finds a passage that discusses that topic without using the same words" case this project actually needs (the "погода" / "жаркий день" example is topical inference, not translation alignment). | `multilingual-e5` or `BGE-M3`, both trained specifically for retrieval (query/passage contrastive objectives), if a local fallback is needed. |
| SQLite FTS5 + `fts5-snowball` for Russian stemming, as a first move | Two compounding risks: (1) it requires the deployed Python's stdlib `sqlite3` to have been compiled with loadable-extension support, which is **not guaranteed on every Linux distro build** (well-documented as broken by default on macOS and Conda; unverified for this project's specific Amazon Linux 2023 Python — check before relying on it); (2) it's a second SQLite virtual-table mechanism (on top of the new embeddings table) that needs triggers to stay in sync with every write path (journal, notes) — real ongoing maintenance for a benefit (exact lexical matching) that dense embeddings already partially cover. | Ship dense-only semantic search first. If exact-term recall genuinely proves insufficient later, use `rank-bm25` (pure Python, no extension-loading risk) rather than FTS5+Snowball. |
| Standing up a new scheduling library or persistent job store to fix "no supervisor" | This conflates two different problems. APScheduler's in-memory jobs being lost on restart is already solved in this codebase via `job_log` + `restore_all_jobs()` catch-up — extend that same pattern for the periodic synthesis job. The *actual* gap named in the constraints — "the process has no supervisor" — is a process-liveness problem, not a scheduling problem: if `bot.py` crashes outright, no job-store technology fixes that, because nothing restarts the process. | Wrap `bot.py` in a systemd unit with `Restart=on-failure`, mirroring the `secretary-mcp.service` unit this repo already runs for `mcp_server.py`. This is the same fix, reused, for the same class of problem, and it's outside this milestone's stack surface but directly answers the reliability question posed. |
| Cohere or Voyage embeddings as a first move | Real quality edge exists but is not free: a new API key, a new provider relationship, and 3–6x higher token cost, to serve one user whose entire corpus costs cents to embed regardless of provider. The operational surface area is the actual cost here, not the dollar amount. | OpenAI `text-embedding-3-small` first; escalate only if empirical testing on the project's own known failure case shows it's insufficient. |

## Stack Patterns by Variant

**If empirical testing shows `text-embedding-3-small` fails the project's own cross-lingual acceptance test** (a Russian query like "погода" must retrieve an entry like "жаркий день"; an English query must retrieve a matching Russian entry and vice versa):
- Escalate to a local model via `sentence-transformers`, trying `intfloat/multilingual-e5-base` first (smaller footprint), then `multilingual-e5-large` or `BAAI/bge-m3` if still insufficient.
- Because this removes the per-call cost, you'd also re-embed the full corpus once with the new model to keep vectors comparable (embeddings from different models/dimensions are not interchangeable).

**If the corpus meaningfully outgrows "low thousands"** (multi-user, or one user's corpus reaches tens of thousands of rows and brute-force cosine becomes visibly slow — verify with a real timing test before acting on this, not a projection):
- Move the same embedding vectors from a plain BLOB column into a `sqlite-vec` `vec0` virtual table in the same `bot_memory.db` file. This is a storage-layer change only; the embedding model and write/query call sites don't need to change.

**If exact-term / keyword recall proves insufficient after dense-only search ships**:
- Add `rank-bm25` as a second, independent scorer over the same corpus and combine via Reciprocal Rank Fusion (`score(d) = Σ 1/(k + rank(d))`, `k=60` is the standard default) — this avoids FTS5's Russian-tokenizer/extension-loading risk entirely while still getting the accuracy benefit hybrid retrieval is known for.

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|------------------|-------|
| `openai` ≥1.0.0 (already required) | `text-embedding-3-small` / `text-embedding-3-large` | Same client class (`AsyncOpenAI`) already used for chat completions in `bot.py`; embeddings are a separate endpoint (`client.embeddings.create`) on the same client — no new client instantiation pattern needed. |
| `numpy` (any recent 1.x/2.x) | Python 3.12 (already the project's runtime) | No known incompatibilities; numpy 2.x is a mature, current major version as of 2026. |
| `sentence-transformers` (fallback path only) | `torch` (CPU build) | Install the CPU-only torch wheel explicitly (`--index-url https://download.pytorch.org/whl/cpu`) to avoid pulling multi-GB CUDA dependencies onto a small Linux host that has no GPU — this is a real footprint difference (hundreds of MB vs several GB) worth being deliberate about if this path is ever taken. |
| `sqlite-vec` (escalation path only) | stdlib `sqlite3` with loadable-extension support | Verify `enable_load_extension` works on the deployed Python **before** adopting — not guaranteed on every Linux Python build (see Pitfalls / What NOT to Use). |

## Sources

- WebSearch (multiple independent results, cross-checked) — sqlite-vec features/version (0.1.9, Mar 2026), PyPI package name and install command — MEDIUM confidence
- WebSearch (multiple independent results) — FAISS vs ChromaDB vs numpy brute-force framing, including the "ChromaDB beats numpy around 5,000 items" data point — MEDIUM confidence
- WebSearch (multiple independent results) — OpenAI `text-embedding-3-small`/`-large` pricing ($0.02 vs $0.13 per 1M tokens), MTEB (62.3 vs 64.6), MIRACL multilingual (44.0 vs ada-002's 31.4) — MEDIUM confidence (not fetched from openai.com directly in this run)
- WebSearch (multiple independent results) — multilingual-e5 and BGE-M3 Russian MTEB IR standing, BGE-M3's dense+sparse+multi-vector design, MIRACL/MKQA SOTA claims — MEDIUM confidence
- WebSearch (multiple independent results) — LaBSE's bi-text/Tatoeba-alignment design and 83.7% Tatoeba accuracy — MEDIUM confidence
- WebSearch (multiple independent results) — SQLite FTS5 Russian support requiring `fts5-snowball`/Snowball stemmer, `tokenize='snowball russian'` syntax — MEDIUM confidence
- WebSearch (multiple independent results) — hybrid BM25+dense RRF benchmark numbers (WANDS NDCG figures) and RRF formula/default `k=60` — MEDIUM confidence
- WebSearch (multiple independent results) — APScheduler `SQLAlchemyJobStore` behavior, `misfire_grace_time`, and 4.0 alpha status as of mid-2026 (stable line 3.11.x) — MEDIUM confidence
- WebSearch (multiple independent results) — systemd timers vs cron reliability characteristics — MEDIUM confidence
- WebSearch (multiple independent results) — Cohere `embed-multilingual-v3.0` and Voyage `voyage-3` pricing and relative multilingual quality claims — MEDIUM confidence
- WebSearch — Python `sqlite3.enable_load_extension` not guaranteed compiled-in on every platform (documented for macOS/Conda; not specifically verified for this project's Amazon Linux 2023 Python — flagged as a pre-adoption check, not an asserted fact) — MEDIUM confidence, explicitly flagged as needing local verification
- `/home/ec2-user/secretary-bot/.planning/PROJECT.md`, `.planning/codebase/STACK.md`, `.planning/codebase/ARCHITECTURE.md` — existing project constraints, storage architecture, and the exact worked failure case ("погода" / "жаркий день") — HIGH confidence (primary source, this repo)

---
*Stack research for: semantic retrieval + periodic LLM synthesis on a single-file Python/SQLite personal bot*
*Researched: 2026-08-01*
