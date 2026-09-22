# longmemeval — oracle — arm direct — 2026-09-20T02:42Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=96 scored=96 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `ours` · write-time model usage: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · items created 171 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.917 |
| recall@5 (session) | 0.998 |
| recall@15 (session) | 1.0 |
| context tokens mean / p95 | 2722.76 / 4455 |
| search ms p50 / p95 | 108 / 157 |
| answer ms p50 / p95 (reader call) | 2927 / 5822 |
| ingest | 171 docs, 2425274 chars, 2528990 ms (959 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 454700, "output": 18469, "calls": 196, "compute_units": 0.10940900000000008}} ≈ $1.6411 |
| wall | 494.6 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| knowledge-update | 16 | 0.938 | 1.0 | 1.0 |
| multi-session | 16 | 0.75 | 1.0 | 1.0 |
| single-session-assistant | 16 | 1.0 | 1.0 | 1.0 |
| single-session-preference | 16 | 0.875 | 1.0 | 1.0 |
| single-session-user | 16 | 0.938 | 1.0 | 1.0 |
| temporal-reasoning | 16 | 1.0 | 0.99 | 1.0 |
