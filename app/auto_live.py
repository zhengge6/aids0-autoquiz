"""一键全自动答题：读电脑微信 Cookie -> 报名(如需) -> 开始答题 -> 逐题自动作答 -> 交卷 -> 报分。

前提：用户已在【电脑版微信内置浏览器】打开过竞赛页面并完成微信授权+关注公众号，
      登录态存在于微信 WebView Cookie 库中（脚本用 VSS+DPAPI 自动提取）。

用法:
  python -m app.auto_live                  # 全自动跑
  python -m app.auto_live --qrcode         # 只生成扫码登录二维码
  python -m app.auto_live --identity place # 用占位身份报名
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from .config import load_config, PROJECT_ROOT, APP_DIR
from .live_engine import LiveSession, parse_question
from .bank import QuestionBank
from .run_quiz import decide, JUDGE_POS

LOG_DIR = os.path.join(APP_DIR, "logs")
log = logging.getLogger("autoquiz.auto")

WX_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
         "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
         "MicroMessenger/8.0.40(0x18002832) NetType/WIFI Language/zh_CN")


def make_qrcode(text: str, out: str):
    import qrcode
    img = qrcode.make(text)
    img.save(out)
    return out


def read_cookie():
    """优先读 logs/wx_aids0_cookie.txt；否则用纯 Python wx_cookie 自动提取。"""
    p = os.path.join(LOG_DIR, "wx_aids0_cookie.txt")
    if os.path.exists(p):
        ck = open(p, encoding="utf-8").read().strip()
        if "PHPSESSID=" in ck:
            return ck
    # 自动提取（有缓存直接解密；无缓存且有管理员权限时重建卷影副本）
    try:
        import wx_cookie
        ck = wx_cookie.get_cookie(force=False, verbose=True)
        if ck:
            return ck
        log.warning("未取到 Cookie（可能需管理员权限生成卷影副本，或无微信登录态）")
    except Exception as e:
        log.warning("自动提取 Cookie 失败: %s", e)
    return ""


def ensure_registered(sess: LiveSession, base: str, identity: dict) -> bool:
    """访问 answer2.html；若停在报名表单页则自动提交报名。返回是否处于可答题状态。"""
    r = sess.session.get(f"{base}/answer2.html", timeout=20, allow_redirects=True)
    html = r.text
    if "ginfo" in html or "完善个人信息" in html:
        log.info("检测到报名表单，自动提交报名信息 ...")
        data = {
            "year": identity.get("year", "2026"),
            "school": identity.get("school", "测试大学"),
            "fullname": identity.get("fullname", "测试同学"),
            "qq": identity.get("qq", "10000000"),
            "mobile": identity.get("mobile", "13800000000"),
            "studentid": identity.get("studentid", "TEST" + str(int(time.time()))),
            "gmajor": identity.get("gmajor", "计算机"),
            "sex": identity.get("sex", "0"),
            "subinfo": "提交",
        }
        rr = sess.session.post(f"{base}/answer2.html", data=data, timeout=20, allow_redirects=False)
        log.info("报名返回 status=%s", rr.status_code)
        if rr.status_code == 200 and "提交成功" in rr.text:
            log.info("报名成功")
        return True
    if any(k in html for k in ("开始答题", "preanswer", "答题机会")):
        log.info("已报名，进入答题准备页")
        return True
    return True


def start_quiz(sess: LiveSession, base: str):
    """从当前页（准备页/成绩页）点开始答题。返回 (ok, resp, reason)。"""
    import re
    r = sess.session.get(f"{base}/answer2.html", timeout=20, allow_redirects=True)
    html = r.text
    if parse_question(html):
        return True, r, "题目页"

    m = re.search(r"preanswer1\.html\?vip=(\d+)", html)
    if m:
        pu = f"{base}/preanswer1.html?vip={m.group(1)}"
        # 先 GET 准备页，判断是否还有次数（有无 getq 开始按钮）
        prep = sess.session.get(pu, timeout=20, allow_redirects=False)
        if "开始答题" not in prep.text:
            # 无开始按钮 = 次数用尽（或成绩页）
            if "答题记录" in prep.text or "分数" in prep.text:
                return False, prep, "答题次数已用完（已无「开始答题」按钮）"
        log.info("开始答题: POST getq -> %s", pu)
        rr = sess.session.post(pu, data={"getq": "开始答题"}, timeout=20, allow_redirects=False)
        if rr.status_code == 302 and rr.headers.get("Location"):
            loc = rr.headers["Location"]
            nxt = loc if loc.startswith("http") else f"{base}/{loc.lstrip('/')}"
            rr2 = sess.session.get(nxt, timeout=20, allow_redirects=True)
            if parse_question(rr2.text):
                return True, rr2, "题目页"
        if parse_question(rr.text):
            return True, rr, "题目页"

    rr = sess.session.post(f"{base}/answer2.html", data={"getq": "开始答题"}, timeout=20, allow_redirects=True)
    if parse_question(rr.text):
        return True, rr, "题目页"
    return False, rr, "未进入题目页"


def run(identity_mode: str, max_q: int = 30):
    cfg = load_config()
    base = cfg.base_url
    ck = read_cookie()
    if not ck or "PHPSESSID=" not in ck:
        log.error("未取得微信登录 Cookie。请先在电脑微信内置浏览器打开竞赛页完成授权。")
        return 2
    log.info("Cookie: %s...", ck[:40])

    sess = LiveSession.new(base, ck, WX_UA)
    bank = QuestionBank()

    if identity_mode == "place":
        identity = {"school": "测试大学", "fullname": "测试同学", "mobile": "13800000000",
                    "studentid": "TEST" + str(int(time.time())), "gmajor": "计算机", "sex": "0"}
    else:
        identity = cfg.__dict__.get("identity", {}) or {}

    ensure_registered(sess, base, identity)
    ok, r, reason = start_quiz(sess, base)
    if not ok:
        log.error("无法进入答题页：%s", reason)
        return 3

    answered = 0
    for step in range(max_q):
        q = parse_question(r.text)
        if not q:
            log.info("=== 无题，答题结束/交卷 ===")
            open(os.path.join(LOG_DIR, "auto_result.html"), "w", encoding="utf-8").write(r.text)
            break
        picks = decide(q, bank)
        data = {n: v for n, v in picks}
        data["questionid"] = q.qid
        data["answer"] = "提交"
        log.info("Q%s [%s] %s -> %s", q.answered, q.qtype, q.stem[:26], picks)
        pr = sess.session.post(r.url, data=data, timeout=20, allow_redirects=False)
        if pr.status_code == 302 and pr.headers.get("Location"):
            loc = pr.headers["Location"]
            nxt = loc if loc.startswith("http") else f"{base}/{loc.lstrip('/')}"
            r = sess.session.get(nxt, timeout=20, allow_redirects=True)
        elif "多选题" in pr.text:
            # 多选被拒：补全所有选项重试
            allp = [(o["name"], o["value"]) for o in q.options]
            d2 = {n: v for n, v in allp}; d2["questionid"] = q.qid; d2["answer"] = "提交"
            pr = sess.session.post(r.url, data=d2, timeout=20, allow_redirects=False)
            if pr.status_code == 302 and pr.headers.get("Location"):
                loc = pr.headers["Location"]
                nxt = loc if loc.startswith("http") else f"{base}/{loc.lstrip('/')}"
                r = sess.session.get(nxt, timeout=20, allow_redirects=True)
        else:
            r = pr
        answered += 1
        time.sleep(0.3)

    log.info("已作答 %d 题", answered)
    # 解析成绩
    import re
    m = re.search(r'class="score">([^<]*分[^<]*)<', r.text)
    if m:
        log.info("★ 成绩: %s", m.group(1))
    else:
        open(os.path.join(LOG_DIR, "auto_result.html"), "w", encoding="utf-8").write(r.text)
        log.info("成绩页已保存 logs/auto_result.html")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="一键微信扫码+全自动答题")
    p.add_argument("--qrcode", action="store_true", help="生成扫码登录二维码")
    p.add_argument("--identity", default="place", choices=["place", "config"])
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(os.path.join(LOG_DIR, "auto_live.log"), encoding="utf-8")])
    if args.qrcode:
        # 用能正常打开的入口：mblogin.php -> 微信授权(redirect_uri=mblogin2.php)
        link = "http://www.aids0.com/mblogin.php"
        out = os.path.join(LOG_DIR, "login_qrcode.png")
        make_qrcode(link, out)
        # 保存链接到 txt（方便复制）
        linkfile = os.path.join(LOG_DIR, "login_link.txt")
        with open(linkfile, "w", encoding="utf-8") as f:
            f.write(link)
        # 尝试复制到剪贴板
        copied = False
        try:
            import subprocess
            subprocess.run("clip", input=link.encode("utf-8"), shell=True, check=True)
            copied = True
        except Exception:
            try:
                import win32clipboard  # type: ignore
                win32clipboard.OpenClipboard(); win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(link); win32clipboard.CloseClipboard()
                copied = True
            except Exception:
                copied = False
        print("=" * 60)
        print("  登录链接（可直接复制发给朋友）:")
        print("  " + link)
        print("=" * 60)
        print("二维码图片:", out)
        print("链接文件  :", linkfile)
        print("已复制到剪贴板:", "是 ✅ (直接 Ctrl+V 粘贴)" if copied else "否（请手动复制上面的链接）")
        return 0
    return run(args.identity)


if __name__ == "__main__":
    sys.exit(main())

