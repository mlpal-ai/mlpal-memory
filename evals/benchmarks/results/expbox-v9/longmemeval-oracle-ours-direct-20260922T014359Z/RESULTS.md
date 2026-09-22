# longmemeval — oracle — arm direct — 2026-09-22T01:53Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=96 scored=96 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `ours` · write-time model usage: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · items created 171 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.865 |
| recall@5 (session) | 0.992 |
| recall@15 (session) | 0.992 |
| context tokens mean / p95 | 1892.604 / 2539 |
| search ms p50 / p95 | 74 / 90 |
| answer ms p50 / p95 (reader call) | 3066 / 10974 |
| ingest | 171 docs, 2425274 chars, 109768 ms (22095 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 341982, "output": 20272, "calls": 199, "compute_units": 0.08866840000000001}} ≈ $1.33 |
| wall | 565.8 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| knowledge-update | 16 | 1.0 | 1.0 | 1.0 |
| multi-session | 16 | 0.625 | 0.984 | 0.984 |
| single-session-assistant | 16 | 1.0 | 1.0 | 1.0 |
| single-session-preference | 16 | 0.938 | 1.0 | 1.0 |
| single-session-user | 16 | 1.0 | 1.0 | 1.0 |
| temporal-reasoning | 16 | 0.625 | 0.969 | 0.969 |
