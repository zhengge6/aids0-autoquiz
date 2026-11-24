"""
wx_cookie.py —— 纯 Python 提取微信 WebView 里的 aids0 登录 Cookie（PHPSESSID）。

「图书馆式」原理（全 Python 实现，不依赖外部 ps1/命令行）：
  1. ctypes 调 WMI(COM) Win32_ShadowCopy 创建 C: 卷影副本  -> 绕过微信对 Cookies 库的独占锁
  2. 从影子路径复制出 Cookies SQLite 库
  3. 读 Local State 的 os_crypt.encrypted_key (base64, 前缀 'DPAPI')
  4. ctypes.CryptUnprotectData (DPAPI, 当前用户) 解出 AES-256 主 key
  5. AES-256-GCM 解密每条 encrypted_value，剥离微信固定二进制头 -> 真实值
  6. 输出 PHPSESSID 并可写回 config.json

用法:
  python wx_cookie.py            # 提取（若已缓存则直接解密）
  python wx_cookie.py fresh      # 强制重建卷影副本再提取
  python wx_cookie.py --whoami   # 只打印账号信息
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import sqlite3
import sys
import tempfile

# ---------- 依赖检查 ----------
try:
    from Crypto.Cipher import AES
except ImportError:
    print("[!] 缺少 pycryptodome：pip install pycryptodome")
    sys.exit(1)

WECHAT_ROOT = os.path.join(os.environ["APPDATA"], "Tencent", "xwechat", "radium", "web")
LOCAL_STATE = os.path.join(WECHAT_ROOT, "Local State")
PROFILE_GLOB_ROOT = os.path.join(WECHAT_ROOT, "profiles")


def _app_dir() -> str:
    """获取程序所在目录（兼容 PyInstaller 打包：取 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _app_dir()
CACHE_DIR = os.path.join(BASE_DIR, "logs", "wxck")
CACHE_DB = os.path.join(CACHE_DIR, "Cookies")
LOG_DIR = os.path.join(BASE_DIR, "logs")
AIDS0_HOSTS = ("aids0.com",)

# ============================================================
# 1) 管理员提权 & 卷影副本 (ctypes + WMI via COM 太繁琐，改用 vssadmin? 不，用 pywin32 WMI)
# ============================================================

def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def elevate():
    """以管理员重新启动自身（UAC 弹窗），并返回是否已发起提权。"""
    if is_admin():
        return False
    script = os.path.abspath(sys.argv[0])
    extra = " ".join(sys.argv[1:])
    r = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, f'"{script}" {extra}', None, 1)
    if r <= 32:
        print("[!] 提权失败或被拒绝 (code=%s)" % r)
        return False
    return True


def create_shadow_copy() -> str | None:
    """用 WMI Win32_ShadowCopy.Create 创建 C: 卷影副本（通过 ExecMethod 正确带参）。"""
    try:
        import win32com.client
        locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        svc = locator.ConnectServer(".", "root\\cimv2")
        cls = svc.Get("Win32_ShadowCopy")
        in_params = cls.Methods_("Create").InParameters.SpawnInstance_()
        in_params.Volume = "C:\\"
        in_params.Context = "ClientAccessible"
        print("[vss] ExecMethod Create(ClientAccessible) ...")
        out = svc.ExecMethod("Win32_ShadowCopy", "Create", in_params)
        ret = out.ReturnValue
        print(f"[vss] ReturnValue={ret}")
        if ret != 0:
            in_params.Context = "Backup"
            print("[vss] 回退 Backup context ...")
            out = svc.ExecMethod("Win32_ShadowCopy", "Create", in_params)
            ret = out.ReturnValue
            print(f"[vss] Backup ReturnValue={ret}")
            if ret != 0:
                return None
        shadow_id = out.ShadowID
        shadows = svc.ExecQuery(f"SELECT * FROM Win32_ShadowCopy WHERE ID='{shadow_id}'")
        for sc in shadows:
            print("[vss] device:", sc.DeviceObject)
            return sc.DeviceObject
    except Exception as e:
        import traceback
        print("[vss] 异常:", e)
        traceback.print_exc()
    return None


def delete_shadow_copy(device_object: str):
    try:
        import win32com.client
        locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        svc = locator.ConnectServer(".", "root\\cimv2")
        shadows = svc.ExecQuery("SELECT * FROM Win32_ShadowCopy")
        for sc in shadows:
            if sc.DeviceObject == device_object:
                sc.Delete_()
                break
    except Exception:
        pass


def find_profile_cookie_rel() -> list[str]:
    """返回各 profile 相对 C: 的 Cookies 路径列表（用于影子路径拼接）。"""
    rels = []
    if not os.path.isdir(PROFILE_GLOB_ROOT):
        return rels
    for name in os.listdir(PROFILE_GLOB_ROOT):
        p = os.path.join(PROFILE_GLOB_ROOT, name, "Network", "Cookies")
        if os.path.exists(p):
            # C:\Users\... -> \Users\...
            rels.append((name, p[2:]))
    return rels


def refresh_cookie_copy():
    os.makedirs(CACHE_DIR, exist_ok=True)
    rels = find_profile_cookie_rel()
    print(f"[copy] 找到 {len(rels)} 个 profile Cookies 库")
    if not rels:
        print("[!] 未找到微信 Cookies 库")
        return False
    dev = create_shadow_copy()
    if not dev:
        return False
    got = False
    for name, rel in rels:
        src = f"{dev}\\{rel}"
        dst = os.path.join(CACHE_DIR, name + ".db")
        try:
            if os.path.exists(src):
                with open(src, "rb") as fi, open(dst, "wb") as fo:
                    fo.write(fi.read())
                sz = os.path.getsize(dst)
                print(f"[copy] {name}: {sz} bytes")
                if sz > 0:
                    got = True
            else:
                print(f"[copy] 影子路径不存在: {src}")
        except Exception as e:
            print(f"[!] 复制 {name} 失败: {e}")
    delete_shadow_copy(dev)
    return got


