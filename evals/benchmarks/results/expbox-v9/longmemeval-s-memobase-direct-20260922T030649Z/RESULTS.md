# longmemeval — s — arm direct — 2026-09-22T13:03Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=52 scored=52 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `memobase` · write-time model usage: {'llm_calls': 7179, 'llm_input_tokens': 19924950, 'llm_output_tokens': 3095432, 'embed_calls': 4208, 'embed_tokens': 1657673} ≈ $35.4353 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 52, 'embed_tokens': 809} ≈ $0.0 · items created 5494 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.865 |
| recall@5 (session) | 0.0 |
| recall@15 (session) | 0.0 |
| context tokens mean / p95 | 5815.462 / 5965 |
| search ms p50 / p95 | 328 / 555 |
| answer ms p50 / p95 (reader call) | 3617 / 6680 |
| ingest | 2475 docs, 25626346 chars, 33001401 ms (777 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 485952, "output": 12731, "calls": 108, "compute_units": 0.10992139999999999}} ≈ $1.6488 |
| wall | 35811.9 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| multi-session | 14 | 0.714 | 0.0 | 0.0 |
| single-session-preference | 16 | 0.812 | 0.0 | 0.0 |
| single-session-user | 15 | 1.0 | 0.0 | 0.0 |
| temporal-reasoning | 7 | 1.0 | 0.0 | 0.0 |
