# -*- coding: utf-8 -*-
"""tests/conftest.py — 测试全局护栏

## 为什么需要这道闸门

项目配了真实的 API Key（`config.local.json`），而 `ai.ai_available()` 只看"有没有
Key"，**不看是不是在测试里**。于是任何没打桩的 AI 路径都会在测试中真的调用模型：

  - 实测过：心跳循环在 lifespan 里生成掌柜复盘（反复升级后变成 6 次模型调用），
    每起一个 TestClient 就跑一次 → 整个套件从 21 秒变成 127 秒，**并且真实扣费**。
  - 这类问题很隐蔽：测试全绿、只是"变慢"，不看耗时根本发现不了。

所以这里把网线拔掉：测试里任何走到**真实 ai.chat** 的调用都会立刻抛错并点名是哪段
文本触发的。需要模型返回值的测试自己 `mock.patch("ai.chat")`，补丁会盖过本闸门，
所以不影响既有测试。

## 顺带防的第二种情况

`config._LOCAL_CONFIG` 指向开发者本机的 `config.local.json`。测试若不小心写设置
（`save_settings`），会把开发者的 Key/模型配置**覆盖掉**。这里一并把配置路径
指向临时文件，测试读写设置都落在沙箱里。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# 既有测试覆盖完整能力面；core/full 边界由注册表专项测试单独校验。
os.environ["SHOP_API_PROFILE"] = "full"
import config  # noqa: E402  必须在 main 导入前隔离本机配置
config._LOCAL_CONFIG = Path(os.environ.get("TEMP", ".")) / f"xirang-pytest-{os.getpid()}.json"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class RealAICallBlocked(RuntimeError):
    """测试里试图真的调用大模型。"""


def _blocked(messages, **kwargs):
    text = ""
    try:
        for m in messages or []:
            if isinstance(m, dict) and m.get("content"):
                text = str(m["content"])
    except Exception:  # noqa: BLE001
        pass
    raise RealAICallBlocked(
        "测试里试图真的调用大模型（会联网并扣费）。\n"
        "请给这条用例加 mock.patch(\"ai.chat\", ...) 或 mock.patch(\"ai.ai_available\", "
        "return_value=False)，走降级路径。\n"
        f"触发文本片段：{text[:120]!r}")


def _client_is_stubbed() -> bool:
    """测试是否已经把模型客户端换掉了（换了就说明它自己负责不联网）。

    `mock.patch.object(ai, "get_client")` 之后，`ai.get_client` 会带上 mock 的特征
    属性（`mock` / `side_effect` 等），据此判断即可 —— 比去翻 mock 的内部结构稳。
    """
    import ai
    getter = getattr(ai, "get_client", None)
    if getter is None:
        return False
    return any(hasattr(getter, attr) for attr in
               ("mock", "side_effect", "return_value", "assert_called"))


@pytest.fixture(autouse=True)
def _no_real_ai_calls(tmp_path, monkeypatch):
    """全局自动生效：拔掉网线（见模块 docstring）。

    但**给"已经自己处理了模型调用"的测试留门**：有些用例（如
    test_ai_domains.TestChatTokenBudget）故意打桩 `ai.get_client`，
    用假的 OpenAI 客户端验证 max_tokens 重试逻辑 —— 那种调用不会联网，
    不该被拦（第一版一律拦死，把这 4 条误伤了）。
    """
    import ai
    import config

    real_chat = ai.chat

    def _chat_guarded(messages, **kwargs):
        if _client_is_stubbed():
            return real_chat(messages, **kwargs)
        _blocked(messages, **kwargs)

    with mock.patch.object(ai, "chat", side_effect=_chat_guarded):
        # 设置读写落在临时目录，别动开发者的 config.local.json
        local = tmp_path / "config.local.json"
        monkeypatch.setattr(config, "_LOCAL_CONFIG", local, raising=False)
        yield
