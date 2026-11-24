"""提交作答：把解题结果按站点表单格式 POST 回服务端。"""
from __future__ import annotations

import logging
from typing import Dict, List

from .config import Config
from .http_client import HttpClient
from .parser import Question

log = logging.getLogger("autoquiz.submit")


def build_payload(cfg: Config, questions: List[Question]) -> Dict[str, str]:
    """构造提交表单。默认字段命名 answer{index} -> value。可在 config.submit 调整。"""
    payload: Dict[str, str] = dict(cfg.submit.extra_fields or {})
    for q in questions:
        if q.answer_value is None:
            continue
        key = f"{cfg.submit.field_answer_prefix}{q.index}"
        payload[key] = q.answer_value
    return payload


def submit(client: HttpClient, cfg: Config, questions: List[Question]) -> bool:
    payload = build_payload(cfg, questions)
    url = cfg.url(cfg.submit.endpoint_path)

    if cfg.safety.dry_run:
        log.info("[DRY-RUN] 不实际提交。将 POST %s", url)
        log.info("[DRY-RUN] payload = %s", payload)
        return True

    log.info("提交 %d 题答案到 %s", len([q for q in questions if q.answer_value]), url)
    if cfg.submit.json_body:
        resp = client.post(url, json=payload)
    else:
        resp = client.post(url, data=payload)

    if resp is None:
        log.error("提交请求失败")
        return False

    body = resp.text or ""
    log.info("提交返回 HTTP %s, %d 字节", resp.status_code, len(body))
    if cfg.safety.save_raw_html:
        with open("logs/submit_response.html", "w", encoding="utf-8") as f:
            f.write(body)

    ok_markers = ["成功", "成绩", "得分", "分数", "提交成功", "已交卷", "合格"]
    fail_markers = ["失败", "错误", "请先登录", "非法", "超时"]
    if any(m in body for m in ok_markers) and not any(m in body for m in fail_markers):
        log.info("提交成功")
        return True
    if any(m in body for m in fail_markers):
        log.warning("提交返回疑似失败: %s", body[:200])
        return False
    log.info("提交完成（返回未明确，视为已发送）")
    return True
