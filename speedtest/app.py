#!/usr/bin/env python3
"""
iptv-speedtest: 定时拉取直播源 -> 逐条测速 -> 每个频道保留码率最高且能流畅播放的 N 条
-> 在局域网提供 /m3u /txt 订阅。纯标准库，无第三方依赖。

环境变量:
  SOURCES         订阅源地址，多个用英文逗号分隔（支持 m3u / txt 格式）
  INTERVAL_HOURS  重新测速间隔（小时），默认 12；0 = 只跑一次
  KEEP            每个频道保留条数，默认 1
  WORKERS         并发测速数，默认 32
  TIMEOUT         单次请求超时秒数，默认 8
  SMOOTH_RATIO    下载速度需达到码率的倍数才算流畅，默认 1.2
  MIN_KBPS        最低码率 kbps，低于则丢弃，默认 0
  PORT            HTTP 端口，默认 8080
  DATA_DIR        结果目录，默认 /data
"""
import json, os, re, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin

DEFAULT_SRC = "https://raw.githubusercontent.com/zknjjjx/iptv/main/iptv.m3u"
SOURCES = [s.strip() for s in os.getenv("SOURCES", DEFAULT_SRC).split(",") if s.strip()]
INTERVAL = float(os.getenv("INTERVAL_HOURS", "12"))
KEEP = int(os.getenv("KEEP", "1"))
WORKERS = int(os.getenv("WORKERS", "32"))
TIMEOUT = float(os.getenv("TIMEOUT", "8"))
SMOOTH = float(os.getenv("SMOOTH_RATIO", "1.2"))
MIN_KBPS = float(os.getenv("MIN_KBPS", "0"))
PORT = int(os.getenv("PORT", "8080"))
DATA = os.getenv("DATA_DIR", "/data")
UA = "Mozilla/5.0 (iptv-speedtest)"
MAX_SEG = 8 * 1024 * 1024  # 单个分片最多下载 8MB

os.makedirs(DATA, exist_ok=True)
state = {"status": "idle", "progress": 0, "total": 0, "last_run": None,
         "channels": 0, "tested": 0, "alive": 0, "duration_s": 0}
refresh_evt = threading.Event()


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def fetch(url, limit=None, max_time=None):
    """返回 (bytes, 用时秒)"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    t = time.time()
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        buf = bytearray()
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            buf += chunk
            if limit and len(buf) >= limit:
                break
            if time.time() - t > (max_time or TIMEOUT * 3):
                break
    return bytes(buf), max(time.time() - t, 0.001)


# ---------------- 解析源 ----------------
def parse(text):
    """返回 [(name, group, logo, url)]，兼容 m3u 和 txt(频道,url / 分组,#genre#)"""
    out = []
    if "#EXTM3U" in text[:200] or "#EXTINF" in text:
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


def norm(name):
    n = name.upper().replace(" ", "").replace("-", "")
    n = re.sub(r"(高清|超清|标清|HD|FHD|UHD|4K|1080P?|720P?)$", "", n)
    return n


# ---------------- 测速 ----------------
def probe(url):
    """返回 dict(bitrate kbps, speed kbps, res) 或 None"""
    u = url.split("$")[0]
    try:
        if ".m3u8" not in u.lower():
            # 非 HLS（flv/ts/rtmp 等）：直播流按实时速率推送，读 5 秒，平均速率≈码率
            if not u.startswith("http") or re.search(r"\.(mp4|mkv|avi)(\?|$)", u.lower()):
                return None  # mp4 等点播文件通常是占位视频，不算直播
            data, dt = fetch(u, limit=6 * 1024 * 1024, max_time=5)
            if len(data) < 100 * 1024:
                return None
            sp = len(data) * 8 / dt / 1000
            return {"bitrate": sp, "speed": sp * SMOOTH, "res": ""}
        text = fetch(u)[0].decode("utf-8", "ignore")
        res = ""
        for _ in range(2):  # 处理主列表 -> 子列表
            if "#EXT-X-STREAM-INF" not in text:
                break
            vs = re.findall(r"#EXT-X-STREAM-INF:([^\n]*)\n\s*([^\n#]+)", text)
            if not vs:
                return None
            def bw(v):
                m = re.search(r"BANDWIDTH=(\d+)", v[0])
                return int(m.group(1)) if m else 0
            attr, sub = max(vs, key=bw)
            m = re.search(r"RESOLUTION=(\d+x\d+)", attr)
            res = m.group(1) if m else res
            u = urljoin(u, sub.strip())
            text = fetch(u)[0].decode("utf-8", "ignore")
        segs = re.findall(r"#EXTINF:\s*([\d.]+)[^\n]*\n\s*([^\n#]+)", text)
        if not segs:
            return None
        dur, seg = segs[-2] if len(segs) > 1 else segs[0]  # 取倒数第二个，靠近直播点且已生成完
        dur = float(dur) or 1.0
        data, dt = fetch(urljoin(u, seg.strip()), limit=MAX_SEG)
        if len(data) < 10 * 1024:
            return None
        return {"bitrate": len(data) * 8 / dur / 1000,
                "speed": len(data) * 8 / dt / 1000, "res": res}
    except Exception:
        return None


def run_once():
    t0 = time.time()
    state.update(status="fetching", progress=0)
    entries, seen = [], set()
    for s in SOURCES:
        try:
            text = fetch(s)[0].decode("utf-8", "ignore")
            items = parse(text)
            log(f"源 {s}: {len(items)} 条")
            for it in items:
                key = it[3].split("$")[0]
                if key not in seen:
                    seen.add(key)
                    entries.append(it)
        except Exception as e:
            log(f"源拉取失败 {s}: {e}")
    if not entries:
        state.update(status="error: 没有可用源")
        return
    state.update(status="testing", total=len(entries))
    log(f"开始测速 {len(entries)} 条，并发 {WORKERS}")

    results = [None] * len(entries)
    def work(i):
        results[i] = probe(entries[i][3])
        state["progress"] += 1
    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(work, range(len(entries))))

    best, order = {}, []
    for (name, group, logo, url), r in zip(entries, results):
        if not r or r["bitrate"] < MIN_KBPS or r["speed"] < r["bitrate"] * SMOOTH:
            continue
        k = norm(name)
        if k not in best:
            best[k] = []
            order.append(k)
        best[k].append((r["bitrate"], name, group, logo, url, r))
    alive = sum(len(v) for v in best.values())

    # 按分组输出，组内保持原始顺序
    groups = {}
    for k in order:
        picks = sorted(best[k], key=lambda x: -x[0])[:KEEP]
        g = picks[0][2] or "其他"
        groups.setdefault(g, []).extend(picks)

    stamp = time.strftime("%Y-%m-%d %H:%M")
    m3u = ['#EXTM3U x-tvg-url="https://live.fanmingming.cn/e.xml"']
    txt, report = [], []
    for g, picks in groups.items():
        txt.append(f"{g},#genre#")
        for br, name, _, logo, url, r in picks:
            logo_attr = f' tvg-logo="{logo}"' if logo else ""
            m3u.append(f'#EXTINF:-1 tvg-name="{name}"{logo_attr} group-title="{g}",{name}')
            m3u.append(url)
            txt.append(f"{name},{url}")
            report.append({"group": g, "name": name, "url": url,
                           "kbps": round(br), "speed_kbps": round(r["speed"]), "res": r["res"]})
        txt.append("")
    for fn, body in (("best.m3u", "\n".join(m3u) + "\n"), ("best.txt", "\n".join(txt)),
                     ("report.json", json.dumps(report, ensure_ascii=False, indent=1))):
        tmp = os.path.join(DATA, fn + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, os.path.join(DATA, fn))

    state.update(status="done", last_run=stamp, channels=len(order), tested=len(entries),
                 alive=alive, duration_s=round(time.time() - t0))
    log(f"完成：测试 {len(entries)} 条，可用 {alive} 条，频道 {len(order)} 个，用时 {state['duration_s']}s")


