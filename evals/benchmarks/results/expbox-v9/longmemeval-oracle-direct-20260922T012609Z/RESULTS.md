# longmemeval — oracle — arm direct — 2026-09-22T01:37Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=96 scored=96 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

| metric | value |
|---|---|
| accuracy | 0.885 |
| recall@5 (session) | 0.996 |
| recall@15 (session) | 0.996 |
| context tokens mean / p95 | 2785.948 / 4661 |
| search ms p50 / p95 | 77 / 98 |
| answer ms p50 / p95 (reader call) | 3343 / 6185 |
| ingest | 171 docs, 2425274 chars, 99532 ms (24367 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 480590, "output": 18680, "calls": 195, "compute_units": 0.11479799999999994}} ≈ $1.722 |
| wall | 688.0 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| knowledge-update | 16 | 0.875 | 1.0 | 1.0 |
| multi-session | 16 | 0.75 | 0.984 | 0.984 |
| single-session-assistant | 16 | 0.938 | 1.0 | 1.0 |
| single-session-preference | 16 | 0.875 | 1.0 | 1.0 |
| single-session-user | 16 | 1.0 | 1.0 | 1.0 |
| temporal-reasoning | 16 | 0.875 | 0.99 | 0.99 |
