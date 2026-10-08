#!/usr/bin/env python3
"""
iptv-speedtest: 定时拉取直播源 -> 逐条测速 -> 每个频道保留码率最高且能流畅播放的 N 条
-> 局域网提供 /m3u /txt 订阅，并带网页设置面板。纯标准库，无第三方依赖。

所有设置在网页里改，保存到 /data/config.json。环境变量只在首次启动时作为默认值：
  SOURCES, INTERVAL_HOURS, KEEP, WORKERS, TIMEOUT, PORT, DATA_DIR
  ADMIN_PASSWORD  设置后，修改设置/触发测速需要输入此密码（订阅地址不受影响）
"""
import base64, ipaddress, json, os, re, socket, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urljoin, urlparse

PORT = int(os.getenv("PORT", "8080"))
DATA = os.getenv("DATA_DIR", "/data")
PASSWORD = os.getenv("ADMIN_PASSWORD", "")
CFG_PATH = os.path.join(DATA, "config.json")
UA = "Mozilla/5.0 (iptv-speedtest)"
MAX_SEG = 8 * 1024 * 1024
SAFE = ":/?&=%#@+,;~!$'()*[]"   # 链接里的中文等字符转义，其余保持原样
os.makedirs(DATA, exist_ok=True)

DEFAULT_CFG = {
    "sources": [{"url": u.strip(), "name": "", "enabled": True}
                for u in os.getenv("SOURCES", "https://raw.githubusercontent.com/zknjjjx/iptv/main/iptv.m3u").split(",") if u.strip()],
    "group_mode": "rules",          # rules = 按下面规则分组；source = 保留源里的原始分组
    "group_rules": [
        {"group": "央视频道", "keywords": "CCTV,CETV,CGTN,央视"},
        {"group": "卫视频道", "keywords": "卫视"},
        {"group": "港澳台", "keywords": "凤凰,翡翠,TVB,明珠,星空,澳门,台视,中视,华视,民视,东森,中天,三立,纬来"},
        {"group": "数字付费", "keywords": "剧场,影院,电影,CHC,求索,纪实,风云,兵器,怀旧,文化精品,女性时尚"},
        {"group": "少儿动画", "keywords": "少儿,卡通,动漫,动画,哈哈,炫动,金鹰卡通,优漫"},
    ],
    "unmatched_group": "地方频道",   # 规则都没匹配上的频道放这里；留空 = 用源里的原始分组
    "blacklist": "购物,测试,广告",   # 频道名或链接含这些词就丢弃
    "interval_hours": float(os.getenv("INTERVAL_HOURS", "12")),
    "keep": int(os.getenv("KEEP", "1")),
    "workers": int(os.getenv("WORKERS", "32")),
    "timeout": float(os.getenv("TIMEOUT", "8")),
    "smooth_ratio": 1.2,
    "min_kbps": 0,
    "min_height": 0,                # 最低分辨率（高度），0 = 不限
    "keep_unknown_res": True,       # 测不出分辨率的是否保留
    "ip_version": "all",            # all / ipv4 / ipv6
    "skip_vod": True,               # 丢弃 mp4 等点播占位视频
    "merge_names": True,            # 合并 "CCTV-1 高清" "CCTV1" 这类同名频道
    "epg_url": "https://live.fanmingming.cn/e.xml",
    "run_on_start": False,          # 容器启动后是否立即测速（默认否，等到点或手动）
}

lock = threading.Lock()
state = {"status": "idle", "progress": 0, "total": 0, "last_run": None,
         "channels": 0, "tested": 0, "alive": 0, "duration_s": 0, "next_run": None,
         "source_stats": {}, "cur_stats": {}}
refresh_evt = threading.Event()
stop_evt = threading.Event()
RUN_ID = 0
STATE_PATH = os.path.join(DATA, "state.json")
try:
    state.update({k: v for k, v in json.load(open(STATE_PATH, encoding="utf-8")).items()
                  if k in ("last_run", "last_ts", "channels", "tested", "alive", "duration_s", "source_stats")})
except Exception:
    pass
state["status"] = "空闲"


def save_state():
    try:
        json.dump({k: state.get(k) for k in ("last_run", "last_ts", "channels", "tested", "alive", "duration_s", "source_stats")},
                  open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def load_cfg():
    cfg = json.loads(json.dumps(DEFAULT_CFG))
    if os.path.exists(CFG_PATH):
        try:
            cfg.update(json.load(open(CFG_PATH, encoding="utf-8")))
        except Exception as e:
            log("配置文件损坏，使用默认值", e)
    return cfg


def save_cfg(cfg):
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CFG_PATH)


CFG = load_cfg()
if not os.path.exists(CFG_PATH):
    save_cfg(CFG)


def fetch(url, timeout, limit=None, max_time=None):
    req = urllib.request.Request(quote(url, safe=SAFE), headers={"User-Agent": UA})
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        buf = bytearray()
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            buf += chunk
            if (limit and len(buf) >= limit) or time.time() - t > (max_time or timeout * 3):
                break
    return bytes(buf), max(time.time() - t, 0.001)


def source_candidates(url):
    """源地址 + 备用地址：GitHub 代理前缀失败时直连 raw，再试 jsDelivr 镜像"""
    cands = [url]
    m = re.search(r"https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/(?:refs/heads/)?([^/]+)/(.+)", url)
    if m:
        raw = m.group(0)
        if raw != url:
            cands.append(raw)
        u, r, br, path = m.groups()
        cands += [f"https://fastly.jsdelivr.net/gh/{u}/{r}@{br}/{path}", f"https://cdn.jsdelivr.net/gh/{u}/{r}@{br}/{path}"]
    return cands


def fetch_source(url, timeout=20):
    """依次尝试源地址和备用地址，每个重试 2 次。返回 (文本, 实际使用的地址, 用时)"""
    errs = []
    for c in source_candidates(url):
        for _ in range(2):
            if stop_evt.is_set():
                raise RuntimeError("已停止")
            try:
                data, dt = fetch(c, timeout, max_time=120)
                text = data.decode("utf-8", "ignore")
                if len(text) < 50 or text.lstrip().startswith("<"):
                    raise RuntimeError("内容不是直播源（可能被拦截）")
                return text, c, dt
            except Exception as e:
                errs.append(f"{urlparse(c).hostname}: {str(e)[:60]}")
    raise RuntimeError("；".join(errs[-3:]))


def fetch_info(url, timeout, limit=None, max_time=None):
    """同 fetch，另外返回跳转后的最终地址和 Content-Type"""
    req = urllib.request.Request(quote(url, safe=SAFE), headers={"User-Agent": UA})
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        final, ctype = r.geturl(), (r.headers.get("Content-Type") or "").lower()
        buf = bytearray()
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            buf += chunk
            if (limit and len(buf) >= limit) or time.time() - t > (max_time or timeout * 3):
                break
    return bytes(buf), max(time.time() - t, 0.001), final, ctype


