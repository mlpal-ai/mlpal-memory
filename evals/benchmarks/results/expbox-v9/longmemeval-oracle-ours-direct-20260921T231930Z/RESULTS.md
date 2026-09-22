# longmemeval — oracle — arm direct — 2026-09-21T23:21Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=6 scored=6 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `ours` · write-time model usage: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · items created 11 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.833 |
| recall@5 (session) | 1.0 |
| recall@15 (session) | 1.0 |
| context tokens mean / p95 | 2714.667 / 4314 |
| search ms p50 / p95 | 64 / 86 |
| answer ms p50 / p95 (reader call) | 3219 / 5524 |
| ingest | 11 docs, 162722 chars, 114227 ms (1425 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 27170, "output": 1037, "calls": 12, "compute_units": 0.006471}} ≈ $0.0971 |
| wall | 147.6 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| knowledge-update | 1 | 1.0 | 1.0 | 1.0 |
| multi-session | 1 | 0.0 | 1.0 | 1.0 |
| single-session-assistant | 1 | 1.0 | 1.0 | 1.0 |
| single-session-preference | 1 | 1.0 | 1.0 | 1.0 |
| single-session-user | 1 | 1.0 | 1.0 | 1.0 |
| temporal-reasoning | 1 | 1.0 | 1.0 | 1.0 |
