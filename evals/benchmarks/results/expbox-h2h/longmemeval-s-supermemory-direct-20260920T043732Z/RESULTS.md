# longmemeval — s — arm direct — 2026-09-20T05:21Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=4 scored=4 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `supermemory` · write-time model usage: {'llm_calls': 780, 'llm_input_tokens': 7134644, 'llm_output_tokens': 656065, 'embed_calls': 7294, 'embed_tokens': 626033} ≈ $10.4275 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 4, 'embed_tokens': 46} ≈ $0.0 · items created 6166 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.5 |
| recall@5 (session) | 1.0 |
| recall@15 (session) | 1.0 |
| context tokens mean / p95 | 1717.25 / 2933 |
| search ms p50 / p95 | 327 / 361 |
| answer ms p50 / p95 (reader call) | 3282 / 3397 |
| ingest | 192 docs, 1987494 chars, 2598068 ms (765 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 11818, "output": 334, "calls": 8, "compute_units": 0.0026976}} ≈ $0.0405 |
| wall | 2618.0 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| multi-session | 1 | 0.0 | 1.0 | 1.0 |
| single-session-user | 3 | 0.667 | 1.0 | 1.0 |
