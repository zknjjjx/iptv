#!/usr/bin/env python3
"""
iptv-speedtest: 定时拉取直播源 -> 逐条测速 -> 每个频道保留码率最高且能流畅播放的 N 条
-> 局域网提供 /m3u /txt 订阅，并带网页设置面板。纯标准库，无第三方依赖。

所有设置在网页里改，保存到 /data/config.json。环境变量只在首次启动时作为默认值：
  SOURCES, INTERVAL_HOURS, KEEP, WORKERS, TIMEOUT, PORT, DATA_DIR
  ADMIN_PASSWORD  设置后，修改设置/触发测速需要输入此密码（订阅地址不受影响）
"""
import ipaddress, json, os, re, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlparse

PORT = int(os.getenv("PORT", "8080"))
DATA = os.getenv("DATA_DIR", "/data")
PASSWORD = os.getenv("ADMIN_PASSWORD", "")
CFG_PATH = os.path.join(DATA, "config.json")
UA = "Mozilla/5.0 (iptv-speedtest)"
MAX_SEG = 8 * 1024 * 1024
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
    "ip_version": "all",            # all / ipv4 / ipv6
    "skip_vod": True,               # 丢弃 mp4 等点播占位视频
    "merge_names": True,            # 合并 "CCTV-1 高清" "CCTV1" 这类同名频道
    "epg_url": "https://live.fanmingming.cn/e.xml",
    "run_on_start": False,          # 容器启动后是否立即测速（默认否，等到点或手动）
}

lock = threading.Lock()
state = {"status": "idle", "progress": 0, "total": 0, "last_run": None,
         "channels": 0, "tested": 0, "alive": 0, "duration_s": 0, "next_run": None,
         "source_stats": {}}
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
    req = urllib.request.Request(url, headers={"User-Agent": UA})
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
def probe(url, cfg):
    u, to, smooth = url.split("$")[0], cfg["timeout"], cfg["smooth_ratio"]
    try:
        if ".m3u8" not in u.lower():
            if not u.startswith("http"):
                return None
            if cfg["skip_vod"] and re.search(r"\.(mp4|mkv|avi|mov)(\?|$)", u.lower()):
                return None
            data, dt = fetch(u, to, limit=6 * 1024 * 1024, max_time=5)
            if len(data) < 100 * 1024:
                return None
            sp = len(data) * 8 / dt / 1000
            return {"bitrate": sp, "speed": sp * smooth, "res": ""}
        text = fetch(u, to)[0].decode("utf-8", "ignore")
        res = ""
        for _ in range(2):
            if "#EXT-X-STREAM-INF" not in text:
                break
            vs = re.findall(r"#EXT-X-STREAM-INF:([^\n]*)\n\s*([^\n#]+)", text)
            if not vs:
                return None
            bw = lambda v: int((re.search(r"BANDWIDTH=(\d+)", v[0]) or [0, 0])[1])
            attr, sub = max(vs, key=bw)
            m = re.search(r"RESOLUTION=(\d+x\d+)", attr)
            res = m.group(1) if m else res
            u = urljoin(u, sub.strip())
            text = fetch(u, to)[0].decode("utf-8", "ignore")
        segs = re.findall(r"#EXTINF:\s*([\d.]+)[^\n]*\n\s*([^\n#]+)", text)
        if not segs:
            return None
        dur, seg = segs[-2] if len(segs) > 1 else segs[0]
        dur = float(dur) or 1.0
        data, dt = fetch(urljoin(u, seg.strip()), to, limit=MAX_SEG)
        if len(data) < 10 * 1024:
            return None
        return {"bitrate": len(data) * 8 / dur / 1000, "speed": len(data) * 8 / dt / 1000, "res": res}
    except Exception:
        return None


