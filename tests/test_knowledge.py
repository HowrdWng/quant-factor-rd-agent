from pathlib import Path

from quant_factor_agent.retrieval import LocalKnowledgeBase


def test_local_knowledge_index_and_citation(tmp_path: Path) -> None:
    corpus = tmp_path / "reports"
    corpus.mkdir()
    report = corpus / "volume-momentum.md"
    report.write_text(
        "# 成交量与动量\n\n成交量异常放大与短期价格动量结合，可能反映信息扩散和投资者反应不足。"
        "研究时必须滞后成交量并进行样本外验证。",
        encoding="utf-8",
    )
    knowledge = LocalKnowledgeBase(tmp_path / "knowledge.db")
    result = knowledge.index_directory(corpus)

    assert result.indexed == 1
    assert result.chunks >= 1
    evidence = knowledge.search("成交量 动量", limit=3)
    assert evidence
    assert evidence[0].source_path == str(report.resolve())
    assert "成交量" in evidence[0].text


def test_knowledge_index_is_resumable(tmp_path: Path) -> None:
    report = tmp_path / "note.txt"
    report.write_text("价值因子使用账面价值与市场价值，并且财务数据需要按公告日对齐。" * 4, encoding="utf-8")
    knowledge = LocalKnowledgeBase(tmp_path / "knowledge.db")

    first = knowledge.index_directory(tmp_path)
    second = knowledge.index_directory(tmp_path)

    assert first.indexed == 1
    assert second.skipped == 1