def scheduler():
    while True:
        try:
            run_once()
        except Exception as e:
            state["status"] = f"error: {e}"
            log("运行出错", e)
        if INTERVAL <= 0:
            refresh_evt.wait()
        else:
            refresh_evt.wait(INTERVAL * 3600)
        refresh_evt.clear()


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

    def _file(self, fn, ctype):
        p = os.path.join(DATA, fn)
        if not os.path.exists(p):
            return self._send(503, "首次测速进行中，请稍后再试\n" + json.dumps(state, ensure_ascii=False))
        with open(p, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/m3u", "/best.m3u"):
            return self._file("best.m3u", "audio/x-mpegurl; charset=utf-8")
        if path in ("/txt", "/best.txt"):
            return self._file("best.txt", "text/plain; charset=utf-8")
        if path == "/report":
            return self._file("report.json", "application/json; charset=utf-8")
        if path == "/status":
            return self._send(200, json.dumps(state, ensure_ascii=False), "application/json; charset=utf-8")
        if path == "/refresh":
            refresh_evt.set()
            return self._send(200, "已触发重新测速\n")
        host = self.headers.get("Host", f"localhost:{PORT}")
        s = state
        pct = f"{s['progress']}/{s['total']}" if s["total"] else "-"
        html = f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>IPTV 测速</title><body style="font-family:sans-serif;max-width:640px;margin:2em auto;padding:0 1em">
<h2>IPTV 测速优选</h2>
<p>状态：<b>{s['status']}</b>（进度 {pct}）<br>上次完成：{s['last_run'] or '-'}，
频道 {s['channels']} 个，可用 {s['alive']}/{s['tested']} 条，用时 {s['duration_s']}s</p>
<p>M3U 订阅：<code>http://{host}/m3u</code><br>TXT 订阅：<code>http://{host}/txt</code></p>
<p><a href=/report>测速明细</a> · <a href=/status>状态 JSON</a> · <a href=/refresh>立即重测</a></p>"""
        self._send(200, html, "text/html; charset=utf-8")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    log(f"源 {SOURCES}，每 {INTERVAL}h 重测，每频道保留 {KEEP} 条，端口 {PORT}")
    threading.Thread(target=scheduler, daemon=True).start()
    ThreadingHTTPServer(("", PORT), H).serve_forever()
