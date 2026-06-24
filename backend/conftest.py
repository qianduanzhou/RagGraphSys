"""根级 conftest —— 在任何模块导入前确保存在 LLM_API_KEY。

导入 ``main`` 时会在模块加载阶段调用 ``get_settings()``，这要求 key 存在。
测试从不访问真实 API（全部被 mock），因此这里为整个会话设置一个占位 key。
"""
import os

os.environ.setdefault("LLM_API_KEY", "test-key-not-real")