def run_once():
    with lock:
        cfg = json.loads(json.dumps(CFG))
    t0 = time.time()
    state.update(status="拉取源", progress=0, total=0)
    black = [k for k in re.split(r"[,，\s]+", cfg["blacklist"]) if k]
    entries, seen, stats = [], set(), {}
    for s in cfg["sources"]:
        if stop_evt.is_set():
            break
        if not s.get("enabled", True):
            continue
        try:
            items = parse(fetch(s["url"], max(cfg["timeout"] * 3, 30), max_time=180)[0].decode("utf-8", "ignore"))
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
            stats[s["url"]] = {"total": len(items), "used": n, "alive": 0, "error": ""}
            log(f"源 {s['url']}: {len(items)} 条，去重过滤后 {n} 条")
        except Exception as e:
            stats[s["url"]] = {"total": 0, "used": 0, "alive": 0, "error": str(e)[:120]}
            log(f"源拉取失败 {s['url']}: {e}")
    if stop_evt.is_set():
        state.update(status="已停止")
        return
    if not entries:
        state["source_stats"] = stats
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
    ex = ThreadPoolExecutor(max(1, int(cfg["workers"])))
    futs = [ex.submit(work, i) for i in range(len(entries))]
    while not stop_evt.is_set() and not all(f.done() for f in futs):
        time.sleep(0.5)
    ex.shutdown(wait=not stop_evt.is_set(), cancel_futures=True)
    if stop_evt.is_set():
        state.update(status="已停止")
        log("测速已手动停止，保留上次结果")
        return
    state["source_stats"] = stats

    best, order = {}, []
    for (name, g, logo, url, src), r in zip(entries, results):
        if not r or r["bitrate"] < cfg["min_kbps"] or r["speed"] < r["bitrate"] * cfg["smooth_ratio"]:
            continue
        stats[src]["alive"] += 1
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
            m3u += [f'#EXTINF:-1 tvg-name="{name}"{la} group-title="{g}",{name}', url]
            txt.append(f"{name},{url}")
            report.append({"group": g, "name": name, "url": url, "kbps": round(br),
                           "speed_kbps": round(r["speed"]), "res": r["res"]})
        txt.append("")
    for fn, body in (("best.m3u", "\n".join(m3u) + "\n"), ("best.txt", "\n".join(txt)),
                     ("report.json", json.dumps(report, ensure_ascii=False))):
        tmp = os.path.join(DATA, fn + ".tmp")
        open(tmp, "w", encoding="utf-8").write(body)
        os.replace(tmp, os.path.join(DATA, fn))
    state.update(status="完成", last_run=time.strftime("%Y-%m-%d %H:%M"), last_ts=time.time(), channels=len(order),
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
           "timeout": (2, 60), "smooth_ratio": (0, 5), "min_kbps": (0, 100000)}
    for k, (lo, hi) in num.items():
        if k in new:
            v = float(new[k])
            c[k] = int(v) if k in ("keep", "workers") else v
            c[k] = min(max(c[k], lo), hi)
    for k in ("group_mode", "unmatched_group", "blacklist", "ip_version", "epg_url"):
        if k in new:
            c[k] = str(new[k]).strip()
    for k in ("skip_vod", "merge_names", "run_on_start"):
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
  <button id=btnrun onclick=refresh()>立即测速</button> <button id=btnstop class=red onclick=stopRun() hidden>停止测速</button></div>
 <div class=card><h3>订阅地址</h3>
  <div class=row>M3U：<code id=u1></code></div><div class=row>TXT：<code id=u2></code></div>
  <div class=muted>填入播放器即可，测速完成后自动更新内容。</div></div>
 <div class=card><h3>各源情况（上次测速）</h3><table id=sst></table></div>
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
  <div class=row><label>最低码率 kbps</label><input type=number id=min_kbps min=0><span class=muted>如 2000 ≈ 只要高清</span></div></div>
 <div class=card><h3>过滤</h3>
  <div class=row><label>IP 类型</label><select id=ip_version><option value=all>全部</option><option value=ipv4>仅 IPv4</option><option value=ipv6>仅 IPv6</option></select></div>
  <div class=row><label>屏蔽关键词</label><input type=text id=blacklist placeholder="逗号分隔，频道名或链接含有即丢弃"></div>
  <div class=row><label>丢弃点播占位视频</label><input type=checkbox id=skip_vod></div>
  <div class=row><label>合并同名频道</label><input type=checkbox id=merge_names><span class=muted>CCTV-1 高清 = CCTV1</span></div></div>
 <div class=card><h3>输出</h3>
  <div class=row><label>EPG 节目单地址</label><input type=text id=epg_url></div></div>
</section>

<section id=result hidden>
 <div class=card><h3>测速结果 <span class=muted id=rc></span></h3>
  <div class=row><input type=text id=rf placeholder="搜索频道" oninput=drawResult()></div>
  <table id=rt></table></div>
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
const F=['interval_hours','keep','workers','timeout','smooth_ratio','min_kbps','ip_version','blacklist','epg_url','group_mode','unmatched_group'],B=['skip_vod','merge_names','run_on_start'];
async function load(){cfg=await api('/api/config');F.forEach(k=>$(k).value=cfg[k]);B.forEach(k=>$(k).checked=cfg[k]);drawSrc();drawRules()}
function collect(){F.forEach(k=>cfg[k]=$(k).value);B.forEach(k=>cfg[k]=$(k).checked)}
async function save(){collect();const r=await api('/api/config',cfg);if(r.ok){cfg=r.config;toast('已保存，下次测速生效');drawSrc();drawRules()}else toast(r.error)}
function drawSrc(){$('srcs').innerHTML=cfg.sources.map((s,i)=>`<div class=item><input type=checkbox ${s.enabled?'checked':''} onchange="cfg.sources[${i}].enabled=this.checked">
 <div style="flex:1;min-width:0"><input type=text value="${esc(s.name)}" placeholder=备注 onchange="cfg.sources[${i}].name=this.value" style="width:100%;margin-bottom:4px">
 <input type=text value="${esc(s.url)}" onchange="cfg.sources[${i}].url=this.value" style="width:100%"></div>
 <button class=red onclick="if(confirm('删除这个源？')){cfg.sources.splice(${i},1);drawSrc()}">删</button></div>`).join('')||'<div class=muted>还没有源</div>'}
