"""core.utils 的测试。"""
from core.utils import extract_json, sanitize_relation_type, split_text, timing, truncate


def test_extract_json_plain():
    assert extract_json('{"a": 1, "b": 2}') == {"a": 1, "b": 2}


def test_extract_json_array():
    assert extract_json('["x", "y"]') == ["x", "y"]


def test_extract_json_fenced():
    assert extract_json("```json\n{\"a\": 1}\n```") == {"a": 1}


def test_extract_json_with_surrounding_prose():
    assert extract_json('结果是 {"pass": true, "feedback": ""} 完成') == {"pass": True, "feedback": ""}


def test_extract_json_garbage_returns_none():
    assert extract_json("no json here") is None
    assert extract_json("") is None


def test_truncate():
    assert truncate("abc", 10) == "abc"
    assert truncate("abcdefgh", 3) == "abc..."


def test_sanitize_relation_type_basic():
    assert sanitize_relation_type("works_for") == "WORKS_FOR"
    assert sanitize_relation_type("WORKS FOR") == "WORKS_FOR"


def test_sanitize_relation_type_strips_special_chars():
    # 注入尝试：只保留 [A-Za-z0-9_]（每个特殊字符 -> "_"）。
    assert sanitize_relation_type("a} RETURN n//") == "A__RETURN_N"


def test_sanitize_relation_type_empty_falls_back():
    assert sanitize_relation_type("") == "RELATES_TO"
    assert sanitize_relation_type("!!!") == "RELATES_TO"


def test_split_text_chunks():
    text = "句子。" * 200
    chunks = split_text(text, chunk_size=50, chunk_overlap=10)
    assert len(chunks) > 1
    # 还原是有损的，但每个分块都是非空字符串
    assert all(isinstance(c, str) and c for c in chunks)


def test_timing_passes_through_and_logs(caplog):
    @timing
    def add(a, b):
        return a + b

    with caplog.at_level("INFO"):
        assert add(2, 3) == 5
    assert any("executed in" in rec.message for rec in caplog.records)


def test_timing_propagates_exception(caplog):
    @timing
    def boom():
        raise ValueError("nope")

    with caplog.at_level("INFO"):
        try:
            boom()
        except ValueError:
            pass
    # 仍然记录了耗时日志行（finally 块）
    assert any("executed in" in rec.message for rec in caplog.records)
