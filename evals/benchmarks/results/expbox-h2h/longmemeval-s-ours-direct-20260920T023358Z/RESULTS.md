# longmemeval — s — arm direct — 2026-09-20T02:35Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=18 scored=18 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

system `ours` · write-time model usage: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · search-time: {'llm_calls': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0, 'embed_calls': 0, 'embed_tokens': 0} ≈ $0.0 · items created 847 · ingest errors 0

| metric | value |
|---|---|
| accuracy | 0.722 |
| recall@5 (session) | 0.972 |
| recall@15 (session) | 0.986 |
| context tokens mean / p95 | 4037.889 / 4363 |
| search ms p50 / p95 | 120 / 131 |
| answer ms p50 / p95 (reader call) | 2798 / 5426 |
| ingest | 847 docs, 8838768 chars, 375073 ms (23565 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 121096, "output": 4205, "calls": 37, "compute_units": 0.0284242}} ≈ $0.4264 |
| wall | 100.7 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| knowledge-update | 3 | 1.0 | 1.0 | 1.0 |
| multi-session | 3 | 0.333 | 0.833 | 0.917 |
| single-session-assistant | 3 | 0.667 | 1.0 | 1.0 |
| single-session-preference | 3 | 0.667 | 1.0 | 1.0 |
| single-session-user | 3 | 0.667 | 1.0 | 1.0 |
| temporal-reasoning | 3 | 1.0 | 1.0 | 1.0 |
