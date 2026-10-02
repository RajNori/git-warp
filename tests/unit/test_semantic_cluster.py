import time

from gitwarp.core.config import Config
from gitwarp.semantic.cluster import cluster_paths


def E(path, a=1, d=0, s="M"):
    return {"path": path, "added": a, "deleted": d, "status": s}


def by_path(clusters):
    return {p: c for c in clusters for p in c.paths}


def test_empty_and_single():
    assert cluster_paths([], Config()) == []
    cs = cluster_paths([E("src/billing/pay.py", 3, 1)], Config())
    assert len(cs) == 1 and cs[0].kind == "source" and cs[0].added == 3 and cs[0].deleted == 1 and cs[0].reasons


def test_tests_paired_with_source():
    cs = cluster_paths([E("src/billing/pay.py"), E("tests/test_pay.py"), E("web/app.test.ts"), E("web/app.ts"),
                        E("src/billing/__tests__/pay.spec.ts")], Config())
    m = by_path(cs)
    assert m["tests/test_pay.py"] is m["src/billing/pay.py"]
    assert m["web/app.test.ts"] is m["web/app.ts"]
    assert any("paired" in r for r in m["tests/test_pay.py"].reasons)


def test_unpaired_tests_group_separately():
    cs = cluster_paths([E("tests/test_other.py"), E("src/a/x.py")], Config())
    assert by_path(cs)["tests/test_other.py"].kind == "test"


def test_migration_and_schema_together():
    cs = cluster_paths([E("db/migrations/0001_add.sql"), E("prisma/schema.prisma"), E("src/a/x.py")], Config())
    m = by_path(cs)
    assert m["db/migrations/0001_add.sql"] is m["prisma/schema.prisma"] and m["prisma/schema.prisma"].kind == "migration"


def test_lockfile_with_manifest():
    cs = cluster_paths([E("package.json"), E("package-lock.json", 500, 400), E("web/package.json"), E("web/yarn.lock")], Config())
    m = by_path(cs)
    assert m["package.json"] is m["package-lock.json"] and m["package.json"].kind == "deps"
    assert m["web/package.json"] is m["web/yarn.lock"] and m["web/package.json"] is not m["package.json"]


def test_generated_isolated_and_secret_isolated():
    cs = cluster_paths([E("dist/bundle.js"), E("src/a/x.py"), E("a.min.js"), E(".env")], Config())
    m = by_path(cs)
    assert m["dist/bundle.js"] is m["a.min.js"] and m["dist/bundle.js"].label == "generated/ignored" and m["dist/bundle.js"].kind == "generated"
    assert m[".env"] is not m["src/a/x.py"]


def test_docs_infra_ci_ui():
    cs = cluster_paths([E("README.md"), E("docs/a.md"), E(".github/workflows/ci.yml"), E("Dockerfile"), E("src/components/Button.tsx")], Config())
    m = by_path(cs)
    assert m["README.md"] is m["docs/a.md"] and m["README.md"].kind == "docs"
    assert m[".github/workflows/ci.yml"].label == "ci" and m["Dockerfile"].label == "infra"
    assert m["src/components/Button.tsx"].kind == "ui"


def test_generic_dirs_skipped_in_modules():
    cs = cluster_paths([E("src/lib/billing/a.py"), E("src/billing/b.py")], Config())
    assert len(cs) == 1 and cs[0].label == "billing"


def test_stable_ordering_and_ids():
    es = [E("a/x.py"), E("a/y.py"), E("b/z.py"), E("README.md")]
    one = cluster_paths(es, Config())
    two = cluster_paths(list(reversed(es)), Config())
    assert [c.to_dict() for c in one] == [c.to_dict() for c in two]
    assert [c.id for c in one] == list(range(1, len(one) + 1))
    assert len(one[0].paths) >= len(one[-1].paths)


def test_weird_paths_and_renames_and_deletions():
    es = [E("we ird/sp ace/ü-file.py"), E("./x/y.py"), E("a\\b\\c.py"), E("only/deleted.py", 0, 9, "D"), E("new/name.py", 0, 0, "R"), E("noext")]
    cs = cluster_paths(es, Config())
    assert sum(len(c.paths) for c in cs) == 6


def test_performance_1000_files():
    es = [E(f"pkg{i % 40}/mod{i % 25}/file{i}.py") for i in range(1000)] + [E(f"tests/test_file{i}.py") for i in range(0, 200)]
    t = time.time()
    cs = cluster_paths(es, Config())
    assert time.time() - t < 2
    assert sum(len(c.paths) for c in cs) == 1200


def test_import_affinity_merges(make_repo):
    r = make_repo()
    r.write("pkga/a.py", "from pkgb.b import f\n").write("pkgb/b.py", "def f(): pass\n").write("pkgc/c.py", "x=1\n")
    r.commit("init")
    es = [E("pkga/a.py"), E("pkgb/b.py"), E("pkgc/c.py")]
    plain = by_path(cluster_paths(es, Config()))
    assert plain["pkga/a.py"] is not plain["pkgb/b.py"]
    merged = by_path(cluster_paths(es, Config(), repo=r.path))
    assert merged["pkga/a.py"] is merged["pkgb/b.py"] and merged["pkgc/c.py"] is not merged["pkga/a.py"]
    assert any("import affinity" in x for x in merged["pkga/a.py"].reasons)
