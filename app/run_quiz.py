"""完整在线答题驱动：开始答题 -> 逐题解析/作答/提交 -> 直到交卷完成。

用法（需先报名并有有效 cookie）:
  python -m app.run_quiz --vip 630799
  python -m app.run_quiz --vip 630799 --dry-run   # 只解析不提交
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time

from bs4 import BeautifulSoup

from .config import load_config, PROJECT_ROOT, APP_DIR
from .live_engine import LiveSession, parse_question
from .bank import QuestionBank, similarity

LOG_DIR = os.path.join(APP_DIR, "logs")
log = logging.getLogger("autoquiz.run")


def read_cookie():
    p = os.path.join(LOG_DIR, "wx_aids0_cookie.txt")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    cfg = load_config()
    return cfg.login.cookie


JUDGE_POS = ("正确", "对", "是", "可以", "会", "应", "需要", "建议")
JUDGE_NEG = ("错误", "错", "不能", "不会", "不", "禁止", "不可", "无需")


def decide(q, bank, llm=None):
    """返回 [(input_name, value), ...] 提交项。"""
    # 1) 题库（用题干+选项文本匹配）
    item, s = bank.best_match(q.stem)
    if item and s >= 0.62:
        ans_texts = [str(x) for x in item["answer"]]
        picks = []
        used = set()
        for at in ans_texts:
            best, bs, bi = None, 0.0, -1
            for i, o in enumerate(q.options):
                if i in used:
                    continue
                sc = similarity(at, o["text"])
                if q.qtype == "judge":
                    ap = any(w in at for w in JUDGE_POS); an = any(w in at for w in JUDGE_NEG)
                    op = any(w in o["text"] for w in JUDGE_POS); on = any(w in o["text"] for w in JUDGE_NEG)
                    if (ap and not an and op and not on) or (an and not ap and on and not op):
                        sc = max(sc, 0.9)
                if sc > bs:
                    best, bs, bi = o, sc, i
            if best is not None and bs >= 0.45:
                picks.append((best["name"], best["value"]))
                used.add(bi)
        if picks:
            log.info("  [题库 %.2f] -> %s", s, picks)
            return picks

    # 2) LLM
    if llm is not None:
        try:
            picks = llm.decide(q)
            if picks:
                log.info("  [LLM] -> %s", picks)
                return picks
        except Exception as e:
            log.warning("  LLM 失败: %s", e)

    # 3) 启发式
    if q.qtype == "judge":
        pick = next((o for o in q.options if any(w in o["text"] for w in JUDGE_POS)), q.options[0])
        log.info("  [启发式-judge] -> %s", [(pick["name"], pick["value"])])
        return [(pick["name"], pick["value"])]

    if q.qtype == "multiple":
        # 多选题：启发式选所有"正向/合理"选项（排除明显错误项：仅/全部/不需要/不会/一定/只要/继续/频繁等）
        NEG = ("只", "全部", "完全", "不需要", "不能", "不会", "一定", "只要", "继续", "频繁",
               "仅", "毫无", "终生", "彻底", "立即停药", "代购", "私下")
        picks = []
        for o in q.options:
            t = o["text"]
            if any(n in t for n in NEG):
                continue
            picks.append((o["name"], o["value"]))
        if not picks:  # 全被判负，退回前两个
            picks = [(o["name"], o["value"]) for o in q.options[:2]]
        if len(picks) < 2:  # 多选至少2项
            for o in q.options:
                if (o["name"], o["value"]) not in picks:
                    picks.append((o["name"], o["value"]))
                if len(picks) >= 2:
                    break
        log.info("  [启发式-multi] -> %s", picks)
        return picks

    pick = max(q.options, key=lambda o: len(o["text"]))
    return [(pick["name"], pick["value"])]


def run(vip: str, dry_run: bool, max_q: int = 30):
    cfg = load_config()
    cookie = read_cookie()
    sess = LiveSession.new(cfg.base_url, cookie, cfg.login.user_agent)
    bank = QuestionBank()

    url = f"{cfg.base_url}/answer2.html?vip={vip}"
    log.info("GET %s", url)
    r = sess.session.get(url, timeout=20, allow_redirects=False)
    log.info("status=%s loc=%s len=%s", r.status_code, r.headers.get("Location"), len(r.text))

    # 若被导到 preanswer(开始答题页)，先开始答题
    if "preanswer" in (r.headers.get("Location") or "") or "preanswer" in r.url:
        pu = f"{cfg.base_url}/preanswer1.html?vip={vip}"
        log.info("POST getq 开始答题 %s", pu)
        r = sess.session.post(pu, data={"getq": "开始答题"}, timeout=20, allow_redirects=False)
        loc = r.headers.get("Location") or ""
        log.info("开机答题返回 status=%s loc=%s", r.status_code, loc)
        if loc:
            nxt = loc if loc.startswith("http") else f"{cfg.base_url}/{loc.lstrip('/')}"
            r = sess.session.get(nxt, timeout=20)
            url = nxt

    if r.status_code != 200:
        log.error("答题页不可用 status=%s", r.status_code)
        return

    quiz_url = r.url
    html = r.text
    answered_count = 0
    for step in range(max_q):
        q = parse_question(html)
        if q is None:
            log.warning("未解析到题目，可能已交卷。检查页面……")
            if any(k in html for k in ("成绩", "得分", "完成", "交卷", "分数", "合格")):
                log.info("检测到完成/成绩页")
            else:
                with open(os.path.join(LOG_DIR, "quiz_final.html"), "w", encoding="utf-8") as f:
                    f.write(html)
                log.info("未识别页面已保存 quiz_final.html")
            break
        log.info("Q%s [%s] %s | 进度 %s", step + 1, q.qtype, q.stem[:36], q.answered)
        picks = decide(q, bank)
        data = {}
        for name, val in picks:
            data[name] = val
        data["questionid"] = q.qid
        data["answer"] = "提交"
        if dry_run:
            log.info("  DRY: payload=%s", data)
            break
        pr = sess.session.post(quiz_url, data=data, timeout=20, allow_redirects=True)
        html = pr.text
        answered_count += 1
        with open(os.path.join(LOG_DIR, f"quiz_step_{step+1}.html"), "w", encoding="utf-8") as f:
            f.write(html)
        log.info("  提交 -> status=%s len=%s", pr.status_code, len(html))
        # 若返回完成页
        if any(k in html for k in ("成绩", "得分", "您已完成", "交卷成功", "分", "合格")) and not parse_question(html):
            log.info("*** 答题结束/成绩页 ***")
            with open(os.path.join(LOG_DIR, "quiz_result.html"), "w", encoding="utf-8") as f:
                f.write(html)
            break
        time.sleep(0.4)

    log.info("共提交 %d 题", answered_count)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--vip", required=True, help="preanswer 页面的 vip 令牌")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(os.path.join(LOG_DIR, "run_quiz.log"), encoding="utf-8")],
    )
    run(args.vip, args.dry_run)


if __name__ == "__main__":
    main()

