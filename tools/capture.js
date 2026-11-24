// 抓包 + 接管：连接已开调试端口的 Chrome，打开答题入口，记录所有网络请求/响应。
// 用法: node tools/capture.js [url]
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const OUT = path.join(__dirname, '..', 'logs', 'capture');
fs.mkdirSync(OUT, { recursive: true });

const START_URL = process.argv[2] || 'http://www.aids0.com/answer2.html?vip=703404';

function safe(s, n = 4000) {
  if (s == null) return '';
  s = String(s);
  return s.length > n ? s.slice(0, n) + `\n...[truncated ${s.length - n} bytes]` : s;
}

(async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const ctx = browser.contexts()[0] || (await browser.newContext());
  let page = ctx.pages()[0] || (await ctx.newPage());

  const log = [];
  const logFile = path.join(OUT, 'requests.log');
  const append = (obj) => {
    log.push(obj);
    fs.appendFileSync(logFile, JSON.stringify(obj) + '\n', 'utf-8');
  };

  // 监听请求
  page.on('request', (req) => {
    const t = req.resourceType();
    if (['xhr', 'fetch', 'document', 'script'].includes(t)) {
      append({ phase: 'request', ts: Date.now(), method: req.method(), url: req.url(),
               type: t, postData: safe(req.postData(), 8000),
               headers: req.headers() });
    }
  });

  // 监听响应
  page.on('response', async (resp) => {
    const t = resp.request().resourceType();
    if (!['xhr', 'fetch', 'document'].includes(t)) return;
    let body = '';
    try { body = await resp.text(); } catch (e) { body = `[body read err: ${e.message}]`; }
    append({ phase: 'response', ts: Date.now(), status: resp.status(), url: resp.url(),
             type: t, body: safe(body, 20000) });
    // 命中的接口单独落盘
    const u = resp.url();
    if (/answer|quiz|exam|score|submit|\.php/i.test(u)) {
      const fn = path.join(OUT, 'resp_' + u.replace(/[^a-zA-Z0-9]/g, '_').slice(-80) + '.txt');
      try { fs.writeFileSync(fn, body, 'utf-8'); } catch (e) {}
    }
  });

  page.on('console', (m) => append({ phase: 'console', ts: Date.now(), type: m.type(), text: safe(m.text(), 1000) }));

  console.log('capture started. current url:', page.url());
  console.log('opening:', START_URL);
  try { await page.goto(START_URL, { waitUntil: 'domcontentloaded', timeout: 30000 }); } catch (e) { console.log('goto err:', e.message); }
  console.log('now at:', page.url());
  console.log('title:', await page.title());
  const html = await page.content();
  fs.writeFileSync(path.join(OUT, 'current_page.html'), html, 'utf-8');
  console.log('page html saved:', html.length, 'bytes');
  console.log('cookies:', (await ctx.cookies()).map(c => `${c.name}=${c.value}`.slice(0, 80)).join(' | '));

  // 保持监听 10 分钟（你在这期间操作登录/答题）
  console.log('>>> 保持监听中，请在浏览器窗口完成登录并进入答题页。10 分钟后或按 Ctrl+C 结束。');
  await new Promise(r => setTimeout(r, 10 * 60 * 1000));
  console.log('capture done. requests:', log.length);
  await browser.close(); // 只断开 CDP，不关闭 Chrome
})();
