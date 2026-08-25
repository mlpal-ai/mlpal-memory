"""Local source collectors: turn the machine's existing knowledge into memory.

Three collectors feed the same governed ingestion surface (documents + episodes):

- ``claude_code``  — past Claude Code sessions (~/.claude/projects/**/*.jsonl)
- ``markdown``     — CLAUDE.md / AGENTS.md / MEMORY.md / skills across the code root
- ``repos``        — repo cards + READMEs/docs for every git repo in the code root

All collectors are idempotent: content-hashed event ids dedup at the API, and a local
state file skips unchanged inputs. Staleness is handled bitemporally — a file's own
timestamp (mtime / git commit date) becomes the document's ``valid_at``, so outdated
sources rank below current ones and remain reachable via as-of reads.
"""
