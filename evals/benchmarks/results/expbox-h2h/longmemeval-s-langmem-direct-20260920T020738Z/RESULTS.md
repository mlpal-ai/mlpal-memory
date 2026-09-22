# longmemeval — s — arm direct — 2026-09-20T04:17Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=18 scored=18 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `langmem` · write-time model usage: {'llm_calls': 847, 'llm_input_tokens': 5004908, 'llm_output_tokens': 532101, 'embed_calls': 3718, 'embed_tokens': 900954} ≈ $7.6834 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 18, 'embed_tokens': 353} ≈ $0.0 · items created 1753 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.389 |
| recall@5 (session) | 0.694 |
| recall@15 (session) | 0.694 |
| context tokens mean / p95 | 4598.556 / 5760 |
| search ms p50 / p95 | 296 / 393 |
| answer ms p50 / p95 (reader call) | 2662 / 7360 |
| ingest | 847 docs, 8838768 chars, 7670218 ms (1152 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 124789, "output": 3844, "calls": 36, "compute_units": 0.028801800000000002}} ≈ $0.432 |
| wall | 7771.4 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| knowledge-update | 3 | 0.333 | 0.5 | 0.5 |
| multi-session | 3 | 0.0 | 0.667 | 0.667 |
| single-session-assistant | 3 | 0.333 | 1.0 | 1.0 |
| single-session-preference | 3 | 1.0 | 0.667 | 0.667 |
| single-session-user | 3 | 0.333 | 0.333 | 0.333 |
| temporal-reasoning | 3 | 0.333 | 1.0 | 1.0 |