def media_duration(data):
    """从 TS 的 PCR 或 FLV 标签时间戳算出这段数据的播放时长（秒），算不出返回 0"""
    try:
        if data[:3] == b"FLV":
            i, ts = 13, []
            while i + 11 <= len(data):
                size = int.from_bytes(data[i + 1:i + 4], "big")
                if data[i] in (8, 9):
                    ts.append(int.from_bytes(data[i + 4:i + 7], "big") | (data[i + 7] << 24))
                i += 11 + size + 4
            return (max(ts) - min(ts)) / 1000 if len(ts) > 2 else 0
        s = data.find(b"\x47")
        while s != -1 and s + 376 < len(data) and not (data[s + 188] == 0x47 and data[s + 376] == 0x47):
            s = data.find(b"\x47", s + 1)
        if s == -1:
            return 0
        pcr = {}
        for i in range(s, len(data) - 187, 188):
            pk = data[i:i + 188]
            if pk[0] == 0x47 and (pk[3] & 0x20) and pk[4] >= 7 and (pk[5] & 0x10):
                pid = ((pk[1] & 0x1F) << 8) | pk[2]
                v = int.from_bytes(pk[6:11], "big") >> 7
                pcr.setdefault(pid, []).append(v / 90000)
        if not pcr:
            return 0
        v = max(pcr.values(), key=len)
        d = v[-1] - v[0]
        return d if 0 < d < 3600 else 0
    except Exception:
        return 0


# ---------------- 解析 ----------------
def parse(text):
    """返回 [(name, group, logo, url)]，兼容 m3u 和 txt"""
    out = []
    if "#EXTINF" in text:
        lines = text.splitlines()
        for i, l in enumerate(lines):
            if not l.startswith("#EXTINF"):
                continue
            name = l.rsplit(",", 1)[-1].strip()
            g = re.search(r'group-title="([^"]*)"', l)
            lg = re.search(r'tvg-logo="([^"]*)"', l)
            for j in range(i + 1, min(i + 4, len(lines))):
                u = lines[j].strip()
                if u and not u.startswith("#"):
                    out.append((name, g.group(1) if g else "", lg.group(1) if lg else "", u))
                    break
    else:
        group = ""
        for l in text.splitlines():
            if "," not in l:
                continue
            name, u = l.split(",", 1)
            if u.strip() == "#genre#":
                group = name.strip()
                continue
            for one in u.split("#"):
                if one.strip().startswith(("http", "rtmp", "rtsp")):
                    out.append((name.strip(), group, "", one.strip()))
    return out


def norm(name, merge):
    if not merge:
        return name
    n = name.upper().replace(" ", "").replace("-", "").replace("_", "")
    return re.sub(r"(高清|超清|标清|蓝光|HD|FHD|UHD|4K|8K|1080P?|720P?|\[.*?\]|\(.*?\)|（.*?）)+$", "", n) or name


def ip_kind(url):
    host = urlparse(url).hostname or ""
    try:
        return "ipv6" if ipaddress.ip_address(host).version == 6 else "ipv4"
    except ValueError:
        return "domain"


def pick_group(name, orig, cfg):
    if cfg["group_mode"] == "source":
        return orig or cfg["unmatched_group"] or "其他"
    up = name.upper()
    for r in cfg["group_rules"]:
        for k in re.split(r"[,，\s]+", r.get("keywords", "")):
            if k and k.upper() in up:
                return r["group"]
    return cfg["unmatched_group"] or orig or "其他"


