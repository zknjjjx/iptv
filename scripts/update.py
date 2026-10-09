#!/usr/bin/env python3
"""拉取各公开直播源，按链接去重，生成 iptv.m3u / iptv.txt。由 GitHub Actions 定时运行。"""
import re, time, collections, urllib.request

SOURCES = [
    "https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/result.m3u",
    "https://raw.githubusercontent.com/YueChan/Live/main/IPTV.m3u",
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/ipv6.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u",
    "https://raw.githubusercontent.com/suxuang/myIPTV/main/ipv4.m3u",
    "https://raw.githubusercontent.com/vbskycn/iptv/master/tv/iptv4.m3u",
]


def fetch(url):
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            print(f"  第{i + 1}次失败: {e}")
            time.sleep(3)
    return None


def cat(name, grp):
    if re.search(r"CCTV|CETV|CGTN", name, re.I):
        return "央视频道"
    if "卫视" in name:
        return "卫视频道"
    return grp or "其他"


chans = collections.OrderedDict()
seen, total, ok = set(), 0, 0
for src in SOURCES:
    print("拉取", src)
    text = fetch(src)
    if text is None:
        print("  跳过")
        continue
    ok += 1
    info = None
    for l in text.splitlines():
        l = l.strip()
        if l.startswith("#EXTINF"):
            info = l
            continue
        if not l or l.startswith("#"):
            continue
        if not re.match(r"^(https?|rtmp|rtsp|p3p)://", l):
            info = None
            continue
        total += 1
        key = l.split("$")[0].strip()
        if key in seen:
            info = None
            continue
        seen.add(key)
        name = info.rsplit(",", 1)[-1].strip() if info else "Unknown"
        g = re.search(r'group-title="([^"]*)"', info or "")
        logo = re.search(r'tvg-logo="([^"]*)"', info or "")
        chans.setdefault(name, []).append((g.group(1) if g else "其他", logo.group(1) if logo else "", key))

print(f"成功 {ok}/{len(SOURCES)} 个来源，共 {total} 条，去重后 {len(seen)} 条，{len(chans)} 个频道")

now = time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 8 * 3600))
out = ['#EXTM3U x-tvg-url="https://live.fanmingming.cn/e.xml"', f"# 更新时间（北京时间）{now}"]
txt = collections.OrderedDict()
for n, items in chans.items():
    for grp, logo, u in items:
        c = cat(n, grp)
        out.append(f'#EXTINF:-1 tvg-name="{n}" tvg-logo="{logo}" group-title="{c}",{n}')
        out.append(u)
        txt.setdefault(c, []).append(f"{n},{u}")
open("iptv.m3u", "w", encoding="utf-8").write("\n".join(out) + "\n")
open("iptv.txt", "w", encoding="utf-8").write("".join(f"{c},#genre#\n" + "\n".join(v) + "\n\n" for c, v in txt.items()))