# ============================================================
# 2) DPAPI + AES-GCM 解密
# ============================================================

class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_decrypt(data: bytes) -> bytes:
    """ctypes 调 CryptUnprotectData（当前用户 DPAPI）。"""
    buf_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    buf_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(buf_in), None, None, None, None, 0, ctypes.byref(buf_out)
    ):
        raise OSError("CryptUnprotectData failed")
    out = ctypes.string_at(buf_out.pbData, buf_out.cbData)
    ctypes.windll.kernel32.LocalFree(buf_out.pbData)
    return out


def get_master_key() -> bytes:
    cfg = json.load(open(LOCAL_STATE, encoding="utf-8"))
    ek = base64.b64decode(cfg["os_crypt"]["encrypted_key"])
    if ek[:5] != b"DPAPI":
        raise ValueError("encrypted_key 非 DPAPI 标记")
    return dpapi_decrypt(ek[5:])


def aes_gcm_decrypt(enc: bytes, key: bytes) -> bytes:
    if not enc:
        return b""
    if enc[:3] in (b"v10", b"v11"):
        nonce, ct_tag = enc[3:15], enc[15:]
        ct, tag = ct_tag[:-16], ct_tag[-16:]
        return AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ct, tag)
    # 明文
    return enc


def strip_wx_prefix(b: bytes) -> str:
    """微信 cookie 明文 = 二进制头 + 真实值(可打印 ASCII)。从尾部截取连续可打印串。"""
    i = len(b) - 1
    while i >= 0 and 0x20 <= b[i] <= 0x7E:
        i -= 1
    s = b[i + 1:].decode("ascii", "replace")
    if s.startswith("%") and len(s) > 1:
        s = s[1:]
    return s


# ============================================================
# 3) 读取库并导出 aids0 cookie
# ============================================================

def read_aids0_cookies(db_path: str, key: bytes) -> list[tuple[str, str]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=5)
    try:
        rows = conn.execute(
            "select host_key,name,value,encrypted_value from cookies where host_key like '%aids0%'"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    conn.close()
    out = []
    for host, name, val, enc in rows:
        real = val if val else strip_wx_prefix(aes_gcm_decrypt(enc, key))
        if real:
            out.append((name, real))
    return out


def collect_cookie() -> str:
    """遍历缓存里的所有 profile db，返回 aids0 cookie 串。"""
    key = get_master_key()
    best = {}
    for fn in os.listdir(CACHE_DIR):
        if not fn.endswith(".db"):
            continue
        try:
            pairs = read_aids0_cookies(os.path.join(CACHE_DIR, fn), key)
        except Exception:
            continue
        for n, v in pairs:
            if n == "PHPSESSID" or n not in best:
                best[n] = v
    if "PHPSESSID" not in best:
        return ""
    # 组装：PHPSESSID 优先，其余附带
    parts = [f"{k}={v}" for k, v in best.items()]
    return "; ".join(parts)


def write_config(cookie: str):
    cfgp = os.path.join(BASE_DIR, "config.json")
    if not os.path.exists(cfgp):
        return
    try:
        cfg = json.load(open(cfgp, encoding="utf-8"))
        cfg.setdefault("login", {})["cookie"] = cookie
        json.dump(cfg, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_cookie(force=False, verbose=True) -> str:
    """取 cookie：优先缓存，必要时（有权限时）重建副本再解密。返回 cookie 串或 ''。
    不 sys.exit、不阻塞（无权限且无缓存时返回 ''）。"""
    def _has_cache():
        return os.path.isdir(CACHE_DIR) and any(f.endswith(".db") for f in os.listdir(CACHE_DIR))

    if (force or not _has_cache()) and is_admin():
        os.makedirs(CACHE_DIR, exist_ok=True)
        if verbose:
            print("[*] 创建卷影副本并复制 Cookies 库 ...")
        refresh_cookie_copy()
    cookie = collect_cookie()
    if cookie:
        outp = os.path.join(LOG_DIR, "wx_aids0_cookie.txt")
        os.makedirs(LOG_DIR, exist_ok=True)
        open(outp, "w", encoding="utf-8").write(cookie)
        write_config(cookie)
    return cookie


def main():
    args = sys.argv[1:]
    force = "fresh" in args

    def _has_cache():
        return os.path.isdir(CACHE_DIR) and any(f.endswith(".db") for f in os.listdir(CACHE_DIR))

    # 需要重建副本且非管理员 -> 提权重启自身
    if (force or not _has_cache()) and not is_admin():
        print("[*] 需要管理员权限访问微信 Cookies 库，正在提权（请在弹出的 UAC 窗口点「是」）...")
        if elevate():
            print("[*] 已在管理员窗口运行。完成后重新运行本命令即可读取 Cookie。")
            return

    logpath = os.path.join(LOG_DIR, "wx_cookie_run.log")
    os.makedirs(LOG_DIR, exist_ok=True)
    if is_admin():
        try:
            _lf = open(logpath, "w", encoding="utf-8", buffering=1)
            sys.stdout = _lf
            sys.stderr = _lf
        except Exception:
            pass

    cookie = get_cookie(force=force, verbose=True)
    if not cookie:
        print("[!] 未提取到 aids0 PHPSESSID。请先在微信里打开过 aids0 站点并登录。")
        sys.exit(2)
    print("\n=== cookie ===")
    for p in cookie.split("; "):
        print("   " + p)
    print("\n[+] saved", os.path.join(LOG_DIR, "wx_aids0_cookie.txt"))


if __name__ == "__main__":
    main()
