"""Deterministic path classification shared by X-Ray, PR, Blast Radius and Commit Composer."""
from __future__ import annotations

import fnmatch
import re
from pathlib import PurePosixPath
from typing import Iterable, Optional

from .config import Config

TAGS = (
    "test", "docs", "infra", "migration", "schema", "config", "dependency", "lockfile",
    "ci", "sensitive", "generated", "source", "ui", "secret-file",
)

_LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Pipfile.lock", "Cargo.lock",
    "Gemfile.lock", "go.sum", "composer.lock", "bun.lockb", "bun.lock",
}
_MANIFESTS = {
    "package.json", "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile", "Cargo.toml",
    "Gemfile", "go.mod", "composer.json", "pom.xml", "build.gradle", "build.gradle.kts",
}
_DOC_EXT = {".md", ".rst", ".txt", ".adoc"}
_UI_EXT = {".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".less", ".html"}
_CODE_EXT = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".c", ".h",
    ".cc", ".cpp", ".hpp", ".cs", ".swift", ".scala", ".sh", ".vue", ".svelte",
}
_SENSITIVE_WORDS = re.compile(r"(auth|permission|payment|billing|secret|credential|session|oauth|crypto|security|acl|rbac|login|password)", re.I)
_TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec|specs|e2e|cypress)(/|$)|(^|/)test_[^/]*\.py$|_test\.(py|go|rb)$|\.(test|spec)\.[cm]?[jt]sx?$|(^|/)conftest\.py$", re.I)
_GENERATED_RE = re.compile(r"(^|/)(dist|build|out|node_modules|vendor|\.next|coverage|__pycache__|target)/|\.min\.(js|css)$|\.map$|\.pyc$|\.snap$|\.generated\.|_pb2\.py$", re.I)


def check_pathspec(path: str) -> str:
    """A path/pathspec handed to Git (always placed after ``--``): text without NUL, not empty."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError(f"unsafe path: {path!r}")
    return path


def matches_any(path: str, globs: Iterable[str]) -> bool:
    for g in globs:
        if fnmatch.fnmatch(path, g) or fnmatch.fnmatch(path, g.rstrip("/") + "/*") or fnmatch.fnmatch(PurePosixPath(path).name, g):
            return True
    return False


def classify_path(path: str, cfg: Optional[Config] = None) -> set:
    """Return the set of tags (see ``TAGS``) describing ``path``. Never empty."""
    cfg = cfg or Config()
    p = path.replace("\\", "/").lstrip("./") if path.startswith("./") else path.replace("\\", "/")
    pp = PurePosixPath(p)
    name, ext, low = pp.name, pp.suffix.lower(), p.lower()
    tags = set()

    if name in _LOCKFILES:
        tags |= {"lockfile", "dependency"}
    if name in _MANIFESTS or re.match(r"requirements[-_.a-z]*\.txt$", name):
        tags.add("dependency")
    if _TEST_RE.search(p) or matches_any(p, cfg.test_paths):
        tags.add("test")
    if _GENERATED_RE.search(p) or matches_any(p, cfg.ignored_paths):
        tags.add("generated")
    if p.startswith(".github/workflows/") or name in (".gitlab-ci.yml", "Jenkinsfile", ".travis.yml", "azure-pipelines.yml") or p.startswith(".circleci/"):
        tags |= {"ci", "infra"}
    if (
        name in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", "Makefile", "Procfile")
        or name.startswith("Dockerfile.")
        or ext in (".tf", ".tfvars", ".hcl")
        or re.search(r"(^|/)(terraform|k8s|kubernetes|helm|charts|deploy|infra|infrastructure|ansible|cloudformation)(/|$)", low)
        or "cloudformation" in low
        or matches_any(p, cfg.infra_paths)
    ):
        tags.add("infra")
    if re.search(r"(^|/)(migrations?|alembic|db/migrate|prisma/migrations)(/|$)", low):
        tags |= {"migration", "schema"}
    if ext in (".sql", ".prisma", ".graphql", ".gql", ".proto") or name in ("schema.rb", "schema.py", "openapi.yaml", "openapi.yml", "openapi.json", "swagger.json") or "schema" in name.lower():
        tags.add("schema")
    is_env_template = name.startswith(".env.") and name.rsplit(".", 1)[-1] in ("example", "sample", "template", "dist")
    if not is_env_template and (name in (".env", ".npmrc", ".pypirc", "id_rsa", "id_ed25519", "credentials", "credentials.json") or name.startswith(".env.") or ext in (".pem", ".key", ".p12", ".pfx", ".keystore")):
        tags |= {"secret-file", "sensitive"}
    if ext in (".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf") and "config" not in tags and not ("lockfile" in tags):
        if name not in _MANIFESTS:
            tags.add("config")
    if "config" in name.lower() or name.startswith("."):
        tags.add("config")
    if ext in _DOC_EXT or p.startswith("docs/") or name in ("LICENSE", "CHANGELOG", "README"):
        tags.add("docs")
    if ext in _UI_EXT or re.search(r"(^|/)(components|pages|views|ui|frontend|public|static)(/|$)", low):
        tags.add("ui")
    if _SENSITIVE_WORDS.search(p) or matches_any(p, cfg.sensitive_paths):
        tags.add("sensitive")
    if ext in _CODE_EXT and not (tags & {"test", "generated"}):
        tags.add("source")
    if not tags:
        tags.add("source" if ext in _CODE_EXT else "config")
    return tags


def is_ignored(path: str, cfg: Optional[Config] = None) -> bool:
    return "generated" in classify_path(path, cfg)
