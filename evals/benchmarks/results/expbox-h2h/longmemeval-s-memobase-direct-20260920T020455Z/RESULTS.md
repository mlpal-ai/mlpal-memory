# longmemeval — s — arm direct — 2026-09-20T04:37Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=15 scored=15 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `memobase` · write-time model usage: {'llm_calls': 2037, 'llm_input_tokens': 5663971, 'llm_output_tokens': 895902, 'embed_calls': 1194, 'embed_tokens': 475799} ≈ $10.153 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 15, 'embed_tokens': 246} ≈ $0.0 · items created 1563 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.8 |
| recall@5 (session) | 0.0 |
| recall@15 (session) | 0.0 |
| context tokens mean / p95 | 5779.0 / 5918 |
| search ms p50 / p95 | 324 / 437 |
| answer ms p50 / p95 (reader call) | 3855 / 5619 |
| ingest | 710 docs, 7361094 chars, 9057543 ms (813 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 134139, "output": 3799, "calls": 30, "compute_units": 0.0306268}} ≈ $0.4594 |
| wall | 9148.5 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| knowledge-update | 3 | 1.0 | 0.0 | 0.0 |
| multi-session | 3 | 0.333 | 0.0 | 0.0 |
| single-session-preference | 3 | 0.667 | 0.0 | 0.0 |
| single-session-user | 3 | 1.0 | 0.0 | 0.0 |
| temporal-reasoning | 3 | 1.0 | 0.0 | 0.0 |