# ---------------- 测速 ----------------
# ---------------- 分辨率解析（H.264 / H.265 SPS） ----------------
class _Bits:
    def __init__(self, b):
        self.b, self.p = b, 0
    def u(self, n):
        v = 0
        for _ in range(n):
            byte = self.b[self.p >> 3] if (self.p >> 3) < len(self.b) else 0
            v = (v << 1) | ((byte >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v
    def ue(self):
        z = 0
        while self.u(1) == 0:
            z += 1
            if z > 31:
                raise ValueError("bad ue")
        return (1 << z) - 1 + self.u(z)
    def se(self):
        v = self.ue()
        return (v + 1) // 2 if v & 1 else -(v // 2)


def _rbsp(b):
    return b.replace(b"\x00\x00\x03", b"\x00\x00")


def _sps264(nal):
    r = _Bits(_rbsp(nal[1:80]))
    prof = r.u(8); r.u(16); r.ue()
    cf = 1
    if prof in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        cf = r.ue()
        if cf == 3:
            r.u(1)
        r.ue(); r.ue(); r.u(1)
        if r.u(1):
            for i in range(8 if cf != 3 else 12):
                if r.u(1):
                    last = nxt = 8
                    for _ in range(16 if i < 6 else 64):
                        if nxt:
                            nxt = (last + r.se() + 256) % 256
                        last = nxt or last
    r.ue()
    t = r.ue()
    if t == 0:
        r.ue()
    elif t == 1:
        r.u(1); r.se(); r.se()
        for _ in range(r.ue()):
            r.se()
    r.ue(); r.u(1)
    w, h = r.ue() + 1, r.ue() + 1
    fmo = r.u(1)
    if not fmo:
        r.u(1)
    r.u(1)
    cl = cr = ct = cb = 0
    if r.u(1):
        cl, cr, ct, cb = r.ue(), r.ue(), r.ue(), r.ue()
    sx, sy = (1, 2 - fmo) if cf == 0 else (2 if cf in (1, 2) else 1, (2 if cf == 1 else 1) * (2 - fmo))
    return w * 16 - sx * (cl + cr), (2 - fmo) * h * 16 - sy * (ct + cb)


def _sps265(nal):
    r = _Bits(_rbsp(nal[2:120]))
    r.u(4); msl = r.u(3); r.u(1)
    r.u(96)   # general profile_tier_level (88) + general_level_idc (8)
    pp, lp = [], []
    for _ in range(msl):
        pp.append(r.u(1)); lp.append(r.u(1))
    if msl > 0:
        for _ in range(msl, 8):
            r.u(2)
    for i in range(msl):
        if pp[i]:
            r.u(88)
        if lp[i]:
            r.u(8)
    r.ue()
    cf = r.ue()
    if cf == 3:
        r.u(1)
    w, h = r.ue(), r.ue()
    if r.u(1):
        sx, sy = (2, 2) if cf == 1 else ((2, 1) if cf == 2 else (1, 1))
        l, rr, t, b = r.ue(), r.ue(), r.ue(), r.ue()
        w, h = w - sx * (l + rr), h - sy * (t + b)
    return w, h


def _try(nal):
    out = []
    if not nal:
        return out
    h = nal[0]
    if h & 0x80:
        return out
    try:
        if h & 0x1F == 7:
            out.append(_sps264(nal))
    except Exception:
        pass
    try:
        if (h >> 1) & 0x3F == 33:
            out.append(_sps265(nal))
    except Exception:
        pass
    return [(w, h2) for w, h2 in out if 64 <= w <= 8192 and 64 <= h2 <= 4320]


def _scan_es(es, found):
    i = es.find(b"\x00\x00\x01")
    while i != -1 and len(found) < 3:
        found += _try(es[i + 3:i + 3 + 200])
        i = es.find(b"\x00\x00\x01", i + 3)


def _ts_es(data):
    """MPEG-TS 去包头，按 PID 拼接负载"""
    s = data.find(b"\x47")
    while s != -1 and s + 376 < len(data) and not (data[s + 188] == 0x47 and data[s + 376] == 0x47):
        s = data.find(b"\x47", s + 1)
    if s == -1 or s + 376 >= len(data):
        return None
    pids = {}
    for i in range(s, len(data) - 187, 188):
        pk = data[i:i + 188]
        if pk[0] != 0x47:
            continue
        pid = ((pk[1] & 0x1F) << 8) | pk[2]
        afc = (pk[3] >> 4) & 3
        off = 4
        if afc & 2:
            off += 1 + pk[4]
        if afc & 1 and off < 188:
            pids.setdefault(pid, bytearray()).extend(pk[off:])
    return [bytes(v) for v in pids.values()]


def detect_res(data, sdp=""):
    found = []
    try:
        for b64 in re.findall(r"sprop-parameter-sets=([A-Za-z0-9+/=]+)", sdp or ""):
            found += _try(base64.b64decode(b64 + "=="))
        for b64 in re.findall(r"sprop-sps=([A-Za-z0-9+/=]+)", sdp or ""):
            found += _try(base64.b64decode(b64 + "=="))
        if not found and data:
            if data[:3] == b"FLV":
                for m in re.finditer(rb"\xff\xe1(..)", data[:2000000]):
                    n = int.from_bytes(m.group(1), "big")
                    found += _try(data[m.end():m.end() + n])
                    if found:
                        break
            if not found:
                streams = _ts_es(data)
                for es in (streams if streams else [data]):
                    _scan_es(es, found)
                    if found:
                        break
    except Exception:
        pass
    if not found:
        return ""
    w, h = max(set(found), key=found.count)
    return f"{w}x{h}"


def rtp_payload(raw):
    """RTSP TCP 交织数据 -> 视频负载（MP2T 原样拼接；H.264/H.265 加起始码）"""
    out, i = bytearray(), 0
    while i + 4 <= len(raw):
        if raw[i] != 0x24:
            j = raw.find(b"$", i + 1)
            if j == -1:
                break
            i = j
            continue
        ch, n = raw[i + 1], int.from_bytes(raw[i + 2:i + 4], "big")
        pkt = raw[i + 4:i + 4 + n]
        i += 4 + n
        if ch != 0 or len(pkt) < 12 or (pkt[0] >> 6) != 2:
            continue
        off = 12 + 4 * (pkt[0] & 0x0F)
        if pkt[0] & 0x10 and len(pkt) >= off + 4:
            off += 4 + 4 * int.from_bytes(pkt[off + 2:off + 4], "big")
        pl = pkt[off:]
        if not pl:
            continue
        if pkt[1] & 0x7F == 33:
            out += pl
        elif pl[0] & 0x1F == 24:      # H.264 STAP-A
            k = 1
            while k + 2 <= len(pl):
                m = int.from_bytes(pl[k:k + 2], "big")
                out += b"\x00\x00\x01" + pl[k + 2:k + 2 + m]
                k += 2 + m
        elif (pl[0] >> 1) & 0x3F == 48:   # H.265 AP
            k = 2
            while k + 2 <= len(pl):
                m = int.from_bytes(pl[k:k + 2], "big")
                out += b"\x00\x00\x01" + pl[k + 2:k + 2 + m]
                k += 2 + m
        else:
            out += b"\x00\x00\x01" + pl
    return bytes(out)


def F(reason):
    return {"fail": reason}


def err_text(e):
    if hasattr(e, "reason") and not hasattr(e, "code"):
        e = e.reason if isinstance(e.reason, Exception) else Exception(str(e.reason))
    t = str(e)
    if isinstance(e, (socket.timeout, TimeoutError)) or "timed out" in t:
        return "超时"
    m = re.search(r"HTTP Error (\d+)", t)
    if m:
        return f"HTTP {m.group(1)}"
    for k, v in (("refused", "连接被拒绝"), ("Name or service", "域名解析失败"), ("getaddrinfo", "域名解析失败"),
                 ("unreachable", "网络不可达"), ("reset", "连接被重置"), ("SSL", "SSL 错误"), ("CERTIFICATE", "证书错误"),
                 ("连接被关闭", "连接被关闭")):
        if k in t:
            return v
    return t[:60] or type(e).__name__


def rtsp_probe(url, cfg, seconds=4):
    """RTSP：DESCRIBE -> SETUP(TCP 交织) -> PLAY，接收几秒数据算码率；支持 301/302 跳转"""
    to = min(cfg["timeout"], 8)
    for _ in range(4):
        p = urlparse(url)
        sock = socket.create_connection((p.hostname, p.port or 554), timeout=to)
        sock.settimeout(to)
        buf = bytearray()
        cseq = [0]

        def req(method, u, extra=""):
            cseq[0] += 1
            sock.sendall(f"{method} {u} RTSP/1.0\r\nCSeq: {cseq[0]}\r\nUser-Agent: {UA}\r\n{extra}\r\n".encode())
            while b"\r\n\r\n" not in buf:
                # 跳过 PLAY 前后可能出现的交织数据包
                while buf[:1] == b"$" and len(buf) >= 4 and len(buf) >= 4 + int.from_bytes(buf[2:4], "big"):
                    del buf[:4 + int.from_bytes(buf[2:4], "big")]
                if b"\r\n\r\n" in buf:
                    break
                d = sock.recv(65536)
                if not d:
                    raise ConnectionError("连接被关闭")
                buf.extend(d)
            i = buf.index(b"RTSP/") if b"RTSP/" in buf else 0
            head, _, rest = bytes(buf[i:]).partition(b"\r\n\r\n")
            lines = head.decode("utf-8", "ignore").split("\r\n")
            code = int(lines[0].split()[1])
            hdr = {l.split(":", 1)[0].strip().lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
            n = int(hdr.get("content-length", 0))
            while len(rest) < n:
                d = sock.recv(65536)
                if not d:
                    break
                rest += d
            buf[:] = rest[n:]
            return code, hdr, rest[:n].decode("utf-8", "ignore")

        try:
            code, hdr, sdp = req("DESCRIBE", url, "Accept: application/sdp\r\n")
            if code in (301, 302, 303, 307) and hdr.get("location"):
                url = hdr["location"]
                continue
            if code != 200:
                return F(f"RTSP DESCRIBE 返回 {code}")
            base = hdr.get("content-base") or hdr.get("content-location") or url
            as_kbps, ctrl, in_media = 0, None, False
            for l in sdp.splitlines():
                l = l.strip()
                if l.startswith("m="):
                    if ctrl is not None:
                        break
                    in_media = True
                elif in_media and l.startswith("b=AS:"):
                    as_kbps = int(re.sub(r"\D", "", l[5:]) or 0)
                elif in_media and l.startswith("a=control:"):
                    ctrl = l[10:]
            if not ctrl or ctrl == "*":
                track = base
            elif ctrl.startswith("rtsp://"):
                track = ctrl
            else:
                track = base.rstrip("/") + "/" + ctrl
            code, hdr, _ = req("SETUP", track, "Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n")
            if code != 200:
                return F(f"RTSP SETUP 返回 {code}")
            sess = hdr.get("session", "").split(";")[0]
            code, hdr, _ = req("PLAY", base, f"Session: {sess}\r\nRange: npt=0.000-\r\n")
            if code != 200:
                return F(f"RTSP PLAY 返回 {code}")
            got, t0 = len(buf), time.time()
            keep = bytearray(buf)
            while time.time() - t0 < seconds and not stop_evt.is_set():
                sock.settimeout(max(0.5, min(to, seconds - (time.time() - t0))))
                try:
                    d = sock.recv(65536)
                except socket.timeout:
                    break
                if not d:
                    break
                got += len(d)
                if len(keep) < 3 * 1024 * 1024:
                    keep += d
            dt = max(time.time() - t0, 0.5)
            try:
                sock.sendall(f"TEARDOWN {base} RTSP/1.0\r\nCSeq: 99\r\nSession: {sess}\r\n\r\n".encode())
            except Exception:
                pass
            if got < 50 * 1024:
                return F(f"数据太少 {got // 1024}KB")
            measured = got * 8 / dt / 1000
            br = as_kbps or measured
            # 实时流下载速度≈码率；收得上来就视为流畅
            speed = measured * cfg["smooth_ratio"] * 1.01 if measured >= br * 0.85 else measured
            return {"bitrate": br, "speed": speed, "res": detect_res(rtp_payload(bytes(keep)), sdp)}
        finally:
            sock.close()
    return F("RTSP 跳转次数过多")


def probe(url, cfg):
    u, to, smooth = url.split("$")[0], cfg["timeout"], cfg["smooth_ratio"]
    try:
        if u.lower().startswith("rtsp://"):
            return rtsp_probe(u, cfg)
        if not u.startswith("http"):
            return F("不支持的协议")
        vod = re.compile(r"\.(mp4|mkv|avi|mov)(\?|$)")
        if cfg["skip_vod"] and vod.search(u.lower()):
            return F("点播文件")
        if ".m3u8" not in u.lower():
            data, dt, final, ctype = fetch_info(u, to, limit=6 * 1024 * 1024, max_time=5)
            if data.lstrip()[:7] == b"#EXTM3U":          # 没有 .m3u8 后缀的 HLS
                text, u = data.decode("utf-8", "ignore"), final
            else:
                # 跳转到 mp4 占位视频（如“盗版提示”）也算无效
                if cfg["skip_vod"] and (vod.search(final.lower()) or "mp4" in ctype or data[4:8] == b"ftyp"):
                    return F("跳转到点播/占位视频")
                if len(data) < 100 * 1024:
                    return F(f"数据太少 {len(data) // 1024}KB")
                sp = len(data) * 8 / dt / 1000
                md = media_duration(data)
                if md:
                    br = len(data) * 8 / md / 1000
                    # 直播流服务器按实时速度推送：收到的内容时长跟得上墙钟时间就算流畅
                    speed = br * smooth * 1.01 if md >= dt * 0.9 else br * md / dt
                else:
                    br, speed = sp, sp * smooth
                return {"bitrate": br, "speed": speed, "res": detect_res(data[:3 * 1024 * 1024])}
        else:
            text = fetch(u, to)[0].decode("utf-8", "ignore")
        res = ""
        for _ in range(2):
            if "#EXT-X-STREAM-INF" not in text:
                break
            vs = re.findall(r"#EXT-X-STREAM-INF:([^\n]*)\n\s*([^\n#]+)", text)
            if not vs:
                return F("m3u8 没有子流")
            bw = lambda v: int((re.search(r"BANDWIDTH=(\d+)", v[0]) or [0, 0])[1])
            attr, sub = max(vs, key=bw)
            m = re.search(r"RESOLUTION=(\d+x\d+)", attr)
            res = m.group(1) if m else res
            u = urljoin(u, sub.strip())
            text = fetch(u, to)[0].decode("utf-8", "ignore")
        segs = re.findall(r"#EXTINF:\s*([\d.]+)[^\n]*\n\s*([^\n#]+)", text)
        if not segs:
            return F("m3u8 没有分片")
        dur, seg = segs[-2] if len(segs) > 1 else segs[0]
        dur = float(dur) or 1.0
        data, dt = fetch(urljoin(u, seg.strip()), to, limit=MAX_SEG)
        if len(data) < 10 * 1024:
            return F("分片太小")
        return {"bitrate": len(data) * 8 / dur / 1000, "speed": len(data) * 8 / dt / 1000,
                "res": res or detect_res(data[:3 * 1024 * 1024])}
    except Exception as e:
        return F(err_text(e))


def res_fail(r, cfg):
    """分辨率不达标返回原因，否则 None"""
    mh = int(cfg.get("min_height") or 0)
    if not mh:
        return None
    m = re.match(r"(\d+)x(\d+)", r.get("res") or "")
    if not m:
        return None if cfg.get("keep_unknown_res", True) else "分辨率未知"
    w, h = int(m.group(1)), int(m.group(2))
    if min(w, h) < mh:
        return f"分辨率过低 {w}x{h}"
    return None


def run_once():
    with lock:
        cfg = json.loads(json.dumps(CFG))
    t0 = time.time()
    state.update(status="拉取源", progress=0, total=0, cur_stats={})
    black = [k for k in re.split(r"[,，\s]+", cfg["blacklist"]) if k]
    entries, seen, stats = [], set(), {}
    for s in cfg["sources"]:
        if stop_evt.is_set():
            break
        if not s.get("enabled", True):
            continue
        try:
            text, via, _ = fetch_source(s["url"])
            items = parse(text)
            n = 0
            for name, g, logo, url in items:
                key = url.split("$")[0]
                if key in seen or any(b in name or b in url for b in black):
                    continue
                if cfg["ip_version"] != "all" and ip_kind(key) == ("ipv4" if cfg["ip_version"] == "ipv6" else "ipv6"):
                    continue
                seen.add(key)
                entries.append((name, g, logo, url, s["url"]))
                n += 1
            stats[s["url"]] = {"name": s.get("name", ""), "total": len(items), "used": n, "tested": 0, "alive": 0, "error": "",
                               "via": "" if via == s["url"] else via}
            log(f"源 {s['url']}: {len(items)} 条，去重过滤后 {n} 条" + ("" if via == s["url"] else f"（经备用地址 {via}）"))
        except Exception as e:
            stats[s["url"]] = {"name": s.get("name", ""), "total": 0, "used": 0, "tested": 0, "alive": 0, "error": str(e)[:120]}
            log(f"源拉取失败 {s['url']}: {e}")
    if stop_evt.is_set():
        state.update(status="已停止")
        return
    state["cur_stats"] = stats   # 本轮各源：条数/已测/成功，实时更新
    if not entries:
        state.update(status="没有可用源")
        return
    state.update(status="测速中", total=len(entries))
    log(f"开始测速 {len(entries)} 条，并发 {cfg['workers']}")

    results = [None] * len(entries)
    global RUN_ID
    RUN_ID += 1
    my = RUN_ID
    def work(i):
        if stop_evt.is_set() or my != RUN_ID:
            return
        r = probe(entries[i][3], cfg)
        if my == RUN_ID:
            results[i] = r
            state["progress"] += 1
            stats[entries[i][4]]["tested"] += 1
            if r and "fail" not in r and r["bitrate"] >= cfg["min_kbps"] and not res_fail(r, cfg) and r["speed"] >= r["bitrate"] * cfg["smooth_ratio"]:
                stats[entries[i][4]]["alive"] += 1
    ex = ThreadPoolExecutor(max(1, int(cfg["workers"])))
    futs = [ex.submit(work, i) for i in range(len(entries))]
    while not stop_evt.is_set() and not all(f.done() for f in futs):
        time.sleep(0.5)
    ex.shutdown(wait=not stop_evt.is_set(), cancel_futures=True)
    if stop_evt.is_set():
        state.update(status="已停止")
        log("测速已手动停止，保留上次结果")
        return

    best, order, failed = {}, [], []
    for (name, g, logo, url, src), r in zip(entries, results):
        if not r or "fail" in r:
            failed.append({"group": g, "name": name, "url": url, "src": src, "reason": (r or {}).get("fail", "未测")})
            continue
        if r["bitrate"] < cfg["min_kbps"]:
            failed.append({"group": g, "name": name, "url": url, "src": src, "reason": f"码率过低 {round(r['bitrate'])}kbps"})
            continue
        rf = res_fail(r, cfg)
        if rf:
            failed.append({"group": g, "name": name, "url": url, "src": src, "reason": rf})
            continue
        if r["speed"] < r["bitrate"] * cfg["smooth_ratio"]:
            failed.append({"group": g, "name": name, "url": url, "src": src,
                           "reason": f"不流畅 速度{round(r['speed'] / 1000, 1)}/码率{round(r['bitrate'] / 1000, 1)}Mbps"})
            continue
        k = norm(name, cfg["merge_names"])
        if k not in best:
            best[k] = []
            order.append(k)
        best[k].append((r["bitrate"], name, g, logo, url, r))
    alive = sum(len(v) for v in best.values())

    # 分组：规则顺序在前，其余按出现顺序
    rule_order = [r["group"] for r in cfg["group_rules"]] if cfg["group_mode"] == "rules" else []
    groups = {g: [] for g in rule_order}
    for k in order:
        picks = sorted(best[k], key=lambda x: -x[0])[:max(1, int(cfg["keep"]))]
        name = picks[0][1]
        groups.setdefault(pick_group(name, picks[0][2], cfg), []).extend(picks)

    epg = f' x-tvg-url="{cfg["epg_url"]}"' if cfg.get("epg_url") else ""
    m3u, txt, report = [f"#EXTM3U{epg}"], [], []
    for g, picks in groups.items():
        if not picks:
            continue
        txt.append(f"{g},#genre#")
        for br, name, _, logo, url, r in picks:
            la = f' tvg-logo="{logo}"' if logo else ""
            m3u += [f'#EXTINF:-1 tvg-name="{name}"{la} group-title="{g}",{name}', url.split("$")[0]]
            txt.append(f"{name},{url}")
            report.append({"group": g, "name": name, "url": url, "kbps": round(br),
                           "speed_kbps": round(r["speed"]), "res": r["res"]})
        txt.append("")
    for fn, body in (("best.m3u", "\n".join(m3u) + "\n"), ("best.txt", "\n".join(txt)),
                     ("report.json", json.dumps(report, ensure_ascii=False)),
                     ("failed.json", json.dumps(failed, ensure_ascii=False))):
        tmp = os.path.join(DATA, fn + ".tmp")
        open(tmp, "w", encoding="utf-8").write(body)
        os.replace(tmp, os.path.join(DATA, fn))
    state.update(status="完成", source_stats=json.loads(json.dumps(stats)), last_run=time.strftime("%Y-%m-%d %H:%M"), last_ts=time.time(), channels=len(order),
                 tested=len(entries), alive=alive, duration_s=round(time.time() - t0))
    save_state()
    log(f"完成：测试 {len(entries)} 条，可用 {alive} 条，频道 {len(order)} 个，用时 {state['duration_s']}s")


def do_run():
    stop_evt.clear()
    try:
        run_once()
    except Exception as e:
        state["status"] = f"出错: {e}"
        log("运行出错", e)
    finally:
        stop_evt.clear()


def scheduler():
    base = time.time()
    if CFG.get("run_on_start"):
        do_run()
        base = time.time()
    else:
        log("等待定时或手动触发测速（可在网页“选项”里开启启动即测速）")
    while True:
        iv = float(CFG.get("interval_hours") or 0)
        last = max(state.get("last_ts") or 0, base)
        nxt = last + iv * 3600 if iv > 0 else None
        state["next_run"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(nxt)) if nxt else None
        if refresh_evt.wait(2):
            refresh_evt.clear()
        elif not nxt or time.time() < nxt:
            continue
        do_run()
        base = time.time()


# ---------------- 配置校验 ----------------
def clean_cfg(new):
    c = json.loads(json.dumps(CFG))
    num = {"interval_hours": (0, 168), "keep": (1, 20), "workers": (1, 256),
           "timeout": (2, 60), "smooth_ratio": (0, 5), "min_kbps": (0, 100000), "min_height": (0, 4320)}
    for k, (lo, hi) in num.items():
        if k in new:
            v = float(new[k])
            c[k] = int(v) if k in ("keep", "workers", "min_height") else v
            c[k] = min(max(c[k], lo), hi)
    for k in ("group_mode", "unmatched_group", "blacklist", "ip_version", "epg_url"):
        if k in new:
            c[k] = str(new[k]).strip()
    for k in ("skip_vod", "merge_names", "run_on_start", "keep_unknown_res"):
        if k in new:
            c[k] = bool(new[k])
    if "sources" in new:
        c["sources"] = [{"url": s["url"].strip(), "name": str(s.get("name", "")).strip(),
                         "enabled": bool(s.get("enabled", True))}
                        for s in new["sources"] if str(s.get("url", "")).strip().startswith(("http", "file"))]
    if "group_rules" in new:
        c["group_rules"] = [{"group": r["group"].strip(), "keywords": str(r.get("keywords", "")).strip()}
                            for r in new["group_rules"] if str(r.get("group", "")).strip()]
    if c["group_mode"] not in ("rules", "source"):
        c["group_mode"] = "rules"
    if c["ip_version"] not in ("all", "ipv4", "ipv6"):
        c["ip_version"] = "all"
    return c


# ---------------- HTTP ----------------
class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _file(self, fn, ctype):
        p = os.path.join(DATA, fn)
        if not os.path.exists(p):
            return self._send(503, "首次测速进行中，请稍后再试")
        self._send(200, open(p, "rb").read(), ctype)

    def _authed(self):
        return not PASSWORD or self.headers.get("X-Token", "") == PASSWORD

    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/") or "/"
        if p in ("/m3u", "/best.m3u"):
            return self._file("best.m3u", "audio/x-mpegurl; charset=utf-8")
        if p in ("/txt", "/best.txt"):
            return self._file("best.txt", "text/plain; charset=utf-8")
        if p in ("/failed", "/api/failed"):
            return self._file("failed.json", "application/json; charset=utf-8")
        if p in ("/report", "/api/report"):
            return self._file("report.json", "application/json; charset=utf-8")
        if p == "/api/status":
            return self._json({**state, "need_password": bool(PASSWORD)})
        if p == "/api/config":
            return self._json(CFG)
        if p == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        self._send(404, "not found")

    def do_POST(self):
        global CFG
        p = self.path.split("?")[0].rstrip("/")
        if not self._authed():
            return self._json({"ok": False, "error": "密码错误"}, 401)
        if p == "/api/config":
            try:
                n = int(self.headers.get("Content-Length", 0))
                new = clean_cfg(json.loads(self.rfile.read(n) or b"{}"))
            except Exception as e:
                return self._json({"ok": False, "error": f"格式错误: {e}"}, 400)
            with lock:
                CFG = new
                save_cfg(CFG)
            return self._json({"ok": True, "config": CFG})
        if p == "/api/refresh":
            if state["status"] in ("测速中", "拉取源", "正在停止"):
                return self._json({"ok": False, "error": "正在测速，请等待完成"})
            refresh_evt.set()
            state["status"] = "拉取源"
            return self._json({"ok": True})
        if p == "/api/testsource":
            try:
                n = int(self.headers.get("Content-Length", 0))
                url = json.loads(self.rfile.read(n) or b"{}").get("url", "").strip()
                text, via, dt = fetch_source(url)
                items = parse(text)
                kinds = {}
                for it in items:
                    k = it[3].split(":", 1)[0].lower()
                    kinds[k] = kinds.get(k, 0) + 1
                return self._json({"ok": True, "count": len(items), "kinds": kinds, "via": via, "seconds": round(dt, 1)})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:300]})
        if p == "/api/stop":
            if state["status"] not in ("测速中", "拉取源"):
                return self._json({"ok": False, "error": "当前没有在测速"})
            stop_evt.set()
            state["status"] = "正在停止"
            return self._json({"ok": True})
        self._send(404, "not found")

    def log_message(self, *a):
        pass


