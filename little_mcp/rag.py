"""Document search (the "R" in RAG).

Every PDF, .txt and .md file in data/ is split into chunks, embedded and
stored in a Chroma vector index. The index records a fingerprint of the
files it was built from; when you add, change or delete a document, the
fingerprint changes and the index is rebuilt automatically.

The server only *retrieves* matching passages. The agent's LLM reads them
and writes the answer, so any MCP client (Claude Desktop, Claude Code...)
can use this tool too.
"""

import hashlib
import logging
import threading
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from . import config

log = logging.getLogger(__name__)

SUPPORTED = {".pdf", ".txt", ".md"}
SKIP_NAMES = {"readme.md"}
FINGERPRINT_FILE = "fingerprint.txt"
COLLECTION = "documents"


def find_documents(data_dir: Path) -> list[Path]:
    return sorted(
        p for p in data_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED and p.name.lower() not in SKIP_NAMES
    )


def fingerprint(files: list[Path], data_dir: Path) -> str:
    h = hashlib.sha256()
    for p in files:
        st = p.stat()
        h.update(f"{p.relative_to(data_dir)}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def load_documents(files: list[Path], data_dir: Path) -> list[Document]:
    """One Document per PDF page, or per text file."""
    docs: list[Document] = []
    for path in files:
        source = str(path.relative_to(data_dir))
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            for page_no, page in enumerate(PdfReader(path).pages):
                docs.append(Document(page.extract_text() or "", metadata={"source": source, "page": page_no}))
        else:
            docs.append(Document(path.read_text(encoding="utf-8"), metadata={"source": source}))
    return docs


class DocumentIndex:
    def __init__(
        self,
        data_dir: Path | None = None,
        index_dir: Path | None = None,
        embeddings: Embeddings | None = None,
    ):
        self.data_dir = Path(data_dir or config.DATA_DIR)
        self.index_dir = Path(index_dir or config.INDEX_DIR)
        self._embeddings = embeddings
        self._store = None
        self._built_from: str | None = None
        self._lock = threading.Lock()

    @property
    def embeddings(self) -> Embeddings:
        if self._embeddings is None:
            from langchain_ollama import OllamaEmbeddings

            self._embeddings = OllamaEmbeddings(model=config.EMBED_MODEL)
        return self._embeddings

    def files(self) -> list[Path]:
        return find_documents(self.data_dir) if self.data_dir.exists() else []

    def _ensure_index(self):
        """Open the index, rebuilding it if the documents changed."""
        files = self.files()
        current = fingerprint(files, self.data_dir)
        if self._store is not None and self._built_from == current:
            return self._store

        from langchain_chroma import Chroma

        def open_store():
            return Chroma(
                collection_name=COLLECTION,
                persist_directory=str(self.index_dir),
                embedding_function=self.embeddings,
            )

        self.index_dir.mkdir(parents=True, exist_ok=True)
        fp_path = self.index_dir / FINGERPRINT_FILE
        store = open_store()
        if not (fp_path.exists() and fp_path.read_text().strip() == current):
            log.info("Indexing %d document(s) for search. This happens once, and again when they change...", len(files))
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            chunks = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200).split_documents(
                load_documents(files, self.data_dir)
            )
            store.delete_collection()
            store = open_store()
            if chunks:
                store.add_documents(chunks)
            fp_path.write_text(current)
            log.info("Document index ready (%d passage%s).", len(chunks), "" if len(chunks) == 1 else "s")
        self._store, self._built_from = store, current
        return store

    def search(self, query: str, k: int = 4) -> list[dict]:
        with self._lock:
            store = self._ensure_index()
            hits = store.similarity_search(query, k=k)
        return [
            {
                "source": d.metadata.get("source", "?"),
                "page": d.metadata["page"] + 1 if "page" in d.metadata else None,
                "text": d.page_content,
            }
            for d in hits
        ]
