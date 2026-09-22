# longmemeval — oracle — arm direct — 2026-09-20T04:28Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=21 scored=21 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `cognee` · write-time model usage: {'llm_calls': 2893, 'llm_input_tokens': 2889006, 'llm_output_tokens': 1069402, 'embed_calls': 2368, 'embed_tokens': 803609} ≈ $8.2521 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 84, 'embed_tokens': 784} ≈ $0.0 · items created 0 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.81 |
| recall@5 (session) | 0.956 |
| recall@15 (session) | 0.956 |
| context tokens mean / p95 | 5826.238 / 5995 |
| search ms p50 / p95 | 2309 / 2765 |
| answer ms p50 / p95 (reader call) | 3088 / 4752 |
| ingest | 56 docs, 824238 chars, 515852 ms (1598 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 191888, "output": 4164, "calls": 43, "compute_units": 0.0425416}} ≈ $0.6381 |
| wall | 671.7 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| multi-session | 5 | 0.4 | 0.95 | 0.95 |
| temporal-reasoning | 16 | 0.938 | 0.958 | 0.958 |
