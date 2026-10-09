# iptv

中国大陆电视直播源合集（已去除重复链接），**每天北京时间 2:00 起每 8 小时自动更新一次**（2:00、10:00、18:00）。

| 文件 | 说明 |
|---|---|
| `iptv.m3u` | M3U 格式，适用于 TiviMate、Kodi、PotPlayer、APTV 等 |
| `iptv.txt` | TXT 格式（`频道名,链接`，`分组,#genre#`），适用于 DIYP、TVBox 等 |

订阅地址（国内访问慢可在前面加代理，如 `https://9797.cc.cd/`）：
- https://raw.githubusercontent.com/zknjjjx/iptv/main/iptv.m3u
- https://raw.githubusercontent.com/zknjjjx/iptv/main/iptv.txt

## 测速优选工具
直播源很多是失效或卡顿的。[speedtest](speedtest/) 是配套的自研测速服务，部署在软路由 / NAS 上：定时拉取本仓库或任意订阅源，逐条测码率、分辨率和流畅度，每个频道只留最好的一条，在局域网输出 M3U / TXT 订阅，全部设置在网页里完成。

```bash
mkdir -p /opt/iptv-speedtest && cd /opt/iptv-speedtest
wget --no-hsts -T 30 -O docker-compose.yml "https://9797.cc.cd/https://raw.githubusercontent.com/zknjjjx/iptv/main/speedtest/docker-compose.yml"
wget --no-hsts -T 30 -O app.py "https://9797.cc.cd/https://raw.githubusercontent.com/zknjjjx/iptv/main/speedtest/app.py"
docker compose up -d
```
然后打开 `http://软路由IP:8899`。详见 [speedtest/README.md](speedtest/README.md)。

## 来源与自动更新
汇总自以下公开项目，按链接（去掉 `$` 后缀）去重，央视、卫视单独分组：
- [Guovin/iptv-api](https://github.com/Guovin/iptv-api)（gd 分支 result.m3u）
- [YueChan/Live](https://github.com/YueChan/Live)（IPTV.m3u）
- [fanmingming/live](https://github.com/fanmingming/live)（ipv6.m3u）
- [iptv-org/iptv](https://github.com/iptv-org/iptv)（cn.m3u）
- [suxuang/myIPTV](https://github.com/suxuang/myIPTV)（ipv4.m3u）
- [vbskycn/iptv](https://github.com/vbskycn/iptv)（iptv4.m3u）

更新由 GitHub Actions（[`.github/workflows/update.yml`](.github/workflows/update.yml)）运行 [`scripts/update.py`](scripts/update.py) 完成：
- 定时：北京时间每天 02:00、10:00、18:00（cron `0 18,2,10 * * *`，UTC）。GitHub 的定时任务高峰期可能推迟几分钟到半小时。
- 每次运行都会重新生成并提交，无论结果多少、有无变化；`iptv.m3u` 第二行记录本次更新时间。某个来源拉取失败时跳过该来源。
- 想立即更新：仓库 Actions → 更新直播源 → Run workflow。
- 本地生成：`python3 scripts/update.py`（只用 Python 标准库）。

## 说明
直播源来自网络公开链接，可用性随时变化，部分源仅支持 IPv6。仅供学习交流，请勿用于商业用途。
