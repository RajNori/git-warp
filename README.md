# Git Warp

**Git intelligence for Claude Code.**

Git Warp combines deterministic safety hooks with agentic Git forensics.

## Included
- Blocks destructive Git commands before execution
- Injects live repository state at SessionStart
- Stores a flight recorder under `.git/git-warp/`
- Emits an end-of-turn branch/diff risk report
- `/git-xray`, `/git-rescue`, `/git-pr`, `/git-archaeology`, `/git-bisect-ai`
- read-only Git forensic subagent

## Principle
**Deterministic for safety. Agentic for judgment.**

## Local test
```bash
claude --plugin-dir /path/to/git-warp
```

## Marketplace entry
```json
{
  "name": "git-warp",
  "source": "./git-warp",
  "description": "Agentic Git intelligence: safety guardrails, repository forensics, recovery, blast-radius analysis, PR readiness, and AI-assisted bisect.",
  "version": "0.1.0"
}
```

## Roadmap
Semantic change clustering, ownership graphs, merge-conflict ancestry reconstruction, policy-driven PR risk analysis, session-to-commit provenance graphs, and optional GitHub PR context.
