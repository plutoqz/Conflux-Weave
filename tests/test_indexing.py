from conflux_weave.indexing import LanceDBDenseIndex
from conflux_weave.retrieval import RetrievalDocument


def test_lancedb_dense_index_preserves_chunk_lineage_and_search(tmp_path):
    index = LanceDBDenseIndex(tmp_path / "lancedb")
    documents = (
        RetrievalDocument("chunk-a", "alpha", "snapshot-a", {"type": "pdf_page", "page": 1}),
        RetrievalDocument("chunk-b", "beta", "snapshot-b", {"type": "pdf_page", "page": 2}),
    )
    manifest = index.publish(documents, ((1.0, 0.0), (0.0, 1.0)))
    result = index.search((1.0, 0.0), top_k=2)
    assert manifest["index_type"] == "lancedb"
    assert result.hits[0].document_id == "chunk-a"
    assert result.hits[0].source_snapshot_id == "snapshot-a"
    assert result.hits[0].locator == {"type": "pdf_page", "page": 1}
