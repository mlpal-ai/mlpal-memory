# longmemeval — s — arm direct — 2026-09-22T01:43Z

reader `claude-sonnet-5` · judge `claude-sonnet-5` · service http://localhost:8000 · n=18 scored=18 errors=0

_official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`_

| metric | value |
|---|---|
| accuracy | 0.889 |
| recall@5 (session) | 0.972 |
| recall@15 (session) | 0.986 |
| context tokens mean / p95 | 4545.0 / 4788 |
| search ms p50 / p95 | 127 / 143 |
| answer ms p50 / p95 (reader call) | 2678 / 7731 |
| ingest | 847 docs, 8838768 chars, 262295 ms (33698 chars/s) |
| model cost | {"claude-sonnet-5": {"input": 142101, "output": 4890, "calls": 37, "compute_units": 0.0333102}} ≈ $0.4997 |
| wall | 378.2 s |

## By type

| type | n | accuracy | recall@5 | recall@15 |
|---|---|---|---|---|
| knowledge-update | 3 | 1.0 | 1.0 | 1.0 |
| multi-session | 3 | 0.333 | 0.833 | 0.917 |
| single-session-assistant | 3 | 1.0 | 1.0 | 1.0 |
| single-session-preference | 3 | 1.0 | 1.0 | 1.0 |
| single-session-user | 3 | 1.0 | 1.0 | 1.0 |
| temporal-reasoning | 3 | 1.0 | 1.0 | 1.0 |
