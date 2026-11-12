"""RAG 파이프라인: 마크다운 '## 섹션' 청킹 → Chroma(벡터) + BM25(kiwipiepy 형태소) 하이브리드 검색.

코퍼스는 data/docs/{technique,composers,works,history}/*.md 이고, 각 파일은 frontmatter
(doc_id, title, category, source, license)를 가진다. 인덱스는 두 개로 나눈다.
- technique : 연습법·테크닉
- knowledge : 작곡가·작품·음악사 (composers + works + history)
청크 doc_id 형식: "{frontmatter doc_id}#{섹션 제목}"
"""
import hashlib
import re
import threading
from functools import lru_cache

import chromadb
from kiwipiepy import Kiwi
from langchain_chroma import Chroma
from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter

from src.config import CHROMA_DIR, DOCS_DIR, embeddings

INDEXES = {"technique": ["technique"], "knowledge": ["composers", "works", "history"]}

_kiwi = Kiwi()
# 의미 있는 품사만 남긴다 (명사, 동사·형용사 어간, 외국어, 숫자, 한자, 어근)
_KEEP_TAGS = ("NN", "VV", "VA", "SL", "SN", "XR", "SH")
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def kiwi_tokenize(text: str) -> list[str]:
    return [t.form.lower() for t in _kiwi.tokenize(text) if t.tag.startswith(_KEEP_TAGS)]


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    m = _FRONTMATTER.match(raw)
    if not m:
        return {}, raw
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip()
    return meta, raw[m.end():]


def load_chunks(index: str) -> list[Document]:
    """인덱스에 속한 md 파일을 '## 섹션' 단위로 자른다. 청크 앞에 문서 제목·섹션을 붙여 문맥을 보존한다."""
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "title"), ("##", "section")], strip_headers=True
    )
    chunks = []
    for folder in INDEXES[index]:
        for path in sorted((DOCS_DIR / folder).glob("*.md")):
            meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            doc_id = meta.get("doc_id", path.stem)
            for part in splitter.split_text(body):
                if not part.page_content.strip():
                    continue
                title = part.metadata.get("title") or meta.get("title", path.stem)
                section = part.metadata.get("section", "개요")
                chunks.append(
                    Document(
                        page_content=f"[{title}] {section}\n{part.page_content}",
                        metadata={
                            "doc_id": f"{doc_id}#{section}",
                            "title": title,
                            "category": meta.get("category", folder),
                            "source": meta.get("source", ""),
                            "license": meta.get("license", ""),
                        },
                    )
                )
    return chunks


def _content_hash(docs: list[Document]) -> str:
    h = hashlib.md5()
    for d in docs:
        h.update(d.page_content.encode("utf-8"))
    return h.hexdigest()[:10]


_lock = threading.Lock()


@lru_cache(maxsize=1)
def _chroma_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def get_retriever(index: str, k: int = 4) -> EnsembleRetriever:
    # ToolNode 가 도구를 병렬 실행하므로 최초 생성은 락으로 직렬화한다 (Chroma 클라이언트 동시 생성 경합 방지).
    with _lock:
        return _build_retriever(index, k)


@lru_cache(maxsize=None)
def _build_retriever(index: str, k: int) -> EnsembleRetriever:
    docs = load_chunks(index)
    if not docs:
        raise RuntimeError(f"'{index}' 인덱스에 문서가 없습니다: {DOCS_DIR}")
    # 문서 내용이 바뀌면 컬렉션 이름이 바뀌어 자동으로 재색인된다.
    store = Chroma(
        client=_chroma_client(),
        collection_name=f"{index}_{_content_hash(docs)}",
        embedding_function=embeddings,
    )
    if not store.get(limit=1)["ids"]:
        store.add_documents(docs, ids=[f"{index}-{i}" for i in range(len(docs))])

    vector = store.as_retriever(search_kwargs={"k": k})
    bm25 = BM25Retriever.from_documents(docs, preprocess_func=kiwi_tokenize, k=k)
    return EnsembleRetriever(retrievers=[bm25, vector], weights=[0.4, 0.6])


def rerank(question: str, items: list[dict]) -> list[dict]:
    """모은 근거를 질문과의 임베딩 코사인 유사도 순으로 다시 정렬한다 (리랭킹).

    도구는 호출 순서대로 근거를 쌓기 때문에, planner 의 보조 검색(prefetch) 결과가 항상 맨 앞에 온다.
    finalize 와 API contexts 가 질문에 가장 관련된 근거부터 보도록 순서를 바로잡는다.
    """
    if len(items) <= 1:
        # 근거가 0~1건이면 정렬할 필요가 없다. 호출부가 rerank_score 를 읽으므로 값은 채워 둔다.
        return [{**it, "rerank_score": 1.0} for it in items]
    import numpy as np

    vecs = np.array(embeddings.embed_documents([it["text"] for it in items]))
    q = np.array(embeddings.embed_query(question))
    sims = (vecs @ q) / (np.linalg.norm(vecs, axis=1) * np.linalg.norm(q) + 1e-10)
    order = np.argsort(-sims)
    return [{**items[i], "rerank_score": round(float(sims[i]), 4)} for i in order]


def retrieve(query: str, index: str, k: int = 4) -> list[dict]:
    """질의에 대한 상위 k개 청크를 {doc_id, text, source, title} 형태로 반환한다."""
    docs = get_retriever(index, k).invoke(query)[:k]
    return [
        {
            "doc_id": d.metadata["doc_id"],
            "text": d.page_content,
            "source": d.metadata.get("source", ""),
            "title": d.metadata.get("title", ""),
        }
        for d in docs
    ]


if __name__ == "__main__":
    for q, idx in [("엄지 넘기기 할 때 소리가 끊겨요", "technique"), ("월광 소나타 이름 유래", "knowledge")]:
        print(f"\n== [{idx}] {q}")
        for c in retrieve(q, idx):
            print(" -", c["doc_id"], "|", c["source"])