function addSrc(){const u=$('nu').value.trim();if(!/^https?:\/\//.test(u))return toast('请输入 http(s) 地址');cfg.sources.push({url:u,name:$('nn').value.trim(),enabled:true});$('nu').value=$('nn').value='';drawSrc();toast('已添加，记得保存')}
function mv(i,d){const r=cfg.group_rules,j=i+d;if(j<0||j>=r.length)return;[r[i],r[j]]=[r[j],r[i]];drawRules()}
function drawRules(){$('rules').innerHTML=cfg.group_rules.map((r,i)=>`<div class=item>
 <input type=text value="${esc(r.group)}" placeholder=分组名 style="flex:0 0 26%" onchange="cfg.group_rules[${i}].group=this.value">
 <input type=text value="${esc(r.keywords)}" placeholder="关键词，逗号分隔" onchange="cfg.group_rules[${i}].keywords=this.value">
 <button class=gray onclick=mv(${i},-1)>↑</button><button class=gray onclick=mv(${i},1)>↓</button>
 <button class=red onclick="cfg.group_rules.splice(${i},1);drawRules()">删</button></div>`).join('')}
async function status(){const s=await api('/api/status');const pct=s.total?Math.round(s.progress*100/s.total):0;
 $('st').innerHTML=`状态：<b>${esc(s.status)}</b>${s.total&&s.status=='测速中'?`（${s.progress}/${s.total}）`:''}<br>
 上次完成：${s.last_run||'-'}，频道 <b>${s.channels}</b> 个，可用 ${s.alive}/${s.tested} 条，用时 ${s.duration_s}s<br>下次自动测速：${s.next_run||'手动'}`;
 const busy=['测速中','拉取源','正在停止'].includes(s.status);$('btnstop').hidden=!busy||s.status=='正在停止';$('btnrun').disabled=busy;$('btnrun').style.opacity=busy?.5:1;
 $('pg').style.width=(s.status=='测速中'?pct:(s.last_run?100:0))+'%';
 $('sst').innerHTML='<tr><th>源</th><th>条数</th><th>可用</th></tr>'+Object.entries(s.source_stats||{}).map(([u,v])=>
 `<tr><td style="word-break:break-all">${esc(u)}</td><td>${v.used}/${v.total}</td><td>${v.error?`<span class=bad>${esc(v.error)}</span>`:`<span class=ok>${v.alive}</span>`}</td></tr>`).join('')}
async function stopRun(){if(!confirm('停止本次测速？订阅保留上次结果'))return;const r=await api('/api/stop',{});toast(r.ok?'正在停止…':r.error);status()}
async function refresh(){const r=await api('/api/refresh',{});toast(r.ok?'已开始重新测速':r.error);status()}
async function loadResult(){try{report=await (await fetch('/report')).json()}catch(e){report=[]}drawResult()}
function drawResult(){const f=$('rf').value.trim().toUpperCase(),rows=report.filter(r=>!f||r.name.toUpperCase().includes(f));$('rc').textContent=`共 ${report.length} 条`;
 $('rt').innerHTML='<tr><th>分组</th><th>频道</th><th>码率</th><th>分辨率</th></tr>'+rows.slice(0,500).map(r=>
 `<tr><td>${esc(r.group)}</td><td><a href="${esc(r.url)}" target=_blank>${esc(r.name)}</a></td><td>${(r.kbps/1000).toFixed(1)} Mbps</td><td>${esc(r.res)||'-'}</td></tr>`).join('')}
$('u1').textContent=location.origin+'/m3u';$('u2').textContent=location.origin+'/txt';
load();status();setInterval(()=>{if(tab=='status')status()},3000);
</script></html>"""


if __name__ == "__main__":
    log(f"启动，端口 {PORT}，配置 {CFG_PATH}")
    threading.Thread(target=scheduler, daemon=True).start()
    ThreadingHTTPServer(("", PORT), H).serve_forever()
