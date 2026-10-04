# Writing a bisect predicate

A predicate is a script `git bisect run` calls at each commit. Its exit status is the verdict.

| Exit | Meaning |
|---|---|
| 0 | good (bug absent) |
| 1-124, 126, 127 | bad (bug present) |
| 125 | skip (cannot test this commit) |
| 128-255 | abort the bisect |

## Template (save outside the work tree, chmod +x)
```sh
#!/bin/sh
# Deterministic bisect predicate. Runs inside the bisect worktree.
set -u
# 1. Build / install. A failure here is "cannot test", not "bad".
make build >/dev/null 2>&1 || exit 125
# 2. (If lockfiles changed across the range) reinstall: e.g. npm ci / uv sync / pip install -r requirements.txt
# 3. Run ONE focused check of the symptom. Exit 1 when the symptom is present.
./run-the-failing-test.sh >/dev/null 2>&1 && exit 0
exit 1
```

## Rules of thumb
- Deterministic: same commit gives same verdict. For flaky symptoms, repeat N times and call it bad only if it fails every time (or good only if it passes all N). If it still flickers, bisect manually.
- Test the observable symptom, not an implementation detail that may not exist in older commits.
- 125 for build/setup failures. Otherwise an unrelated broken build is reported as the culprit.
- Use `set -o pipefail` (bash) or avoid pipelines: a pipeline reports only the last command's status.
- Do not leave modified tracked files behind; the next checkout would fail or carry them forward.
- Test files that do not exist in old commits: copy them in from outside the tree at run time, or the predicate will exit 1 on every old commit.
- Time-based or network-dependent tests are not reproducible; stub them.
- Databases/migrations: recreate state per step if `reproducibility_risks` lists migrations or schema.
- Validate by hand first: run the predicate on the good ref (expect 0) and on the bad ref (expect 1).

## Passing it
`git bisect run /abs/path/predicate.sh` or, for one-liners, `git bisect run sh -c '<command>'` (the exit status of the command is used). `warp.py bisect plan --test CMD` only checks the string is a single non-empty line; it never runs it.
