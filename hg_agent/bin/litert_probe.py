#!/usr/bin/env python3
"""Probe the installed litert_lm API to find where max output tokens is set.
Run on-device:  python3 bin/litert_probe.py
Paste the FULL output back so we can wire the token limit to the exact param."""
import inspect, sys
try:
    import litert_lm
except ImportError:
    print("litert_lm not installed"); sys.exit(1)

def sig(obj, name):
    try: return f"{name}{inspect.signature(obj)}"
    except Exception as e: return f"{name}(? {e})"

print("=== litert_lm version ===")
print(" ", getattr(litert_lm, "__version__", "unknown"))
print("  file:", getattr(litert_lm, "__file__", "?"))

print("\n=== ALL top-level names ===")
print(" ", ", ".join(n for n in dir(litert_lm) if not n.startswith("_")))

print("\n=== Engine.__init__ (token limit may be set at model load) ===")
print(" ", sig(litert_lm.Engine.__init__, "Engine.__init__"))

print("\n=== Engine methods ===")
for m in dir(litert_lm.Engine):
    if not m.startswith("_"):
        obj = getattr(litert_lm.Engine, m, None)
        if callable(obj):
            print(" ", sig(obj, f"Engine.{m}"))

# Conversation / session classes
print("\n=== classes with send_message / generate / run ===")
for n in dir(litert_lm):
    obj = getattr(litert_lm, n)
    if inspect.isclass(obj):
        for m in ("send_message","generate","run","complete","predict","decode"):
            if hasattr(obj, m):
                print(" ", sig(getattr(obj, m), f"{n}.{m}"))

# Any config/param/sampler/backend classes and their constructor fields
print("\n=== *Config / *Params / *Sampler / Backend classes ===")
for n in dir(litert_lm):
    if any(k in n for k in ("Config","Params","Sampler","Backend","Option","Setting")):
        obj = getattr(litert_lm, n)
        if inspect.isclass(obj):
            print(" ", sig(obj.__init__, n))
            # list attributes/fields too
            flds = [a for a in dir(obj) if not a.startswith("_") and not callable(getattr(obj,a,None))]
            if flds: print("      fields:", ", ".join(flds))

# create_conversation signature specifically
print("\n=== create_conversation signature ===")
try: print(" ", sig(litert_lm.Engine.create_conversation, "create_conversation"))
except Exception as e: print("  n/a:", e)

print("\n=== docstrings mentioning tokens/length/decode ===")
for n in dir(litert_lm):
    obj = getattr(litert_lm, n)
    doc = (inspect.getdoc(obj) or "")
    for kw in ("max_token","max_output","max_decode","max_new","output_token","decode_step","max_length"):
        if kw in doc:
            print(f"  {n}: ...{doc[max(0,doc.find(kw)-30):doc.find(kw)+50]}...")
            break

print("\nDone. Paste everything above.")
