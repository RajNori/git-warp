import json
import subprocess
import sys
from pathlib import Path

import pytest

from gitwarp.semantic import blast
from gitwarp.semantic.adapters import JsAdapter, PythonAdapter
from gitwarp.semantic.adapters import generic

ROOT = Path(__file__).resolve().parent.parent.parent
WARP = str(ROOT / "scripts" / "warp.py")


def warp(*args):
    p = subprocess.run([sys.executable, WARP, *args], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout)


@pytest.fixture
def js_repo(make_repo):
    r = make_repo()
    r.commit("init", {
        "tsconfig.json": '{\n // comment\n "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]},},\n}\n',
        "src/payments/payment-service.ts": "export function charge(a: number) { return a }\nexport const FEE = 1\n",
        "src/payments/index.ts": "export * from './payment-service'\n",
        "src/api/routes/checkout.ts": "import { charge } from '../../payments'\nrouter.post('/checkout', (req, res) => charge(1))\n",
        "src/webhooks/stripe.ts": "import { charge } from '@/payments/payment-service'\nexport function webhookHandler() { charge(2) }\n",
        "src/ui/Cart.tsx": "const pay = () => import('../payments/payment-service')\nconst x = require('../payments')\n",
        "src/other/lonely.ts": "export const L = 1\n",
        "tests/payment-service.test.ts": "import { charge } from '../src/payments/payment-service'\n",
        "package.json": '{"name":"x"}\n', "package-lock.json": "{}\n",
        ".github/workflows/ci.yml": "run: node src/payments/payment-service.ts\n",
    })
    return r


def test_js_blast_graph(js_repo):
    r = js_repo
    r.write("src/payments/payment-service.ts", "export function charge(a: number) { return a + 1 }\nexport const FEE = 2\n")
    res = blast.analyze(r.path)
    nodes = {n["path"]: n for n in res["nodes"]}
    assert nodes["src/payments/index.ts"]["depth"] == 1 and "imports ./payment-service" in nodes["src/payments/index.ts"]["reason"]
    assert nodes["src/api/routes/checkout.ts"]["depth"] == 2 and nodes["src/api/routes/checkout.ts"]["kind"] == "interface"
    assert nodes["src/webhooks/stripe.ts"]["depth"] == 1  # via tsconfig path alias
    assert nodes["src/ui/Cart.tsx"]["depth"] in (1, 2)    # dynamic import / require
    assert nodes["tests/payment-service.test.ts"]["kind"] == "test"
    assert nodes[".github/workflows/ci.yml"]["kind"] == "infra"
    assert "src/other/lonely.ts" not in nodes
    ch = res["changed"][0]
    assert "charge" in ch["exports"] and ch["tests"] == ["tests/payment-service.test.ts"]
    assert res["level"] in ("LOW", "MEDIUM", "HIGH") and res["level_rules"]
    assert not any("no tests" in d for d in res["risk_drivers"])


def test_js_blast_drivers_high(js_repo):
    r = js_repo
    r.write("src/webhooks/stripe.ts", "export function webhookHandler() { return 2 }\n")
    r.write("src/other/lonely.ts", "export const L = 2\n")
    r.write("package-lock.json", '{"a":1}\n')
    r.write("db/migrations/002.sql", "alter table x;\n")
    res = blast.analyze(r.path)
    text = " | ".join(res["risk_drivers"])
    assert "webhook handler touched" in text and "schema/migration touched" in text and "lockfile changed" in text and "no tests found" in text
    assert res["level"] == "HIGH"


def test_blast_low_for_doc_only(js_repo):
    js_repo.write("NOTES.md", "hello\n")
    res = blast.analyze(js_repo.path)
    assert res["level"] == "LOW" and res["risk_drivers"] == []


def test_blast_explicit_paths_depth_and_truncation(js_repo):
    res = blast.analyze(js_repo.path, ["src/payments/payment-service.ts"], max_depth=1)
    assert all(n["depth"] <= 1 for n in res["nodes"])
    assert res["truncated"]["depth"] is True
    res = blast.analyze(js_repo.path, ["src/payments/payment-service.ts"], max_nodes=2)
    assert res["truncated"]["nodes"] is True and len([n for n in res["nodes"]]) <= 2


def test_blast_base_ref_and_bad_ref(js_repo):
    r = js_repo
    r.branch("feat", checkout=True)
    r.commit("change", {"src/payments/payment-service.ts": "export const FEE = 3\n"})
    res = blast.analyze(r.path, base="main")
    assert res["mode"] == "base:main" and res["changed"][0]["path"] == "src/payments/payment-service.ts"
    assert "error" in blast.analyze(r.path, base="nope")
    assert "error" in blast.analyze(r.path, base="--evil")


def test_deleted_file_still_imported(js_repo):
    js_repo.git("rm", "-q", "src/payments/payment-service.ts")
    res = blast.analyze(js_repo.path)
    assert any("deleted file still imported" in d for d in res["risk_drivers"])


