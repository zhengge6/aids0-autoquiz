"""带重试、限速、Cookie 的 HTTP 客户端。"""
from __future__ import annotations

import time
import logging
from typing import Dict, Optional

import requests

from .config import Config

log = logging.getLogger("autoquiz.http")


class HttpClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": cfg.login.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
        })
        self._last_request = 0.0
        self._apply_cookie()

    def _apply_cookie(self):
        raw = (self.cfg.login.cookie or "").strip()
        if not raw:
            return
        for part in raw.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            self.session.cookies.set(k.strip(), v.strip())

    def _throttle(self):
        gap = self.cfg.http.min_interval - (time.time() - self._last_request)
        if gap > 0:
            time.sleep(gap)
        self._last_request = time.time()

    def request(self, method: str, url: str, **kwargs) -> Optional[requests.Response]:
        timeout = kwargs.pop("timeout", self.cfg.http.timeout)
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.cfg.http.retries + 1):
            try:
                self._throttle()
                resp = self.session.request(method, url, timeout=timeout, **kwargs)
                log.debug("%s %s -> %s (%d bytes)", method, url, resp.status_code, len(resp.content))
                return resp
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                log.warning("请求失败(%d/%d) %s %s: %s", attempt, self.cfg.http.retries, method, url, exc)
                if attempt < self.cfg.http.retries:
                    time.sleep(self.cfg.http.retry_delay * attempt)
        log.error("请求彻底失败: %s %s (%s)", method, url, last_exc)
        return None

    def get(self, url: str, **kwargs) -> Optional[requests.Response]:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> Optional[requests.Response]:
        return self.request("POST", url, **kwargs)

    def is_logged_in(self, html: str) -> bool:
        """判断返回的 answer2.php 是否是登录后的答题页。"""
        if not html:
            return False
        markers_not = ["请先登录", "请在微信客户端打开链接", "抱歉，出错了"]
        if any(m in html for m in markers_not):
            return False
        markers_yes = ["<form", "题型", "答题", "单选项", "submit", "题干", "考试"]
        return any(m in html for m in markers_yes)
