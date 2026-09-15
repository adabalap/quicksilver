#!/usr/bin/env python3
"""Inspect the Responses object so we can extract generated text correctly."""
import inspect
import litert_lm

def sig(obj, name):
    try: return f"{name}{inspect.signature(obj)}"
    except Exception as e: return f"{name}(? {e})"

print("=== Responses class ===")
R = litert_lm.Responses
print("  methods/attrs:")
for m in dir(R):
    if not m.startswith("_"):
        obj = getattr(R, m, None)
        if callable(obj): print("   ", sig(obj, f"Responses.{m}"))
        else: print(f"    Responses.{m} (attr)")
print("  docstring:", (inspect.getdoc(R) or "(none)")[:300])

print("\n=== Content / Contents / Message (in case Responses wraps them) ===")
for cn in ("Content","Contents","Message","Responses"):
    c = getattr(litert_lm, cn, None)
    if c:
        attrs = [a for a in dir(c) if not a.startswith("_")]
        print(f"  {cn}: {', '.join(attrs)}")

# Try a tiny real generation to see the actual return shape (safe: 8 tokens)
print("\n=== LIVE mini-generation (8 tokens) to see Responses shape ===")
try:
    import os
    # find model path from config
    import json
    cfgp = os.path.expanduser("~/.quicksilver/config.quicksilver.json")
    model = json.load(open(cfgp)).get("model_path") if os.path.exists(cfgp) else None
    if model and os.path.exists(model):
        try: backend = litert_lm.Backend.CPU()
        except Exception: backend = litert_lm.Backend.CPU
        eng = litert_lm.Engine(model, backend=backend, max_num_tokens=512)
        eng.__enter__()
        sess = eng.create_session(max_output_tokens=8)
        sess.run_prefill(["Say hello."])
        resp = sess.run_decode()
        print("  type(resp):", type(resp))
        print("  repr(resp)[:300]:", repr(resp)[:300])
        for attr in ("text","content","contents","responses","message","parts"):
            if hasattr(resp, attr):
                print(f"  resp.{attr} =", repr(getattr(resp, attr))[:200])
        # if iterable
        try:
            print("  list(resp)[:2]:", [repr(x)[:80] for x in list(resp)[:2]])
        except Exception as e:
            print("  (not iterable:", e, ")")
        try: sess.close()
        except Exception: pass
        eng.close()
    else:
        print("  (model path not found; skipping live test)")
except Exception as e:
    import traceback; traceback.print_exc()
print("\nDone.")
