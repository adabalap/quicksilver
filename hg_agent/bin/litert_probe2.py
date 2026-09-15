#!/usr/bin/env python3
"""Focused probe: how to generate via Session (which has max_output_tokens),
and confirm the max_num_tokens semantics on Engine."""
import inspect, sys
import litert_lm

def sig(obj, name):
    try: return f"{name}{inspect.signature(obj)}"
    except Exception as e: return f"{name}(? {e})"

print("=== Session methods (how do we generate + read output?) ===")
for m in dir(litert_lm.Session):
    if not m.startswith("_"):
        obj = getattr(litert_lm.Session, m, None)
        if callable(obj):
            print(" ", sig(obj, f"Session.{m}"))

print("\n=== AbstractSession methods ===")
for m in dir(litert_lm.AbstractSession):
    if not m.startswith("_"):
        obj = getattr(litert_lm.AbstractSession, m, None)
        if callable(obj):
            print(" ", sig(obj, f"AbstractSession.{m}"))

print("\n=== Session docstring ===")
print(inspect.getdoc(litert_lm.Session) or "(none)")

print("\n=== create_session docstring ===")
print(inspect.getdoc(litert_lm.Engine.create_session) or "(none)")

print("\n=== Engine.__init__ max_num_tokens docstring/hint ===")
print(inspect.getdoc(litert_lm.Engine.__init__) or "(none)")

# Look at a generate/run/predict method on Session
print("\n=== Session generate-like methods detail ===")
for m in ("generate","run","predict","send_message","prefill","decode","get_response","respond","step"):
    if hasattr(litert_lm.Session, m):
        print(" ", sig(getattr(litert_lm.Session, m), f"Session.{m}"))
        d = inspect.getdoc(getattr(litert_lm.Session, m))
        if d: print("      doc:", d[:200])

print("\nDone.")