@pytest.fixture
def py_repo(make_repo):
    r = make_repo()
    r.commit("init", {
        "pkg/__init__.py": "from .core import service\n",
        "pkg/core/__init__.py": "",
        "pkg/core/models.py": "class Order:\n    pass\n",
        "pkg/core/service.py": "from .models import Order\nfrom . import models\n",
        "pkg/api.py": "from pkg.core.service import run\nimport pkg.core.models\n",
        "pkg/util/helpers.py": "from ..core.models import Order\n",
        "tests/test_service.py": "from pkg.core import service\n",
    })
    return r


def test_python_blast_relative_imports(py_repo):
    py_repo.write("pkg/core/models.py", "class Order:\n    x = 1\n")
    res = blast.analyze(py_repo.path)
    nodes = {n["path"]: n for n in res["nodes"]}
    assert nodes["pkg/core/service.py"]["depth"] == 1 and "imports" in nodes["pkg/core/service.py"]["reason"]
    assert nodes["pkg/util/helpers.py"]["depth"] == 1       # ..core.models
    assert nodes["pkg/api.py"]["depth"] == 1               # absolute import
    assert nodes["tests/test_service.py"]["kind"] == "test"  # transitive via service
    assert res["stats"]["import_edges"] >= 5


def test_adapters_unit():
    js = JsAdapter()
    specs = js.imports("a.ts", "import a from './a'\nexport {b} from \"../b\"\nconst c = require('c')\nimport('./d')\nimport './side'\n")
    assert specs == ["./a", "../b", "./side", "c", "./d"] or set(specs) == {"./a", "../b", "./side", "c", "./d"}
    files = {"src/a.ts", "src/dir/index.tsx", "src/x.ts"}
    assert js.resolve("./a", "src/main.ts", files) == "src/a.ts"
    assert js.resolve("./dir", "src/main.ts", files) == "src/dir/index.tsx"
    assert js.resolve("./x.js", "src/main.ts", files) == "src/x.ts"
    assert js.resolve("react", "src/main.ts", files) is None
    py = PythonAdapter()
    assert set(py.imports("p/a.py", "import os.path\nfrom . import b\nfrom ..c import d\n")) >= {"os.path", ".", ".b", "..c", "..c.d"}
    pf = {"p/__init__.py", "p/b.py", "c/__init__.py"}
    assert py.resolve(".b", "p/a.py", pf) == "p/b.py"
    assert py.resolve("p", "x.py", pf) == "p/__init__.py"
    assert py.resolve("os", "p/a.py", pf) is None


def test_generic_fallback_without_grep(make_repo, monkeypatch):
    r = make_repo()
    r.commit("init", {"billing/ledger_service.go": "package billing\nfunc Post() {}\n", "cmd/main.go": "import ledger_service\n", "README.md": "x\n"})
    r.write("billing/ledger_service.go", "package billing\nfunc Post() { }\n")
    res = blast.analyze(r.path)
    assert any(n["path"] == "cmd/main.go" and n["confidence"] == "text-match" for n in res["nodes"])

    def boom(*a, **k):
        raise RuntimeError("git grep unavailable")
    monkeypatch.setattr(generic, "_git_grep", boom)
    monkeypatch.setenv("PATH", "/nonexistent-dir-for-rg")  # no rg, no git: only the in-process scan can work
    hits, method = generic.search_token(r.path, "ledger_service", ["cmd/main.go", "billing/ledger_service.go"], exclude={"billing/ledger_service.go"})
    assert hits == ["cmd/main.go"] and method == "python-scan"


def test_blast_skips_big_and_vendor(make_repo):
    r = make_repo()
    r.commit("init", {"a.py": "x=1\n", "node_modules/p/i.js": "require('../../a')\n", "big.py": "import a\n" + "#" * 1_100_000})
    r.write("a.py", "x=2\n")
    res = blast.analyze(r.path)
    assert res["nodes"] == []


def test_blast_cli_variants(js_repo, tmp_path):
    rc, out = warp("blast", "--repo", str(js_repo.path), "src/payments/payment-service.ts")
    assert rc == 0 and out["tree"][0]["path"] == "src/payments/payment-service.ts" and out["tree"][0]["children"]
    rc, out = warp("blast", "--repo", str(tmp_path))
    assert rc != 0 and "error" in out
    rc, out = warp("blast", "--repo", str(js_repo.path))
    assert rc == 0 and out["message"]
    rc, out = warp("blast", "--depth", "x", "--repo", str(js_repo.path))
    assert rc != 0 and "error" in out


def test_blast_unborn(make_repo):
    r = make_repo()
    r.write("a.py", "x=1\n")
    res = blast.analyze(r.path)
    assert any("no commits" in w for w in res["warnings"]) and res["changed"][0]["path"] == "a.py"
