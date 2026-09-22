# longmemeval — s — arm direct — 2026-09-21T23:41Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=18 scored=18 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `ours` · write-time model usage: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · items created 847 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.778 |
| recall@5 (session) | 0.958 |
| recall@15 (session) | 0.972 |
| context tokens mean / p95 | 4005.889 / 4212 |
| search ms p50 / p95 | 121 / 131 |
| answer ms p50 / p95 (reader call) | 3445 / 5387 |
| ingest | 847 docs, 8838768 chars, 394143 ms (22425 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 113855, "output": 3441, "calls": 36, "compute_units": 0.026212}} ≈ $0.3932 |
| wall | 489.3 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| knowledge-update | 3 | 1.0 | 1.0 | 1.0 |
| multi-session | 3 | 0.333 | 0.75 | 0.833 |
| single-session-assistant | 3 | 1.0 | 1.0 | 1.0 |
| single-session-preference | 3 | 0.667 | 1.0 | 1.0 |
| single-session-user | 3 | 0.667 | 1.0 | 1.0 |
| temporal-reasoning | 3 | 1.0 | 1.0 | 1.0 |
