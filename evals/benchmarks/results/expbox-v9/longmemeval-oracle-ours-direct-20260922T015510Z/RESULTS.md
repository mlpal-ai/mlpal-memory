# longmemeval — oracle — arm direct — 2026-09-22T02:04Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=96 scored=96 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `ours` · write-time model usage: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · items created 171 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.833 |
| recall@5 (session) | 0.988 |
| recall@15 (session) | 0.988 |
| context tokens mean / p95 | 1180.01 / 1723 |
| search ms p50 / p95 | 65 / 79 |
| answer ms p50 / p95 (reader call) | 3237 / 6806 |
| ingest | 171 docs, 2425274 chars, 109768 ms (22095 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 235200, "output": 19560, "calls": 199, "compute_units": 0.06659999999999998}} ≈ $0.999 |
| wall | 571.1 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| knowledge-update | 16 | 1.0 | 1.0 | 1.0 |
| multi-session | 16 | 0.688 | 0.984 | 0.984 |
| single-session-assistant | 16 | 0.938 | 1.0 | 1.0 |
| single-session-preference | 16 | 0.75 | 1.0 | 1.0 |
| single-session-user | 16 | 1.0 | 1.0 | 1.0 |
| temporal-reasoning | 16 | 0.625 | 0.946 | 0.946 |
