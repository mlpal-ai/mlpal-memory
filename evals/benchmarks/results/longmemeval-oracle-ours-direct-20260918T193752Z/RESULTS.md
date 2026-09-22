# longmemeval — oracle — arm direct — 2026-09-18T20:10Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=96 scored=96 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `ours` · write-time model usage: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · items created 171 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.854 |
| recall@5 (session) | 0.997 |
| recall@15 (session) | 0.997 |
| context tokens mean / p95 | 2443.865 / 4199 |
| search ms p50 / p95 | 991 / 3486 |
| answer ms p50 / p95 (reader call) | 3324 / 6248 |
| ingest | 171 docs, 2425274 chars, 1372740 ms (1767 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 394788, "output": 17123, "calls": 192, "compute_units": 0.0960806}} ≈ $1.6426 |
| wall | 1942.0 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| knowledge-update | 16 | 0.938 | 1.0 | 1.0 |
| multi-session | 16 | 0.562 | 1.0 | 1.0 |
| single-session-assistant | 16 | 1.0 | 1.0 | 1.0 |
| single-session-preference | 16 | 0.875 | 1.0 | 1.0 |
| single-session-user | 16 | 0.938 | 1.0 | 1.0 |
| temporal-reasoning | 16 | 0.812 | 0.979 | 0.979 |
