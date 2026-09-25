
import os
import re
import json
import hashlib
from typing import Dict, List

import chromadb

# Config
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_JSON_PATH = os.path.join(BASE_DIR, "package_services.json")
CHROMA_DIR = os.path.join(BASE_DIR, "vectors", "chroma_db")
COLLECTION_NAME = "medical_packages"
INDEX_META_PATH = os.path.join(BASE_DIR, "vectors", "index_meta.json")

EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
TOP_K = 3
SCORE_THRESHOLD = 0.35

os.makedirs(os.path.dirname(INDEX_META_PATH), exist_ok=True)

# Read Data
def load_data_from_json(path: str = PACKAGE_JSON_PATH) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


DATA = load_data_from_json()
PACKAGES: List[Dict] = DATA.get("packages", [])
SERVICES: List[Dict] = DATA.get("services", [])

_SVC_BY_ID: Dict[int, Dict] = {s["id"]: s for s in SERVICES if s.get("id") is not None}
_PKG_BY_ID: Dict[int, Dict] = {p["id"]: p for p in PACKAGES if p.get("id") is not None}


def convert_packages_to_str(packages: List[Dict]) -> str:
    """Danh sách gói rút gọn (ID, tên, mô tả, giá) 
            Dùng khi LLM đọc để chọn gói."""
    pkg_strs = []
    for pkg in packages:
        price = pkg.get("price")
        price_str = f"{price:,} VND" if isinstance(price, (int, float)) else "(chưa có giá)"
        pkg_strs.append(
            f"ID: {pkg.get('id', '(không có ID)')}\n"
            f"Tên gói: {pkg.get('name', '(không có tên)')}\n"
            f"Mô tả: {pkg.get('description', '(không có mô tả)')}\n"
            f"Giá: {price_str}"
        )
    return "\n\n".join(pkg_strs)


def _collect_service_ids(pkg: Dict, _seen_pkg_ids=None) -> List[int]:
    """Lấy toàn bộ service_ids của 1 gói, gộp đệ quy từ các gói được `included_package_ids` include."""
    if _seen_pkg_ids is None:
        _seen_pkg_ids = set()
    pkg_id = pkg.get("id")
    if pkg_id in _seen_pkg_ids:
        return []
    _seen_pkg_ids.add(pkg_id)

    ids = list(pkg.get("services_ids", []) or [])
    for included_id in (pkg.get("included_package_ids") or []):
        included_pkg = _PKG_BY_ID.get(included_id)
        if included_pkg:
            ids += _collect_service_ids(included_pkg, _seen_pkg_ids)

    seen, result = set(), []
    for sid in ids:
        if sid not in seen:
            seen.add(sid)
            result.append(sid)
    return result


def get_package_by_id(ids: List[int]) -> str:
    """Trả về mô tả chi tiết (text) của các gói theo ID, gồm cả dịch vụ từ (các) gói được include."""
    selected = []
    for pkg_id in ids:
        pkg = _PKG_BY_ID.get(pkg_id)
        if not pkg:
            continue

        service_ids = _collect_service_ids(pkg)
        service_names = [_SVC_BY_ID[sid]["name"] for sid in service_ids if sid in _SVC_BY_ID]

        price = pkg.get("price")
        price_str = f"{price:,} VND" if isinstance(price, (int, float)) else "(chưa có giá)"

        pkg_str = (
            f"ID: {pkg.get('id')}\n"
            f"Tên gói: {pkg.get('name')}\n"
            f"Mô tả: {pkg.get('description')}\n"
            f"Giá: {price_str}\n"
            f"Dịch vụ đi kèm:\n  - " + "\n  - ".join(service_names) + "\n"
        )
        selected.append(pkg_str)

    return "\n\n".join(selected)


# Embedding + Chroma 
_embedding_model = None  # lazy load SentenceTransformer


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        print(f"⏳ Đang tải model embedding: {EMBEDDING_MODEL_NAME} ...")
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embedding_model


