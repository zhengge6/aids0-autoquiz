"""命令行入口。

子命令：
  check    校验登录态是否有效（能否打开答题页）
  inspect  抓取答题页原始 HTML 并解析题目结构（用于校准 parser / submit 字段）
  solve    完整流程：取题 -> 作答 -> (可选)提交
  demo     使用内置样例 HTML 离线演示解析+作答，不联网
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from .config import Config, load_config, PROJECT_ROOT
from .http_client import HttpClient
from .parser import parse_questions
from .bank import QuestionBank
from .solver import Solver
from .submit import submit, build_payload

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")


def setup_logging(verbose: bool):
    os.makedirs(LOG_DIR, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(os.path.join(LOG_DIR, "autoquiz.log"), encoding="utf-8"),
        ],
    )


log = logging.getLogger("autoquiz.cli")


def cmd_check(cfg: Config):
    client = HttpClient(cfg)
    log.info("检查登录态: %s", cfg.entry_url)
    resp = client.get(cfg.entry_url)
    if resp is None:
        log.error("无法访问答题页")
        return 1
    if client.is_logged_in(resp.text):
        log.info("登录态有效，答题页可访问（%d 字节）", len(resp.text))
        return 0
    log.error("登录态无效或未登录。请从微信登录后复制 Cookie 到 config.json。")
    log.error("返回片段: %s", (resp.text or "")[:200])
    return 2


def cmd_inspect(cfg: Config):
    client = HttpClient(cfg)
    resp = client.get(cfg.entry_url)
    if resp is None:
        log.error("无法访问答题页")
        return 1
    html = resp.text
    path = os.path.join(LOG_DIR, "answer_page_raw.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("原始 HTML 已保存: %s (%d 字节)", path, len(html))
    if not client.is_logged_in(html):
        log.warning("注意：页面疑似未登录态，解析结果可能为空。")
    qs = parse_questions(html)
    log.info("解析到 %d 道题", len(qs))
    for q in qs:
        log.info("#%d [%s] field=%s", q.index, q.qtype, q.qid)
        log.info("   题干: %s", q.stem[:80])
        for o in q.options:
            log.info("     %s) v=%s %s", o.label, o.value, o.text[:40])
    out = os.path.join(LOG_DIR, "parsed_questions.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump([q.to_dict() for q in qs], f, ensure_ascii=False, indent=2)
    log.info("解析结果已保存: %s", out)
    return 0


def cmd_solve(cfg: Config):
    client = HttpClient(cfg)
    resp = client.get(cfg.entry_url)
    if resp is None:
        log.error("无法访问答题页")
        return 1
    html = resp.text
    if cfg.safety.save_raw_html:
        with open(os.path.join(LOG_DIR, "answer_page_raw.html"), "w", encoding="utf-8") as f:
            f.write(html)

    if not client.is_logged_in(html):
        log.error("未检测到有效登录态，停止。请先在微信登录并更新 Cookie。")
        log.error("返回片段: %s", (html or "")[:200])
        return 2

    qs = parse_questions(html)
    if not qs:
        log.error("未解析到题目，请运行 inspect 检查页面结构并调整 parser。")
        return 3

    bank = QuestionBank()
    solver = Solver(cfg, bank)
    qs = solver.solve_all(qs)

    answered = [q for q in qs if q.answer_value is not None]
    log.info("作答完成: %d/%d 题", len(answered), len(qs))
    from collections import Counter
    src = Counter(q.source for q in qs)
    log.info("来源分布: %s", dict(src))

    result_path = os.path.join(LOG_DIR, "answers.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump([q.to_dict() for q in qs], f, ensure_ascii=False, indent=2)
    log.info("作答结果: %s", result_path)

    ok = submit(client, cfg, qs)
    return 0 if ok else 4


def cmd_demo(cfg: Config):
    sample = os.path.join(PROJECT_ROOT, "samples", "sample_quiz.html")
    with open(sample, "r", encoding="utf-8") as f:
        html = f.read()
    qs = parse_questions(html)
    bank = QuestionBank()
    solver = Solver(cfg, bank)
    qs = solver.solve_all(qs)
    print("\n===== DEMO 离线解析+作答结果 =====")
    for q in qs:
        print(f"#{q.index} [{q.qtype}] {q.stem}")
        for o in q.options:
            mark = " <== 选中" if o.label in q.answer_labels else ""
            print(f"    {o.label}. {o.text}{mark}")
        print(f"    -> 答案: {q.answer_labels} (value={q.answer_value}) 来源={q.source} score={q.score:.2f}\n")
    payload = build_payload(cfg, qs)
    print("提交 payload 预览:", payload)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="2026 预防艾滋病知识竞赛 自动答题程序")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("check", "inspect", "solve", "demo"):
        sub.add_parser(name)
    args = parser.parse_args(argv)

    if args.cmd == "demo":
        setup_logging(args.verbose)
        return cmd_demo(load_config())
    setup_logging(args.verbose)
    cfg = load_config()
    return {"check": cmd_check, "inspect": cmd_inspect, "solve": cmd_solve}[args.cmd](cfg)


if __name__ == "__main__":
    sys.exit(main())
