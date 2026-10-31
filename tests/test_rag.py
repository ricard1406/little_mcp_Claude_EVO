from little_mcp.rag import DocumentIndex


def test_search_returns_passages_with_source(doc_index):
    hits = doc_index.search("Dianne", k=1)
    assert hits[0]["source"] == "candidates.txt"
    assert "Bridgewater" in hits[0]["text"]


def test_readme_is_not_indexed(doc_index):
    assert [p.name for p in doc_index.files()] == ["candidates.txt"]


def test_index_rebuilds_when_documents_change(doc_index, docs_dir):
    doc_index.search("anything")
    (docs_dir / "candidates.txt").write_text("Mario Rossi, Torino IT, scores 9 and 8.\n")
    texts = [h["text"] for h in doc_index.search("anything", k=5)]
    assert texts == ["Mario Rossi, Torino IT, scores 9 and 8."]


def test_index_is_reused_across_restarts(doc_index, docs_dir, tmp_path):
    doc_index.search("anything")
    reopened = DocumentIndex(docs_dir, doc_index.index_dir, doc_index.embeddings)
    fp_before = (doc_index.index_dir / "fingerprint.txt").stat().st_mtime_ns
    assert reopened.search("Dianne", k=1)[0]["source"] == "candidates.txt"
    assert (doc_index.index_dir / "fingerprint.txt").stat().st_mtime_ns == fp_before