class BgeM3EmbeddingFunction(chromadb.EmbeddingFunction):

    def __init__(self):
        pass 

    def __call__(self, input: List[str]) -> List[List[float]]:
        model = _get_embedding_model()
        vectors = model.encode(list(input), normalize_embeddings=True, show_progress_bar=False)
        return vectors.tolist()

    @staticmethod
    def name() -> str:
        return "bge_m3_sentence_transformers"


_chroma_client = None
_collection = None

def _get_collection():
    global _chroma_client, _collection
    if _collection is None:
        _chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
        _collection = _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=BgeM3EmbeddingFunction(),
            metadata={"hnsw:space": "cosine"},
        )
    return _collection

def clean_description(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"\(\s*id\s*:\s*\d+\s*\)\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


def build_package_text(pkg: Dict) -> str:
    """Ghép text để embed: Tên + Mô tả (đã bỏ '(id:x)') + tên dịch vụ đi kèm (gồm cả gói được include)."""
    service_ids = _collect_service_ids(pkg)
    names = [_SVC_BY_ID[sid]["name"] for sid in service_ids if sid in _SVC_BY_ID]
    parts = [str(pkg.get("name", "")).strip(), clean_description(pkg.get("description", ""))]
    if names:
        parts.append("Dịch vụ gồm: " + ", ".join(names))
    return ". ".join(p.rstrip(". ") for p in parts if p)


def _compute_data_hash() -> str:
    texts = [f"{p['id']}|{build_package_text(p)}" for p in PACKAGES if p.get("id") is not None]
    raw = EMBEDDING_MODEL_NAME + "\n" + "\n".join(texts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_or_load_index(force_rebuild: bool = False) -> None:

    collection = _get_collection()
    data_hash = _compute_data_hash()

    old_hash = None
    if os.path.exists(INDEX_META_PATH):
        with open(INDEX_META_PATH, "r", encoding="utf-8") as f:
            old_hash = json.load(f).get("data_hash")

    if not force_rebuild and old_hash == data_hash and collection.count() > 0:
        print(f"Chroma index đã khớp dữ liệu ({collection.count()} gói)")
        return

    print(f"Đang (re)build Chroma index cho {len(PACKAGES)} gói khám ...")
    existing_ids = collection.get()["ids"]
    if existing_ids:
        collection.delete(ids=existing_ids)

    ids, docs, metas = [], [], []
    for pkg in PACKAGES:
        if pkg.get("id") is None:
            continue
        ids.append(str(pkg["id"]))
        docs.append(build_package_text(pkg))
        metas.append({"package_id": pkg["id"]})

    collection.add(ids=ids, documents=docs, metadatas=metas)

    with open(INDEX_META_PATH, "w", encoding="utf-8") as f:
        json.dump({"data_hash": data_hash}, f)

    print(f"💾 Đã lưu {len(ids)} vector vào Chroma tại {CHROMA_DIR}")


def search_packages(query: str, k: int = TOP_K, threshold: float = SCORE_THRESHOLD) -> List[int]:
    collection = _get_collection()
    if collection.count() == 0:
        build_or_load_index()
        collection = _get_collection()

    n_results = min(k, max(collection.count(), 1))
    result = collection.query(query_texts=[query], n_results=n_results)
    ids = result.get("ids", [[]])[0]
    distances = result.get("distances", [[]])[0]

    matched = []
    for pkg_id_str, distance in zip(ids, distances):
        similarity = 1 - distance
        if similarity >= threshold:
            matched.append(int(pkg_id_str))

    return matched


if __name__ == "__main__":
    build_or_load_index()
    for q in ["Tôi 50 tuổi hay bị tức ngực", "Đau bụng quá", "Thời tiết hôm nay thế nào"]:
        print(f"\n❓ {q}")
        print("   -> ID gói:", search_packages(q))
