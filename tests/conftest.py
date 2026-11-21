import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from little_mcp.rag import DocumentIndex
from little_mcp.store import Inventory


@pytest.fixture(autouse=True)
def no_saved_choices(monkeypatch):
    """The developer's own .env may hold choices saved by the Settings tab: tests must not depend on them."""
    for name in ("LITTLE_MCP_PROVIDER", "LITTLE_MCP_ASK", "LITTLE_MCP_SHOW_TOOLS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(params=["tinysql", "sqlite"])
def inventory(request, tmp_path):
    return Inventory(backend=request.param, sqlite_path=tmp_path / "store.db", tinysql_dir=tmp_path / "tinysql")


@pytest.fixture
def docs_dir(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "candidates.txt").write_text("Dianne Bridgewater, Montgomery US, scores 6 and 5.\n")
    (d / "README.md").write_text("This file is ignored.\n")
    return d


@pytest.fixture
def doc_index(tmp_path, docs_dir):
    return DocumentIndex(docs_dir, tmp_path / "index", DeterministicFakeEmbedding(size=64))
