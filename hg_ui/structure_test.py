#!/usr/bin/env python3
"""The page is split into /js and /css files loaded by index.html. Guards the split:
every file is referenced exactly once and in a stable order, every script parses on its own,
the server hands out versioned URLs, and no single file balloons again."""
import sys, os, re, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_test import load_app
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  \033[31m✗ {m}\033[0m")
here = os.path.dirname(os.path.abspath(__file__)); st = os.path.join(here, "static")
html = open(os.path.join(st, "index.html"), encoding="utf-8").read()
js_refs = re.findall(r'<script src="/js/([^"?]+)\?v=__V__"></script>', html)
css_refs = re.findall(r'<link rel="stylesheet" href="/css/([^"?]+)\?v=__V__"/>', html)
js_files = sorted(f for f in os.listdir(os.path.join(st, "js")) if f.endswith(".js"))
css_files = sorted(f for f in os.listdir(os.path.join(st, "css")) if f.endswith(".css"))
check(js_refs == js_files, f"index.html loads every script, in filename order: {set(js_files) ^ set(js_refs)}")
check(sorted(css_refs) == css_files, "index.html links every stylesheet")
check(len(js_refs) == len(set(js_refs)), "no script is loaded twice")
check(len(html.splitlines()) < 700, "index.html stays a thin shell")
for f in js_files:
    n = len(open(os.path.join(st, "js", f), encoding="utf-8").read().splitlines())
    check(n <= 900, f"js/{f} is {n} lines — split it before it grows further")
    r = subprocess.run(["node", "--check", os.path.join(st, "js", f)], capture_output=True, text=True)
    check(r.returncode == 0, f"js/{f} parses: {r.stderr[:120]}")
bad = [f for f in js_files if re.search(r"/\*[^*]*$", re.sub(r"/\*[\s\S]*?\*/", "", open(os.path.join(st, "js", f), encoding="utf-8").read()))]
check(not bad, f"no unterminated comment in {bad}")
tmp = tempfile.mkdtemp(); qs = load_app(os.path.join(tmp, "s.db")); cl = qs.app.test_client()
page = cl.get("/").get_data(as_text=True)
check("__V__" not in page, "the server fills in the build token")
tok = re.search(r"/js/00-util\.js\?v=([0-9a-f]+)", page)
check(bool(tok), "script URLs carry a build token")
r = cl.get(f"/js/00-util.js?v={tok.group(1)}")
check(r.status_code == 200 and "javascript" in r.content_type and "immutable" in r.headers.get("Cache-Control", ""), "versioned scripts are cached for good")
check(cl.get("/").headers.get("Cache-Control") == "no-cache", "the page itself is never cached")
check(cl.get("/js/nope.js").get_data(as_text=True).lstrip().startswith("<!DOCTYPE"), "unknown path still falls back to the shell")

# ── server package
import ast, glob
sdir = os.path.join(here, "server")
sfiles = sorted(os.path.basename(f)[:-3] for f in glob.glob(os.path.join(sdir, "*.py")) if not f.endswith("__init__.py"))
import server as _srv
check(sorted(_srv.MODULES) == sfiles, f"server/__init__.py lists every module: {set(sfiles) ^ set(_srv.MODULES)}")
deps = {}
for m in sfiles:
    src = open(os.path.join(sdir, m + ".py"), encoding="utf-8").read()
    check(len(src.splitlines()) <= 700, f"server/{m}.py is {len(src.splitlines())} lines — split it before it grows further")
    deps[m] = {n.module for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.level == 1 and n.module}
    deps[m] |= {a.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.level == 1 and not n.module for a in n.names}
def reach(m, seen=()):
    for d in deps.get(m, ()):
        if d in seen: yield d
        else: yield from reach(d, seen + (d,))
cyc = [m for m in sfiles if m in set(reach(m, (m,)))]
check(not cyc, f"no import cycles between server modules: {cyc}")
# 95 -> 98: /api/captures, /api/captures/scan, /api/captures/forget.
# This number is a tripwire, not a fact about the design -- it is here so
# that adding a route is a deliberate act. Bump it WITH the routes.
check(len(list(qs.app.url_map.iter_rules())) == 103, f"the app serves 103 URL rules (got {len(list(qs.app.url_map.iter_rules()))})")

# API_LEVEL (Python) and API_NEED (the page) are what let the page notice it is
# newer than the running server and say "restart hg_ui" instead of letting an
# action fail with nothing to go on. They were kept in step by a comment asking
# nicely, and the first routes added after that comment shipped without the
# bump -- so the banner stayed silent and a dismiss just said "Couldn't".
_lvl = re.search(r"^API_LEVEL\s*=\s*(\d+)", open(os.path.join(here, "server", "system.py")).read(), re.M)
_need = re.search(r"const API_NEED\s*=\s*(\d+)", open(os.path.join(st, "js", "30-ask.js")).read())
check(bool(_lvl and _need), "both API_LEVEL and API_NEED are declared")
if _lvl and _need:
    check(_lvl.group(1) == _need.group(1),
          f"the page's API_NEED matches the server's API_LEVEL ({_need.group(1)} == {_lvl.group(1)})")

t = P + F
print(f"\n  {'\033[32m' if not F else '\033[31m'}{P}/{t} passed{'  all green ✨' if not F else '  FAILED'}\033[0m")
sys.exit(1 if F else 0)
