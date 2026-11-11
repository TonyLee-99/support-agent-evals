"""Step 3: embed root-cause texts (local bge-small via fastembed) and cluster them.

Clusters are compared with the codebook labels as a *descriptive* check (the root-cause text and the label come
from the same labeller, so agreement is not independent validation). The useful output is a cluster whose
members span several codebook labels or that names a failure mode the codebook lacks.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer

from support_evals.llm.cache import ROOT

OUT = ROOT / "results" / "flywheel"
EMB_CACHE = ROOT / "cache" / "ci" / "embeddings.json"  # committed so CI re-clusters offline
MODEL = "BAAI/bge-small-en-v1.5"


def embed(texts: list[str]) -> np.ndarray:
    cache = json.loads(EMB_CACHE.read_text()) if EMB_CACHE.exists() else {}
    missing = [t for t in texts if t not in cache]
    if missing:
        from fastembed import TextEmbedding

        m = TextEmbedding(MODEL, cache_dir=str(ROOT / "data" / "models"))
        for t, v in zip(missing, m.embed(missing), strict=True):
            cache[t] = [round(float(x), 6) for x in v]
        EMB_CACHE.parent.mkdir(parents=True, exist_ok=True)
        EMB_CACHE.write_text(json.dumps(cache))
    return np.array([cache[t] for t in texts])


def purity(clusters: list[int], labels: list[str]) -> float:
    tot = 0
    for c in set(clusters):
        members = [labels[i] for i, x in enumerate(clusters) if x == c]
        tot += Counter(members).most_common(1)[0][1]
    return tot / len(labels)


def main() -> int:
    rows = [json.loads(line) for line in (OUT / "labels.jsonl").read_text().splitlines()]
    rows = [r for r in rows if r["label"].get("root_cause")]
    texts = [r["label"]["root_cause"] for r in rows]
    cats = [r["label"]["category"] for r in rows]
    X = embed(texts)
    X = X / np.linalg.norm(X, axis=1, keepdims=True)
    k = max(2, min(8, round(len(rows) ** 0.5)))
    cl = AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average").fit_predict(X).tolist()
    tf = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1).fit(texts)
    clusters: list[dict[str, Any]] = []
    for c in sorted(set(cl)):
        idx = [i for i, x in enumerate(cl) if x == c]
        m = tf.transform([texts[i] for i in idx]).mean(axis=0).A1
        terms = [tf.get_feature_names_out()[j] for j in m.argsort()[::-1][:4]]
        clusters.append({"cluster": c, "size": len(idx), "top_terms": terms,
                         "labels": dict(Counter(cats[i] for i in idx)),
                         "examples": [texts[i] for i in idx[:3]], "sources": [rows[i]["source"] for i in idx]})
    by_agent: dict[str, Counter] = {}
    for r in rows:
        by_agent.setdefault(r["agent"], Counter())[r["label"]["category"]] += 1
    out = {"n_failures": len(rows), "k": k, "embedding_model": MODEL, "purity_vs_codebook": round(purity(cl, cats), 3),
           "category_counts": dict(Counter(cats)), "by_agent_version": {a: dict(c) for a, c in by_agent.items()},
           "clusters": sorted(clusters, key=lambda x: -x["size"])}
    (OUT / "taxonomy.json").write_text(json.dumps(out, indent=1))
    print(f"{len(rows)} failures, {k} clusters, purity {out['purity_vs_codebook']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