PAGE = r"""<!doctype html><html lang=zh><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>IPTV 测速优选</title>
<style>
*{box-sizing:border-box}body{font:15px/1.5 -apple-system,"PingFang SC",sans-serif;margin:0;background:#f4f5f7;color:#222}
header{background:#1f2937;color:#fff;padding:14px 16px;font-weight:600}
nav{display:flex;background:#fff;border-bottom:1px solid #ddd;position:sticky;top:0;z-index:2}
nav a{flex:1;text-align:center;padding:11px 0;color:#555;text-decoration:none;cursor:pointer}
nav a.on{color:#2563eb;border-bottom:2px solid #2563eb;font-weight:600}
main{max-width:760px;margin:0 auto;padding:12px}
.card{background:#fff;border-radius:10px;padding:14px;margin-bottom:12px;box-shadow:0 1px 2px #0001}
h3{margin:0 0 10px;font-size:16px}
.row{display:flex;gap:8px;align-items:center;margin:6px 0;flex-wrap:wrap}
.row>label{width:130px;color:#555}
input[type=text],input[type=number],select,textarea{flex:1;min-width:0;padding:7px 9px;border:1px solid #ccc;border-radius:6px;font:inherit}
button{padding:7px 14px;border:0;border-radius:6px;background:#2563eb;color:#fff;font:inherit;cursor:pointer}
button.gray{background:#e5e7eb;color:#333}button.red{background:#ef4444}
.list .item{display:flex;gap:6px;align-items:center;padding:7px 0;border-bottom:1px solid #eee}
.list .item input[type=text]{font-size:13px}
.muted{color:#888;font-size:13px}.ok{color:#16a34a}.bad{color:#dc2626}
code{background:#f1f5f9;padding:2px 6px;border-radius:4px;word-break:break-all}
.bar{height:8px;background:#e5e7eb;border-radius:4px;overflow:hidden}.bar>i{display:block;height:100%;background:#2563eb}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:5px 4px;border-bottom:1px solid #eee;text-align:left}
.save{position:sticky;bottom:0;background:#f4f5f7;padding:10px 0;text-align:right}
#toast{position:fixed;left:50%;bottom:70px;transform:translateX(-50%);background:#111;color:#fff;padding:8px 16px;border-radius:20px;display:none}
</style>
<header>📺 IPTV 测速优选</header>
<nav><a data-t=status class=on>状态</a><a data-t=sources>直播源</a><a data-t=groups>分组</a><a data-t=options>选项</a><a data-t=result>结果</a></nav>
<main>
<section id=status>
 <div class=card><h3>运行状态</h3>
  <div id=st></div><div class=bar style="margin:10px 0"><i id=pg style="width:0"></i></div>
  <button id=btnrun onclick=refresh()>立即测速</button> <button id=btnstop class=red onclick=stopRun()>停止测速</button></div>
 <div class=card><h3>订阅地址</h3>
  <div class=row>M3U：<code id=u1></code></div><div class=row>TXT：<code id=u2></code></div>
  <div class=muted>填入播放器即可，测速完成后自动更新内容。</div></div>
 <div class=card><h3>上次测速情况</h3><div id=lastsum class=muted style="margin-bottom:6px"></div><table id=sst></table></div>
</section>

<section id=sources hidden>
 <div class=card><h3>添加直播源</h3>
  <div class=row><input type=text id=nu placeholder="m3u / txt 订阅地址"></div>
  <div class=row><input type=text id=nn placeholder="备注（可选）"><button onclick=addSrc()>添加</button></div></div>
 <div class=card><h3>源列表</h3><div class="list" id=srcs></div>
  <div class=muted>取消勾选 = 暂停使用；保存后下次测速生效。</div></div>
</section>

<section id=groups hidden>
 <div class=card><h3>分组方式</h3>
  <div class=row><label>模式</label><select id=group_mode>
   <option value=rules>按关键词规则分组</option><option value=source>保留源里的原始分组</option></select></div>
  <div class=row><label>未匹配的频道</label><input type=text id=unmatched_group placeholder="留空 = 用原始分组"></div></div>
 <div class=card><h3>分组规则 <span class=muted>（从上到下匹配，频道名含任一关键词即归入）</span></h3>
  <div class=list id=rules></div>
  <div class=row style="margin-top:8px"><button class=gray onclick="cfg.group_rules.push({group:'',keywords:''});drawRules()">＋ 新增分组</button></div></div>
</section>

<section id=options hidden>
 <div class=card><h3>测速</h3>
  <div class=row><label>启动即测速</label><input type=checkbox id=run_on_start><span class=muted>关闭 = 容器启动后不测，等到点或手动</span></div>
  <div class=row><label>重测间隔（小时）</label><input type=number id=interval_hours step=1 min=0><span class=muted>0 = 只手动</span></div>
  <div class=row><label>每频道保留条数</label><input type=number id=keep min=1 max=20></div>
  <div class=row><label>并发数</label><input type=number id=workers min=1 max=256><span class=muted>软路由弱就调低</span></div>
  <div class=row><label>超时（秒）</label><input type=number id=timeout min=2 max=60></div>
  <div class=row><label>流畅系数</label><input type=number id=smooth_ratio step=0.1 min=0><span class=muted>下载速度 ≥ 码率×此值才保留</span></div>
  <div class=row><label>最低码率 kbps</label><input type=number id=min_kbps min=0><span class=muted>如 2000 ≈ 只要高清</span></div>
  <div class=row><label>最低分辨率</label><select id=min_height><option value=0>不限</option><option value=576>576p（标清）</option><option value=720>720p</option><option value=1080>1080p</option><option value=2160>4K</option></select><span class=muted>低于此分辨率的剔除</span></div>
  <div class=row><label>分辨率未知时保留</label><input type=checkbox id=keep_unknown_res><span class=muted>有些源测不出分辨率，关掉就一并剔除</span></div></div>
 <div class=card><h3>过滤</h3>
  <div class=row><label>IP 类型</label><select id=ip_version><option value=all>全部</option><option value=ipv4>仅 IPv4</option><option value=ipv6>仅 IPv6</option></select></div>
  <div class=row><label>屏蔽关键词</label><input type=text id=blacklist placeholder="逗号分隔，频道名或链接含有即丢弃"></div>
  <div class=row><label>丢弃点播占位视频</label><input type=checkbox id=skip_vod></div>
  <div class=row><label>合并同名频道</label><input type=checkbox id=merge_names><span class=muted>CCTV-1 高清 = CCTV1</span></div></div>
 <div class=card><h3>输出</h3>
  <div class=row><label>EPG 节目单地址</label><input type=text id=epg_url></div></div>
</section>

<section id=result hidden>
 <div class=card>
  <div class=row><button id=vok onclick="view='ok';drawResult()">成功</button><button id=vbad onclick="view='bad';drawResult()">失败清单</button>
   <span class=muted id=rc></span></div>
  <div class=row><input type=text id=rf placeholder="搜索频道或链接" oninput=drawResult()>
   <select id=rr onchange=drawResult() hidden></select><button class=gray id=rexp onclick=exportFail() hidden>导出 txt</button></div>
  <table id=rt></table><div class=muted id=rmore></div></div>
</section>

<div class=save id=savebar hidden><button class=gray onclick=load()>撤销</button> <button onclick=save()>保存设置</button></div>
</main><div id=toast></div>
<script>
let cfg={},report=[],tab='status';const $=id=>document.getElementById(id);
const tok=()=>localStorage.iptvtok||'';
function toast(t){const e=$('toast');e.textContent=t;e.style.display='block';setTimeout(()=>e.style.display='none',2200)}
async function api(p,body){const r=await fetch(p,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Token':tok()},body:JSON.stringify(body)});
 if(r.status==401){const pw=prompt('请输入管理密码');if(pw!==null){localStorage.iptvtok=pw;return api(p,body)}throw 0}return r.json()}
document.querySelectorAll('nav a').forEach(a=>a.onclick=()=>{tab=a.dataset.t;document.querySelectorAll('nav a').forEach(x=>x.classList.toggle('on',x==a));
 document.querySelectorAll('main section').forEach(s=>s.hidden=s.id!=tab);$('savebar').hidden=!['sources','groups','options'].includes(tab);if(tab=='result')loadResult()});
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const F=['interval_hours','keep','workers','timeout','smooth_ratio','min_kbps','min_height','ip_version','blacklist','epg_url','group_mode','unmatched_group'],B=['skip_vod','merge_names','run_on_start','keep_unknown_res'];
async function load(){cfg=await api('/api/config');F.forEach(k=>$(k).value=cfg[k]);B.forEach(k=>$(k).checked=cfg[k]);drawSrc();drawRules()}
function collect(){F.forEach(k=>cfg[k]=$(k).value);B.forEach(k=>cfg[k]=$(k).checked)}
async function save(){collect();const r=await api('/api/config',cfg);if(r.ok){cfg=r.config;toast('已保存，下次测速生效');drawSrc();drawRules()}else toast(r.error)}
function drawSrc(){$('srcs').innerHTML=cfg.sources.map((s,i)=>`<div class=item><input type=checkbox ${s.enabled?'checked':''} onchange="cfg.sources[${i}].enabled=this.checked">
 <div style="flex:1;min-width:0"><input type=text value="${esc(s.name)}" placeholder=备注 onchange="cfg.sources[${i}].name=this.value" style="width:100%;margin-bottom:4px">
 <input type=text value="${esc(s.url)}" onchange="cfg.sources[${i}].url=this.value" style="width:100%"></div>
 <button class=gray onclick="testSrc(${i},this)">测试</button>
 <button class=red onclick="if(confirm('删除这个源？')){cfg.sources.splice(${i},1);drawSrc()}">删</button></div>`).join('')||'<div class=muted>还没有源</div>'}
async function testSrc(i,b){b.disabled=true;b.textContent='…';const r=await api('/api/testsource',{url:cfg.sources[i].url});b.disabled=false;b.textContent='测试';
 alert(r.ok?`可用：${r.count} 条（${Object.entries(r.kinds).map(([k,v])=>k+' '+v).join('，')}），用时 ${r.seconds}s`+(r.via!=cfg.sources[i].url?`\n经备用地址：${r.via}`:''):'拉取失败：'+r.error)}
function addSrc(){const u=$('nu').value.trim();if(!/^https?:\/\//.test(u))return toast('请输入 http(s) 地址');cfg.sources.push({url:u,name:$('nn').value.trim(),enabled:true});$('nu').value=$('nn').value='';drawSrc();toast('已添加，记得保存')}
function mv(i,d){const r=cfg.group_rules,j=i+d;if(j<0||j>=r.length)return;[r[i],r[j]]=[r[j],r[i]];drawRules()}
function drawRules(){$('rules').innerHTML=cfg.group_rules.map((r,i)=>`<div class=item>
 <input type=text value="${esc(r.group)}" placeholder=分组名 style="flex:0 0 26%" onchange="cfg.group_rules[${i}].group=this.value">
 <input type=text value="${esc(r.keywords)}" placeholder="关键词，逗号分隔" onchange="cfg.group_rules[${i}].keywords=this.value">
 <button class=gray onclick=mv(${i},-1)>↑</button><button class=gray onclick=mv(${i},1)>↓</button>
 <button class=red onclick="cfg.group_rules.splice(${i},1);drawRules()">删</button></div>`).join('')}
function srcTable(st,live){const e=Object.entries(st||{});if(!e.length)return '<tr><td class=muted>暂无</td></tr>';
 return '<tr><th>源</th><th>条数</th><th>已测</th><th>成功</th></tr>'+e.map(([u,v])=>`<tr><td style="word-break:break-all">${esc(v.name||u)}${v.via?`<div class=muted>经备用：${esc(v.via)}</div>`:''}</td>`+
 (v.error?`<td colspan=3><span class=bad>拉取失败：${esc(v.error)}</span></td>`:`<td>${v.used}${v.used!=v.total?`<span class=muted>/${v.total}</span>`:''}</td><td>${v.tested??v.used}</td><td class=ok>${v.alive}</td>`)+'</tr>').join('')}
async function status(){const s=await api('/api/status');const pct=s.total?Math.round(s.progress*100/s.total):0;
 const busy=['测速中','拉取源','正在停止'].includes(s.status);
 $('st').innerHTML=`状态：<b>${esc(s.status)}</b>${s.total&&busy?`（${s.progress}/${s.total}）`:''}　下次自动测速：${s.next_run||'手动'}`+
  (busy&&Object.keys(s.cur_stats||{}).length?`<table style="margin-top:8px">${srcTable(s.cur_stats)}</table>`:'');
 const canStop=busy&&s.status!='正在停止';$('btnstop').disabled=!canStop;$('btnstop').style.opacity=canStop?1:.4;$('btnrun').disabled=busy;$('btnrun').style.opacity=busy?.5:1;
 $('pg').style.width=(busy?pct:(s.last_run?100:0))+'%';
 $('lastsum').innerHTML=s.last_run?`${s.last_run} 完成，测试 ${s.tested} 条，成功 ${s.alive} 条，保留频道 <b>${s.channels}</b> 个，用时 ${s.duration_s}s`:'还没有完成过测速';
 $('sst').innerHTML=srcTable(s.source_stats)}
async function stopRun(){if(!confirm('停止本次测速？订阅保留上次结果'))return;const r=await api('/api/stop',{});toast(r.ok?'正在停止…':r.error);status()}
async function refresh(){const r=await api('/api/refresh',{});toast(r.ok?'已开始重新测速':r.error);status()}
let failed=[],view='ok';
async function loadResult(){try{report=await (await fetch('/report')).json()}catch(e){report=[]}
 try{failed=await (await fetch('/failed')).json()}catch(e){failed=[]}
 const cnt={};failed.forEach(x=>{const k=x.reason.startsWith('HTTP')?x.reason:x.reason.split(' ')[0];cnt[k]=(cnt[k]||0)+1});
 $('rr').innerHTML=`<option value="">全部原因（${failed.length}）</option>`+Object.entries(cnt).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<option value="${esc(k)}">${esc(k)}（${v}）</option>`).join('');drawResult()}
function failRows(){const f=$('rf').value.trim().toUpperCase(),k=$('rr').value;
 return failed.filter(x=>(!k||x.reason.startsWith(k))&&(!f||x.name.toUpperCase().includes(f)||x.url.toUpperCase().includes(f)))}
function drawResult(){const f=$('rf').value.trim().toUpperCase();
 $('vok').className=view=='ok'?'':'gray';$('vbad').className=view=='bad'?'':'gray';$('rr').hidden=$('rexp').hidden=view!='bad';
 $('rc').textContent=`成功 ${report.length} 条，失败 ${failed.length} 条`;
 if(view=='ok'){const rows=report.filter(r=>!f||r.name.toUpperCase().includes(f)||r.url.toUpperCase().includes(f));
  $('rt').innerHTML='<tr><th>分组</th><th>频道</th><th>码率</th><th>分辨率</th></tr>'+rows.slice(0,500).map(r=>
  `<tr><td>${esc(r.group)}</td><td><a href="${esc(r.url)}" target=_blank>${esc(r.name)}</a></td><td>${(r.kbps/1000).toFixed(1)} Mbps</td><td>${esc(r.res)||'-'}</td></tr>`).join('');
  $('rmore').textContent=rows.length>500?`只显示前 500 条，共 ${rows.length} 条，可搜索缩小范围`:'';return}
 const rows=failRows();
 $('rt').innerHTML='<tr><th>频道</th><th>原因</th><th>链接</th></tr>'+rows.slice(0,500).map(x=>
  `<tr><td>${esc(x.name)}</td><td class=bad>${esc(x.reason)}</td><td style="word-break:break-all;font-size:12px"><a href="${esc(x.url)}" target=_blank>${esc(x.url)}</a></td></tr>`).join('');
 $('rmore').textContent=rows.length>500?`只显示前 500 条，共 ${rows.length} 条，导出 txt 可拿到全部`:''}
function exportFail(){const t=failRows().map(x=>`${x.name},${x.url}  # ${x.reason}`).join('\n');
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([t],{type:'text/plain'}));a.download='failed.txt';a.click()}
$('u1').textContent=location.origin+'/m3u';$('u2').textContent=location.origin+'/txt';
load();status();setInterval(()=>{if(tab=='status')status()},3000);
</script></html>"""


if __name__ == "__main__":
    log(f"启动，端口 {PORT}，配置 {CFG_PATH}")
    threading.Thread(target=scheduler, daemon=True).start()
    ThreadingHTTPServer(("", PORT), H).serve_forever()
