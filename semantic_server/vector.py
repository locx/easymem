"""Vector retrieval: model loading, encoding, int8 quantization."""
from __future__ import annotations

import os
import sys
from typing import Optional

import numpy as np

from .config import EMBED_MODEL, EMBED_DIM

_model = None
_NORM_EPS = 1e-8
# RRF fuses results without an absolute score; floor candidates whose
# cosine is too low to be meaningful — they otherwise displace real hits.
# int8 quantization on 256-dim embeddings has ~1/127 per-dim noise; a
# tight floor (0.15) drops legitimate semantically-related hits.
VECTOR_MIN_SIM = 0.05

_NAME_DTYPE = "U256"
_MODEL_DTYPE = "U128"


def get_model():
    """Lazy-load model2vec StaticModel (one-shot per process)."""
    global _model
    if _model is None:
        from model2vec import StaticModel
        model_name = os.environ.get("EMBED_MODEL", EMBED_MODEL)
        _model = StaticModel.from_pretrained(model_name)
    return _model


def l2_quantize_int8(arr: np.ndarray) -> np.ndarray:
    """L2-normalize rows then scale to int8; zero-norm rows -> zeros."""
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr[None, :]
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    safe = np.where(norms < _NORM_EPS, 1.0, norms)
    normed = arr / safe
    normed[(norms < _NORM_EPS).flatten()] = 0.0
    return np.clip(
        np.round(normed * 127.0), -127, 127,
    ).astype(np.int8)


def embed_entities(entities: dict) -> tuple[list[str], np.ndarray]:
    """Build embed text per entity; return (names, int8 matrix).

    Skips entities whose embed text produces near-zero norm.
    """
    model = get_model()
    names: list[str] = []
    texts: list[str] = []
    for name, info in entities.items():
        etype = info.get("entityType", "")
        obs = info.get("observations") or []
        obs_str = " | ".join(str(o) for o in obs[:5])
        prefix = f"{etype}: " if etype else ""
        # why: budget the header separately so long symbol names don't eat the
        # full 512-char window and leave zero room for observations.
        header = f"{prefix}{name}"[:256]
        remaining = max(0, 512 - len(header) - 1)
        text = f"{header}\n{obs_str[:remaining]}"
        names.append(name)
        texts.append(text)
    if not texts:
        return [], np.zeros((0, EMBED_DIM), dtype=np.int8)
    vecs = np.asarray(model.encode(texts), dtype=np.float32)
    vecs = vecs[:, :EMBED_DIM]
    norms = np.linalg.norm(vecs, axis=1)
    keep = norms >= _NORM_EPS
    dropped = int((~keep).sum())
    if dropped:
        sys.stderr.write(
            f"[vector] skipped {dropped} entities with "
            f"near-zero embed norm\n"
        )
    names = [n for n, k in zip(names, keep) if k]
    vecs = vecs[keep]
    return names, l2_quantize_int8(vecs)


def save_index(
    path: str,
    names: list[str],
    vecs: np.ndarray,
    model_id: str,
) -> None:
    """Atomic write of vec_index.npz (temp + os.replace).

    Fixed-width string dtypes so np.load runs with allow_pickle=False.
    """
    # np.savez_compressed auto-appends ".npz"; keep tmp suffix explicit
    # so os.replace finds the actual on-disk filename.
    tmp = path + ".tmp.npz"
    np.savez_compressed(
        tmp,
        vecs=vecs.astype(np.int8),
        names=np.array(names, dtype=_NAME_DTYPE),
        model=np.array(model_id, dtype=_MODEL_DTYPE),
        dim=np.int32(EMBED_DIM),
    )
    os.replace(tmp, path)


def load_index(memory_dir: str) -> Optional[dict]:
    """Load vec_index.npz; return None if missing/corrupt."""
    path = os.path.join(memory_dir, "vec_index.npz")
    if not os.path.exists(path):
        return None
    try:
        z = np.load(path)
        return {
            "vecs": z["vecs"],
            "names": [str(n) for n in z["names"]],
            "model": str(z["model"]),
            "dim": int(z["dim"]),
        }
    except (OSError, ValueError, KeyError):
        return None


def vector_search(
    memory_dir: str, query: str, top_k: int = 20,
) -> list[tuple[str, float]]:
    """Return [(name, score)] ranked by cosine similarity."""
    idx = load_index(memory_dir)
    if idx is None or len(idx["names"]) == 0:
        return []
    try:
        model = get_model()
    except Exception as exc:
        sys.stderr.write(
            f"[vector] model load failed: {exc} - "
            f"falling back to lexical\n"
        )
        return []
    q_vec = np.asarray(
        model.encode([query]), dtype=np.float32,
    )[:, :EMBED_DIM]
    q_norm = np.linalg.norm(q_vec)
    if q_norm < _NORM_EPS:
        return []
    # Mirror l2_quantize_int8 (round + clip) so query and indexed vectors
    # share the same quantization regime; .astype alone biases negatives.
    q_int8 = np.clip(
        np.round(q_vec / q_norm * 127.0), -127, 127,
    ).astype(np.int8)
    scores = (
        idx["vecs"].astype(np.int32)
        @ q_int8[0].astype(np.int32)
    ) / (127.0 * 127.0)
    top = np.argsort(-scores)[:top_k]
    # Floor: near-zero / negative similarities are noise; allowing them
    # into RRF fusion lets vector misfires outrank legitimate hits.
    return [
        (idx["names"][i], float(scores[i])) for i in top
        if scores[i] > VECTOR_MIN_SIM
    ]


def _index_metadata_path(memory_dir: str) -> str:
    return os.path.join(memory_dir, ".vec_index.meta")


def rebuild_if_stale(
    memory_dir: str, entities: dict, graph_mtime: float,
) -> bool:
    """Rebuild vec_index.npz if stale or missing. Returns True if rebuilt."""
    path = os.path.join(memory_dir, "vec_index.npz")
    meta_path = _index_metadata_path(memory_dir)
    model_id = os.environ.get("EMBED_MODEL", EMBED_MODEL)

    if os.path.exists(path) and os.path.exists(meta_path):
        try:
            with open(meta_path) as _mf:
                prev = _mf.read().strip().split("|")
            prev_model = prev[0]
            prev_count = int(prev[1])
            prev_mtime = float(prev[2])
            if (
                prev_model == model_id
                and prev_count == len(entities)
                and abs(prev_mtime - graph_mtime) < 1e-6
            ):
                return False
        except (OSError, ValueError, IndexError):
            pass

    names, vecs = embed_entities(entities)
    save_index(path, names, vecs, model_id)

    tmp = meta_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"{model_id}|{len(entities)}|{graph_mtime}")
    os.replace(tmp, meta_path)
    return True
