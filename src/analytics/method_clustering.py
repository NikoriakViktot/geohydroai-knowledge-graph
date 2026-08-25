import json
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.cluster import KMeans

METHOD_SYNONYMS = {
    "SVM": [
        "svm",
        "support vector machine"
    ],
    "Random Forest": [
        "random forest",
        "rf"
    ],
    "NDVI": [
        "ndvi",
        "normalized difference vegetation index"
    ],
    "NDWI": [
        "ndwi",
        "normalized difference water index"
    ],
    "CNN": [
        "cnn",
        "convolutional neural network"
    ],
    "U-Net": [
        "unet",
        "u-net"
    ],
}

model = SentenceTransformer("all-MiniLM-L6-v2")

def normalize_method(name: str):
    if not name:
        return name

    n = name.lower()

    for canonical, variants in METHOD_SYNONYMS.items():
        for v in variants:
            if v in n:
                return canonical

    return name

def normalize_method_semantic(name: str):
    if not name:
        return name

    name_norm = normalize_method(name)

    # якщо вже нормалізували — ок
    if name_norm != name:
        return name_norm

    # 🔥 embeddings fallback
    emb = model.encode([name])[0]

    best_label = None
    best_score = 0.0

    for canonical, variants in METHOD_SYNONYMS.items():
        variant_embs = model.encode(variants)
        sim = cosine_similarity([emb], variant_embs).max()

        if sim > best_score:
            best_score = sim
            best_label = canonical

    if best_score > 0.7:
        return best_label
    if best_score < 0.5:
        return "Other"

    return name

def group_methods(methods, max_k=5):
    if not methods:
        return []

    texts = [normalize_method_semantic(m["name"]) for m in methods]
    # 🔥 якщо мало методів — не кластеризуємо
    if len(texts) <= 2:
        return [{"name": t, "cluster": 0} for t in texts]

    embs = model.encode(texts)

    # 🔥 динамічний k
    k = min(max_k, len(texts))

    kmeans = KMeans(n_clusters=k, random_state=42, n_init="auto")
    labels = kmeans.fit_predict(embs)

    return [
        {
            "name": texts[i],
            "cluster": int(labels[i])
        }
        for i in range(len(texts))
    ]

def run_clustering(all_methods):
    texts = list(set(all_methods))
    embs = model.encode(texts)

    kmeans = KMeans(n_clusters=6, random_state=42)
    labels = kmeans.fit_predict(embs)

    return [
        {"method": texts[i], "cluster": int(labels[i])}
        for i in range(len(texts))
    ]