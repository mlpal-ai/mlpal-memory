# longmemeval — oracle — arm direct — 2026-09-20T03:32Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=96 scored=96 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `ours` · write-time model usage: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · items created 171 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.927 |
| recall@5 (session) | 0.996 |
| recall@15 (session) | 0.996 |
| context tokens mean / p95 | 2427.854 / 4081 |
| search ms p50 / p95 | 66 / 93 |
| answer ms p50 / p95 (reader call) | 3086 / 5600 |
| ingest | 171 docs, 2425274 chars, 102485 ms (23665 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 401779, "output": 17279, "calls": 194, "compute_units": 0.09763480000000004}} ≈ $1.4645 |
| wall | 480.9 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| knowledge-update | 16 | 1.0 | 1.0 | 1.0 |
| multi-session | 16 | 0.812 | 0.984 | 0.984 |
| single-session-assistant | 16 | 0.938 | 1.0 | 1.0 |
| single-session-preference | 16 | 0.938 | 1.0 | 1.0 |
| single-session-user | 16 | 1.0 | 1.0 | 1.0 |
| temporal-reasoning | 16 | 0.875 | 0.99 | 0.99 |
