"""Selenium 浏览器模式：接管真实浏览器，用户在微信环境完成登录后自动答题。

适用场景：http://www.aids0.com 的答题页仅在微信内置浏览器/已登录态下渲染，
纯 HTTP 抓取需要手动搬运 Cookie。浏览器模式让你：
  1) 启动带微信 UA 的浏览器（或连接已登录的远程调试 Chrome）；
  2) 打开答题入口，人工完成微信授权登录；
  3) 程序自动解析题目、按题库/LLM 作答、点选并交卷。

依赖：pip install selenium。需要本机 Chrome/Edge 与匹配的 webdriver（Selenium 4 自动管理）。

用法：
  python -m app.browser_mode --url "http://www.aids0.com/answer2.php?vip=703404" \
      --remote-debugging-port 9222   # 可选：连接已开调试端口的浏览器
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import logging

from .config import load_config, PROJECT_ROOT
from .parser import parse_questions, Question
from .bank import QuestionBank
from .solver import Solver

log = logging.getLogger("autoquiz.browser")

WECHAT_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.40(0x18002832) NetType/WIFI Language/zh_CN"
)


def get_driver(remote_port: int | None):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    if remote_port:
        opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{remote_port}")
        drv = webdriver.Chrome(options=opts)
    else:
        # 移动端 UA 近似微信环境（注意：仅设置 UA 不等于微信 JS-SDK 授权，登录仍需人工）
        opts.add_argument(f"--user-agent={WECHAT_UA}")
        opts.add_argument("--window-size=420,900")
        drv = webdriver.Chrome(options=opts)
    return drv


def click_option(driver, q: Question) -> bool:
    """在页面中按题干/选项文本定位并点击。返回是否成功。"""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    # 优先按 input value/name 定位
    for label in q.answer_labels:
        opt = next((o for o in q.options if o.label == label), None)
        if not opt:
            continue
        # 1) 按 name+value
        css = f"input[name='{q.qid}'][value='{opt.value}']"
        try:
            el = driver.find_element(By.CSS_SELECTOR, css)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            try:
                el.click()
            except Exception:  # noqa: BLE001
                driver.execute_script("arguments[0].click();", el)
            continue
        except Exception:  # noqa: BLE001
            pass
        # 2) 按可见文本点 label
        try:
            xp = f"//label[contains(normalize-space(.), '{opt.text[:20]}')]"
            el = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, xp)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            el.click()
        except Exception:  # noqa: BLE001
            log.warning("无法点击选项 %s(%s)", q.index, label)
            return False
    return True


def run(url: str, remote_port: int | None, auto_submit: bool, wait_login: int):
    cfg = load_config()
    try:
        driver = get_driver(remote_port)
    except Exception as exc:  # noqa: BLE001
        log.error("浏览器启动失败: %s (请确认已安装 Chrome 与 selenium)", exc)
        return 2

    log.info("打开答题页: %s", url)
    driver.get(url)

    # 等待登录 + 题目渲染
    log.info("请在浏览器中完成微信登录，脚本将在 %d 秒内等待题目出现……", wait_login)
    qs = []
    deadline = time.time() + wait_login
    while time.time() < deadline:
        html = driver.page_source
        if any(m in html for m in ("请先登录", "请在微信客户端打开链接")):
            time.sleep(3)
            continue
        qs = parse_questions(html)
        if qs:
            break
        time.sleep(3)

    if not qs:
        log.error("未检测到题目，可能未登录或页面结构变化。")
        return 3
    log.info("检测到 %d 道题，开始作答", len(qs))

    bank = QuestionBank()
    solver = Solver(cfg, bank)
    qs = solver.solve_all(qs)

    ok_count = 0
    for q in qs:
        if q.answer_value is None:
            continue
        if click_option(driver, q):
            ok_count += 1
        time.sleep(0.3)
    log.info("已点选 %d/%d 题", ok_count, len(qs))

    if auto_submit:
        from selenium.webdriver.common.by import By
        submitted = False
        for sel in ("input[type='submit']", "button[type='submit']",
                    "xpath://*[contains(text(),'交卷')]",
                    "xpath://*[contains(text(),'提交')]"):
            try:
                if sel.startswith("xpath:"):
                    el = driver.find_element(By.XPATH, sel[6:])
                else:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                el.click()
                submitted = True
                log.info("已点击交卷按钮")
                break
            except Exception:  # noqa: BLE001
                continue
        if not submitted:
            log.warning("未找到交卷按钮，请手动点击。")
    else:
        log.info("已按配置不自动交卷（--submit 可开启）。请在浏览器确认后手动交卷。")

    log.info("完成。浏览器将保持打开 60 秒供核对。")
    time.sleep(60)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Selenium 浏览器自动答题模式")
    p.add_argument("--url", default="http://www.aids0.com/answer2.php?vip=703404")
    p.add_argument("--remote-debugging-port", type=int, default=None)
    p.add_argument("--submit", action="store_true", help="自动交卷")
    p.add_argument("--wait-login", type=int, default=180, help="等待登录/题目出现的秒数")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    os.makedirs(os.path.join(PROJECT_ROOT, "logs"), exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(os.path.join(PROJECT_ROOT, "logs", "browser.log"), encoding="utf-8")],
    )
    return run(args.url, args.remote_debugging_port, args.submit, args.wait_login)


if __name__ == "__main__":
    sys.exit(main())
