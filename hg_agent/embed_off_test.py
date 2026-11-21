#!/usr/bin/env python3
"""embeddings_enabled=false: nothing is loaded, and the daemon says so."""
import os, sys, tempfile, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  ✗ {m}")
HERE = os.path.dirname(os.path.abspath(__file__))
import importlib.util, sqlite3
PWA = os.path.abspath(os.path.join(HERE, "..", "hg_ui", "app.py"))
tmp = tempfile.mkdtemp(); dbp = os.path.join(tmp, "n.db"); os.environ["QS_DB"] = dbp
spec = importlib.util.spec_from_file_location("pwa_app", PWA); pwa = importlib.util.module_from_spec(spec)
sys.modules["pwa_app"] = pwa; spec.loader.exec_module(pwa); pwa.init_db()
from memos_daemon.config import DEFAULT_CONFIG
from memos_daemon.daemon import MemosDaemon
def mk(**kw):
    c = dict(DEFAULT_CONFIG)
    c.update(db_path=dbp, backend="quicksilver", state_file=os.path.join(tmp, "s.json"), pending_writes_file=os.path.join(tmp, "p"),
             todo_file=os.path.join(tmp, "t.json"), thermal_enabled=False, engine="auto", model_path="", calendar_enabled=False,
             dashboard_enabled=False, connections_enabled=False, todo_enabled=False); c.update(kw)
    logging.disable(logging.CRITICAL)
    try: return MemosDaemon(c)
    finally: logging.disable(logging.NOTSET)
d = mk(embed_model="all-MiniLM-L6-v2", embeddings_enabled=False)
check(d.embedder.model_name == "" and not d.embedder.available(), "disabled: no model is configured or loaded")
check(d.embedder.encode("hello") is None, "disabled: encode returns nothing instead of loading a model")
d2 = mk(embed_model="all-MiniLM-L6-v2", embeddings_enabled=True, embed_backend="onnx", embed_onnx_dir=os.path.join(tmp, "none"))
check(d2.embedder.model_name == "all-MiniLM-L6-v2", "enabled: the configured model is used")
print(f"\n  {P} passed, {F} failed"); sys.exit(1 if F else 0)
