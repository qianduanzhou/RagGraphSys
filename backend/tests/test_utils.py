"""core.utils 的测试。"""
from core.utils import (
    extract_json,
    is_aggregate_query,
    sanitize_relation_type,
    split_text,
    timing,
    truncate,
)


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


# --- is_aggregate_query（聚合/穷举型查询判定）---
def test_is_aggregate_query_positive():
    for q in [
        "列出所有岗位",
        "有哪些职位",
        "全部岗位一览",
        "岗位清单",
        "汇总一下",
        "分别是什么",
        "每个岗位的要求",
        "总共有多少个岗位",
        "完整列表",
        "列举出所有部门",
    ]:
        assert is_aggregate_query(q), f"应判定为聚合：{q}"


def test_is_aggregate_query_negative():
    for q in ["你好", "消息结构的核心是什么", "随便看看", "广州车站行车岗位要什么学历", "解释一下 RAG"]:
        assert not is_aggregate_query(q), f"不应判定为聚合：{q}"


def test_is_aggregate_query_short_all_does_not_fire():
    # 仅「所有」两个字不触发（需后跟≥2字符），避免短输入误判
    assert not is_aggregate_query("所有")
    assert not is_aggregate_query("全部")
    # 但「所有岗位」这种后跟内容的仍触发
    assert is_aggregate_query("所有岗位")


def test_is_aggregate_query_empty():
    assert not is_aggregate_query("")


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
