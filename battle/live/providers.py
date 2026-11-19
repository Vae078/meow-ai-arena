# -*- coding: utf-8 -*-
"""LLM provider 抽象。模型可配置，key 从环境变量读，不落盘。

provider 取值：
  mock        MockProvider（不发网络，返回固定文本，用于全链路验证）
  openai      OpenAICompatProvider（零依赖 urllib，base_url 可配，覆盖 OpenAI/DeepSeek/Qwen/GLM/Ollama 等）
  anthropic   AnthropicProvider（anthropic SDK）
"""
import json
import os
import urllib.request


class Provider:
    def chat(self, system, user):
        raise NotImplementedError


class MockProvider(Provider):
    def __init__(self, cfg):
        self.reply = cfg.get("mock_reply", "")

    def chat(self, system, user):
        return self.reply


class OpenAICompatProvider(Provider):
    def __init__(self, cfg):
        self.model = cfg["model"]
        self.base_url = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = cfg.get("api_key", "") or os.environ.get(cfg.get("api_key_env", "OPENAI_API_KEY"), "")
        self.temperature = float(cfg.get("temperature", 0.7))
        self.max_tokens = int(cfg.get("max_tokens", 1000))
        self.timeout = float(cfg.get("timeout", 120))
        self.effort = cfg.get("effort", "")
        self.last_reasoning = ""

    def chat(self, system, user):
        url = self.base_url + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.effort:
            payload["effort"] = self.effort
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.api_key,
        })
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        msg = body["choices"][0]["message"]
        content = msg.get("content") or ""
        rc = msg.get("reasoning_content") or ""
        # 推理模型（如 deepseek-flash）把思考写进 reasoning_content，答案写进 content。
        # content 为空（推理占满 max_tokens）时回退用 reasoning_content，避免整场空指令。
        if content.strip():
            self.last_reasoning = rc
        else:
            self.last_reasoning = ""
            content = rc
        return content


class AnthropicProvider(Provider):
    def __init__(self, cfg):
        import anthropic  # 惰性导入：没装就不用
        api_key = cfg.get("api_key", "") or os.environ.get(cfg.get("api_key_env", "ANTHROPIC_API_KEY"), "")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = cfg["model"]
        self.temperature = float(cfg.get("temperature", 0.7))
        self.max_tokens = int(cfg.get("max_tokens", 1000))

    def chat(self, system, user):
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def make_provider(cfg):
    p = cfg.get("provider", "mock")
    if p == "mock":
        return MockProvider(cfg)
    if p in ("openai", "openai_compat"):
        return OpenAICompatProvider(cfg)
    if p == "anthropic":
        return AnthropicProvider(cfg)
    raise ValueError(f"不认识的 provider：{p}（可选 mock / openai / anthropic）")
