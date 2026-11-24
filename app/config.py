"""配置加载与校验。"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict


def _res_dir() -> str:
    """打包资源目录：PyInstaller 解压到 _MEIPASS；否则源码目录父级。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app_dir() -> str:
    """运行目录（日志/缓存写这里）：exe 所在目录 或 源码目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


PROJECT_ROOT = _res_dir()          # 只读资源（题库等）
APP_DIR = _app_dir()               # 可写目录（logs）
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
EXAMPLE_PATH = os.path.join(_res_dir(), "config.example.json")

PLACEHOLDER_TOKENS = ("REPLACE_ME", "REPLACE", "TODO", "")


def _load_raw() -> Dict[str, Any]:
    path = CONFIG_PATH if os.path.exists(CONFIG_PATH) else EXAMPLE_PATH
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class HttpCfg:
    timeout: int = 20
    retries: int = 3
    retry_delay: float = 2.0
    min_interval: float = 0.8


@dataclass
class LoginCfg:
    cookie: str = ""
    user_agent: str = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
        "MicroMessenger/8.0.40(0x18002832) NetType/WIFI Language/zh_CN"
    )

    def cookie_ready(self) -> bool:
        c = (self.cookie or "").strip()
        return bool(c) and not any(tok in c for tok in ("REPLACE_ME", "REPLACE"))


@dataclass
class AnswerCfg:
    bank_match_threshold: float = 0.62
    strategy: str = "bank_then_llm"
    prefer_max_score: bool = True


@dataclass
class LlmCfg:
    enabled: bool = False
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    api_key: str = ""
    temperature: float = 0.0

    def ready(self) -> bool:
        return self.enabled and bool(self.api_key) and "REPLACE" not in self.api_key


@dataclass
class SubmitCfg:
    endpoint_path: str = "/answer2.php"
    method: str = "POST"
    json_body: bool = False
    field_answer_prefix: str = "answer"
    field_id: str = "id"
    extra_fields: Dict[str, str] = field(default_factory=dict)


@dataclass
class SafetyCfg:
    dry_run: bool = True
    max_submit_attempts_per_run: int = 1
    save_raw_html: bool = True


@dataclass
class Config:
    base_url: str
    entry_path: str
    vip: str
    login: LoginCfg
    http: HttpCfg
    answer: AnswerCfg
    llm: LlmCfg
    submit: SubmitCfg
    safety: SafetyCfg

    @property
    def entry_url(self) -> str:
        sep = "&" if "?" in self.entry_path else "?"
        return f"{self.base_url.rstrip('/')}{self.entry_path}{sep}vip={self.vip}"

    def url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"


def _strip_comments(d: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(d, dict):
        return d
    return {k: v for k, v in d.items() if not (isinstance(k, str) and k.startswith("_"))}


def load_config() -> Config:
    raw = _load_raw()
    return Config(
        base_url=raw.get("base_url", "http://www.aids0.com"),
        entry_path=raw.get("entry_path", "/answer2.php"),
        vip=str(raw.get("vip", "")),
        login=LoginCfg(**_strip_comments(raw.get("login", {}))),
        http=HttpCfg(**_strip_comments(raw.get("http", {}))),
        answer=AnswerCfg(**_strip_comments(raw.get("answer", {}))),
        llm=LlmCfg(**_strip_comments(raw.get("llm", {}))),
        submit=SubmitCfg(**_strip_comments(raw.get("submit", {}))),
        safety=SafetyCfg(**_strip_comments(raw.get("safety", {}))),
    )
