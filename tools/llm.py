#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
llm.py —— 统一的 OpenAI 兼容大模型调用（给题库生成、语义判分用）

配置：tools/llm.json
{
  "base_url": "https://open.bigmodel.cn/api/paas/v4",
  "api_key": "你的key",
  "model": "glm-4-flash"
}

常用国内端点（都兼容 OpenAI 格式）：
  智谱 GLM-4-Flash   https://open.bigmodel.cn/api/paas/v4          glm-4-flash（免费）
  阿里 通义 qwen     https://dashscope.aliyuncs.com/compatible-mode/v1  qwen-turbo
  DeepSeek          https://api.deepseek.com/v1                    deepseek-chat（便宜）
  SiliconFlow       https://api.siliconflow.cn/v1                  Qwen/Qwen2.5-7B-Instruct（有免费额度）

命令行自测：
  python tools/llm.py --test
  python tools/llm.py "用一句话解释推挽输出"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(ROOT, "tools", "llm.json")


def load_conf() -> dict:
    if not os.path.exists(CONF):
        return {}
    try:
        with open(CONF, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def available() -> bool:
    c = load_conf()
    return bool(c.get("api_key") and c.get("base_url") and c.get("model"))


def chat(messages: list[dict], *, temperature: float = 0.3, max_tokens: int = 1200,
         timeout: int = 90) -> str:
    """调用模型；未配置时抛 RuntimeError。

    2026-10-27 实测修正：早先注释写「沙箱内会因 TLS 被拦，需要 full-access」，
    实际在工作区沙箱（workspace-write）里可以直接调通，不必提权。
    真会失败的只有：断网、限速（429）、key 过期。
    """
    c = load_conf()
    if not available():
        raise RuntimeError("未配置 tools/llm.json（缺 api_key / base_url / model）")

    url = c["base_url"].rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": c["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + c["api_key"],
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError("HTTP %s: %s" % (e.code, detail)) from e

    return data["choices"][0]["message"]["content"].strip()


def ask(prompt: str, *, system: str = "", **kw) -> str:
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return chat(msgs, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?", default="")
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()

    if not available():
        print("未配置：请先创建 tools/llm.json（见文件头注释）")
        sys.exit(1)

    try:
        if args.test:
            print("model:", load_conf().get("model"))
            print(chat([{"role": "user", "content": "只回复两个字：可用"}], max_tokens=20))
        elif args.prompt:
            print(ask(args.prompt))
        else:
            print("用法: python tools/llm.py \"问题\" | --test")
    except Exception as e:  # noqa: BLE001
        print("失败:", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
