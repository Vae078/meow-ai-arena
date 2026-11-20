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

    def run_agent(self, system, user, tools, execute_tool, max_rounds=3, budget=None):
        """工具调用循环，返回 (最终文本, 消耗的调用次数)。

        - tools：中间格式 [{"name","description","params"}]，provider 各自转成本协议的 tools 参数
        - execute_tool(name, args_dict) -> str：执行一个工具，返回喂给 LLM 的结果
        - budget：剩余可调用次数（None = 不限）

        默认实现不调工具，直接单次对话（mock 走这里）。
        """
        return self.chat(system, user), 1


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

    def _request(self, system, messages, tools):
        """发一次请求，返回 (content, tool_calls)。tool_calls = [{"id","name","arguments"}]。"""
        url = self.base_url + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            # 推理模型（deepseek-flash 等）thinking 模式工具调用时不要发 tool_choice，让模型自己决定
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
        tool_calls = []
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args = {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            tool_calls.append({"id": tc.get("id", ""), "name": fn.get("name", ""), "arguments": args})
        if tool_calls:
            # 调工具时 content 本来就空（思考在 rc、答案待出）；保留 rc 供 assistant 消息回填
            self.last_reasoning = rc
        elif content.strip():
            self.last_reasoning = rc
        else:
            # 无工具调用且 content 空（推理占满 max_tokens）：回退用 reasoning_content，避免整场空指令
            self.last_reasoning = ""
            content = rc
        return content, tool_calls

    def chat(self, system, user):
        content, _ = self._request(system, [{"role": "user", "content": user}], None)
        return content

    def run_agent(self, system, user, tools, execute_tool, max_rounds=3, budget=None):
        openai_tools = self._to_openai_tools(tools)
        messages = [{"role": "user", "content": user}]
        n = 0
        content = ""
        for _ in range(max_rounds):
            if budget is not None and n >= budget:
                break
            content, tool_calls = self._request(system, messages, openai_tools)
            n += 1
            if not tool_calls:
                return content, n
            assistant_tc = [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
                for tc in tool_calls
            ]
            assistant_msg = {"role": "assistant", "content": content or None, "tool_calls": assistant_tc}
            # 推理模型要求跨 assistant 消息保留 reasoning_content，否则后续请求会失败
            if self.last_reasoning:
                assistant_msg["reasoning_content"] = self.last_reasoning
            messages.append(assistant_msg)
            for tc in tool_calls:
                result = execute_tool(tc["name"], tc["arguments"])
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
        return content, n

    @staticmethod
    def _to_openai_tools(tools):
        out = []
        for t in tools:
            props = {}
            for k, v in (t.get("params") or {}).items():
                props[k] = {"type": v["type"], "description": v.get("description", "")}
            out.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": {"type": "object", "properties": props},
                },
            })
        return out


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

    def run_agent(self, system, user, tools, execute_tool, max_rounds=3, budget=None):
        anthropic_tools = self._to_anthropic_tools(tools)
        messages = [{"role": "user", "content": [{"type": "text", "text": user}]}]
        n = 0
        text = ""
        for _ in range(max_rounds):
            if budget is not None and n >= budget:
                break
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=system,
                messages=messages,
                tools=anthropic_tools,
            )
            n += 1
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            tool_uses = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
            if not tool_uses:
                return text, n
            messages.append({"role": "assistant", "content": msg.content})
            results = []
            for tu in tool_uses:
                args = getattr(tu, "input", None) or {}
                result = execute_tool(tu.name, args)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result})
            messages.append({"role": "user", "content": results})
        return text, n

    @staticmethod
    def _to_anthropic_tools(tools):
        out = []
        for t in tools:
            props = {}
            for k, v in (t.get("params") or {}).items():
                props[k] = {"type": v["type"], "description": v.get("description", "")}
            out.append({
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": {"type": "object", "properties": props},
            })
        return out


def make_provider(cfg):
    p = cfg.get("provider", "mock")
    if p == "mock":
        return MockProvider(cfg)
    if p in ("openai", "openai_compat"):
        return OpenAICompatProvider(cfg)
    if p == "anthropic":
        return AnthropicProvider(cfg)
    raise ValueError(f"不认识的 provider：{p}（可选 mock / openai / anthropic）")
