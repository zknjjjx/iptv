# iptv

中国大陆电视直播源合集（已去除重复链接）。

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

## 来源
汇总自以下公开项目，按链接（去掉 `$` 后缀）去重，央视、卫视单独分组：
- iptv-org/iptv（cn.m3u）
- fanmingming/live
- YueChan/Live
- Guovin/iptv-api
- vbskycn/iptv
- suxuang/myIPTV

## 说明
直播源来自网络公开链接，可用性随时变化，部分源仅支持 IPv6。仅供学习交流，请勿用于商业用途。
