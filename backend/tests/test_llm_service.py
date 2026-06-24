"""services.llm_service.LLMService 的测试（ChatOpenAI 注入替身，无网络）。"""
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from services.llm_service import LLMService
from tests.conftest import FakeChatModel


def make_service(settings, **kw) -> LLMService:
    return LLMService(settings, llm=FakeChatModel(**kw))


def test_chat_returns_content(settings):
    svc = make_service(settings, chat_content="hello world")
    assert svc.chat([{"role": "user", "content": "hi"}]) == "hello world"


def test_chat_converts_messages_to_langchain(settings):
    fake = FakeChatModel(chat_content="ok")
    svc = LLMService(settings, llm=fake)
    svc.chat([{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}])
    msgs = fake.invoked_with
    assert isinstance(msgs[0], SystemMessage) and msgs[0].content == "sys"
    assert isinstance(msgs[1], HumanMessage) and msgs[1].content == "u"


def test_chat_binds_temperature_and_max_tokens(settings):
    fake = FakeChatModel(chat_content="ok")
    svc = LLMService(settings, llm=fake)
    svc.chat([{"role": "user", "content": "x"}], temperature=0.1, max_tokens=10)
    assert {"temperature": 0.1, "max_tokens": 10} in fake.bound_kwargs


def test_chat_no_bind_when_no_overrides(settings):
    fake = FakeChatModel(chat_content="ok")
    svc = LLMService(settings, llm=fake)
    svc.chat([{"role": "user", "content": "x"}])
    assert fake.bound_kwargs == []  # 直接用原对象，不 bind


def test_chat_stream_yields_tokens(settings):
    svc = make_service(settings, stream_chunks=["你", "好", "！"])
    assert list(svc.chat_stream([{"role": "user", "content": "x"}])) == ["你", "好", "！"]


def test_chat_stream_skips_empty_content(settings):
    svc = make_service(settings, stream_chunks=["你", "", None, "好"])
    assert list(svc.chat_stream([{"role": "user", "content": "x"}])) == ["你", "好"]


def test_chat_propagates_exception(settings):
    svc = make_service(settings, raise_on_invoke=True)
    with pytest.raises(RuntimeError):
        svc.chat([{"role": "user", "content": "x"}])


def test_extract_keywords_parses_json_array(settings):
    svc = make_service(settings, chat_content='["张三", "Neo4j", "公司"]')
    assert svc.extract_keywords("q") == ["张三", "Neo4j", "公司"]


def test_extract_keywords_fenced_json(settings):
    svc = make_service(settings, chat_content="```json\n[\"A\"]\n```")
    assert svc.extract_keywords("q") == ["A"]


def test_extract_keywords_malformed_returns_empty(settings):
    svc = make_service(settings, chat_content="not json")
    assert svc.extract_keywords("q") == []


def test_extract_graph_filters_invalid_triples(settings):
    svc = make_service(settings, chat_content='{"triples": [{"head":"A","rel":"uses","tail":"B"}, '
                                   '{"head":"","rel":"x","tail":"Y"}, {"head":"C","tail":"D"}]}')
    triples = svc.extract_graph("text")
    assert triples == [
        {"head": "A", "rel": "uses", "tail": "B"},
        {"head": "C", "rel": "relates_to", "tail": "D"},
    ]


def test_reflect_parses_verdict(settings):
    svc = make_service(settings, chat_content='{"pass": false, "feedback": "too vague"}')
    assert svc.reflect("q", "a", "c") == {"pass": False, "feedback": "too vague"}


def test_reflect_failure_defaults_to_pass(settings):
    # reflect 内部 chat 抛异常 -> 捕获 -> 默认通过，不让图崩掉
    svc = make_service(settings, raise_on_invoke=True)
    assert svc.reflect("q", "a", "c")["pass"] is True
