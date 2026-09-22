# longmemeval — s — arm direct — 2026-09-22T03:45Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=96 scored=96 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `ours` · write-time model usage: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · items created 4602 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.865 |
| recall@5 (session) | 0.95 |
| recall@15 (session) | 0.982 |
| context tokens mean / p95 | 4018.875 / 4403 |
| search ms p50 / p95 | 168 / 201 |
| answer ms p50 / p95 (reader call) | 3703 / 8099 |
| ingest | 4605 docs, 47355158 chars, 2172628 ms (21796 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 618719, "output": 21748, "calls": 197, "compute_units": 0.1454917999999999}} ≈ $2.1824 |
| wall | 2418.2 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| knowledge-update | 16 | 1.0 | 1.0 | 1.0 |
| multi-session | 16 | 0.688 | 0.935 | 0.969 |
| single-session-assistant | 16 | 1.0 | 1.0 | 1.0 |
| single-session-preference | 16 | 0.75 | 1.0 | 1.0 |
| single-session-user | 16 | 1.0 | 0.938 | 1.0 |
| temporal-reasoning | 16 | 0.75 | 0.828 | 0.922 |
