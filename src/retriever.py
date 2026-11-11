"""RAG 파이프라인: 마크다운 섹션 청킹 → Chroma(벡터) + BM25(kiwipiepy 형태소) 하이브리드 검색.

- 런북(runbooks)과 포스트모템(postmortems)을 각각 별도 인덱스로 구축한다.
- doc_id 형식: "RB-001_db_connection_pool#조치"
"""
import hashlib
from functools import lru_cache

from kiwipiepy import Kiwi
from langchain_chroma import Chroma
from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter

from src.config import CHROMA_DIR, DATA_DIR, embeddings

SOURCES = {"runbook": "runbooks", "postmortem": "postmortems"}
_kiwi = Kiwi()

# 형태소 중 의미 있는 품사만 남긴다 (명사, 동사·형용사 어간, 외국어, 숫자, 영문)
_KEEP_TAGS = ("NN", "VV", "VA", "SL", "SN", "XR", "SH")


def kiwi_tokenize(text: str) -> list[str]:
    return [t.form.lower() for t in _kiwi.tokenize(text) if t.tag.startswith(_KEEP_TAGS)]


def load_chunks(source: str) -> list[Document]:
    """data/{runbooks|postmortems}/*.md 를 '## 섹션' 단위로 자른다. 각 청크 앞에 문서 제목을 붙여 문맥을 보존한다."""
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "title"), ("##", "section")], strip_headers=True
    )
    chunks = []
    for path in sorted((DATA_DIR / SOURCES[source]).glob("*.md")):
        for part in splitter.split_text(path.read_text(encoding="utf-8")):
            title = part.metadata.get("title", path.stem)
            section = part.metadata.get("section", "개요")
            chunks.append(
                Document(
                    page_content=f"[{title}] {section}\n{part.page_content}",
                    metadata={"doc_id": f"{path.stem}#{section}", "source": source, "title": title},
                )
            )
    return chunks


def _content_hash(docs: list[Document]) -> str:
    h = hashlib.md5()
    for d in docs:
        h.update(d.page_content.encode("utf-8"))
    return h.hexdigest()[:10]


@lru_cache(maxsize=None)
def get_retriever(source: str, k: int = 4) -> EnsembleRetriever:
    docs = load_chunks(source)
    # 문서 내용이 바뀌면 컬렉션 이름이 바뀌어 자동 재색인된다.
    collection = f"{source}_{_content_hash(docs)}"
    store = Chroma(
        collection_name=collection,
        embedding_function=embeddings,
        persist_directory=str(CHROMA_DIR),
    )
    if not store.get(limit=1)["ids"]:
        store.add_documents(docs, ids=[f"{source}-{i}" for i in range(len(docs))])

    vector = store.as_retriever(search_kwargs={"k": k})
    bm25 = BM25Retriever.from_documents(docs, preprocess_func=kiwi_tokenize, k=k)
    return EnsembleRetriever(retrievers=[bm25, vector], weights=[0.4, 0.6])


def retrieve(query: str, source: str = "runbook", k: int = 4) -> list[dict]:
    """질의에 대한 상위 k개 청크를 {"doc_id","text"} 형태로 반환."""
    docs = get_retriever(source, k).invoke(query)[:k]
    return [{"doc_id": d.metadata["doc_id"], "text": d.page_content} for d in docs]


if __name__ == "__main__":
    for q in ["주문 API에서 커넥션 타임아웃이 계속 나요", "디스크 꽉 찼을 때 로그 지워도 돼?"]:
        print(f"\n== {q}")
        for c in retrieve(q):
            print(" -", c["doc_id"])
