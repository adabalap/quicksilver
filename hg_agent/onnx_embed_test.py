#!/usr/bin/env python3
"""ONNX embedding backend (no PyTorch): load, pooling, normalisation, fallback. Uses a tiny synthetic model."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  ✗ {m}")
import onnx
from onnx import helper, TensorProto, numpy_helper
from tokenizers import Tokenizer, models, pre_tokenizers
from memos_daemon.embeddings import Embedder

d = tempfile.mkdtemp()
vocab = {"[UNK]": 0, "[PAD]": 1, "cat": 2, "dog": 3, "bank": 4, "river": 5, "money": 6}
tok = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]")); tok.pre_tokenizer = pre_tokenizers.Whitespace()
tok.save(os.path.join(d, "tokenizer.json"))
rng = np.random.RandomState(1); table = rng.randn(len(vocab), 8).astype(np.float32)
g = helper.make_graph(
    [helper.make_node("Gather", ["emb", "input_ids"], ["last_hidden_state"])], "t",
    [helper.make_tensor_value_info(n, TensorProto.INT64, [1, None]) for n in ("input_ids", "attention_mask", "token_type_ids")],
    [helper.make_tensor_value_info("last_hidden_state", TensorProto.FLOAT, [1, None, 8])],
    [numpy_helper.from_array(table, "emb")])
m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)]); m.ir_version = 8
onnx.save(m, os.path.join(d, "model.onnx"))

e = Embedder("all-MiniLM-L6-v2", backend="onnx", onnx_dir=d)
check(e.available() and e._kind == "onnx", "onnx backend loads from a local folder")
check(e.dim == 8 and e.tag == "all-MiniLM-L6-v2", "dimension and tag are set")
v = e.encode("cat dog")
exp = (table[2] + table[3]) / 2; exp /= np.linalg.norm(exp)
check(np.allclose(v, exp, atol=1e-5), "mean pooling + L2 normalisation match the reference")
check(abs(np.linalg.norm(v) - 1) < 1e-5, "vectors are unit length")
check(np.allclose(e.encode("cat dog", kind="query"), v), "queries use the same path")
check(isinstance(v, list) and all(isinstance(x, float) for x in v), "returns plain floats")
check(e.encode("") is not None, "empty text does not crash")
# unusable folder + onnx-only → clean failure, no exception
e2 = Embedder("all-MiniLM-L6-v2", backend="onnx", onnx_dir=os.path.join(d, "missing"))
check(not e2.available() and e2.encode("cat") is None, "a bad model folder fails safe (lexical fallback)")
# auto with no sentence-transformers and a bad onnx dir also fails safe
e3 = Embedder("all-MiniLM-L6-v2", backend="auto", onnx_dir=os.path.join(d, "missing"))
check(e3.encode("cat") is None or e3._kind == "st", "auto falls through without raising")
print(f"\n  {P} passed, {F} failed"); sys.exit(1 if F else 0)
