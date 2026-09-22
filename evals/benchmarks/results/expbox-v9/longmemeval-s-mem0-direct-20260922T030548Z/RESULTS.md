# longmemeval — s — arm direct — 2026-09-22T07:00Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=51 scored=51 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `mem0` · write-time model usage: {'llm_calls': 2440, 'llm_input_tokens': 29451748, 'llm_output_tokens': 1224822, 'embed_calls': 4807, 'embed_tokens': 5984847} ≈ $35.6956 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 51, 'embed_tokens': 764} ≈ $0.0 · items created 16826 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.804 |
| recall@5 (session) | 0.962 |
| recall@15 (session) | 0.984 |
| context tokens mean / p95 | 907.569 / 1231 |
| search ms p50 / p95 | 587 / 797 |
| answer ms p50 / p95 (reader call) | 3541 / 6321 |
| ingest | 2440 docs, 25169291 chars, 13755742 ms (1830 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 106636, "output": 11598, "calls": 105, "compute_units": 0.03292519999999999}} ≈ $0.4939 |
| wall | 14093.5 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| multi-session | 16 | 0.688 | 0.879 | 0.948 |
| single-session-preference | 16 | 0.75 | 1.0 | 1.0 |
| single-session-user | 16 | 0.938 | 1.0 | 1.0 |
| temporal-reasoning | 3 | 1.0 | 1.0 | 1.0 |
