# iptv-speedtest

自研的直播源测速优选服务：定时拉取订阅源 → 逐条测速 → 每个频道只保留**码率最高且能流畅播放**的一条 → 在局域网提供订阅地址。纯 Python 标准库，镜像约 50MB，支持 amd64 / arm64 / armv7 软路由。

## 网页设置面板
浏览器打开 `http://软路由IP:8899`，五个页签：
- **状态**：测速进度、订阅地址、每个源的可用条数，一键重测
- **直播源**：添加 / 删除 / 暂停订阅源（m3u、txt 均可）
- **分组**：按关键词规则分组（可增删、排序），或保留源里的原始分组
- **选项**：重测间隔、每频道保留条数、并发、超时、流畅系数、最低码率、IPv4/IPv6、屏蔽关键词、合并同名频道、EPG 地址
- **结果**：每个频道选中的链接、码率、分辨率，可搜索

设置保存在 `data/config.json`。设置环境变量 `ADMIN_PASSWORD` 后，改设置需输入密码（订阅地址不受影响）。

## 测速原理
- **HLS (m3u8)**：主列表选 BANDWIDTH 最高的子流，下载一个完整分片，`码率 = 分片大小 ÷ 分片时长`，`速度 = 分片大小 ÷ 下载用时`
- **flv / ts 等直连流**：读 5 秒，按平均速率估算码率
- 下载速度 < 码率 × 1.2 视为会卡，淘汰；mp4 点播占位视频直接丢弃
- 同名频道（忽略“高清/HD/空格/横杠”）中选码率最高的

## 部署（软路由 / NAS / 任意 Linux）
```bash
mkdir -p /opt/iptv-speedtest && cd /opt/iptv-speedtest
wget -O docker-compose.yml https://raw.githubusercontent.com/zknjjjx/iptv/main/speedtest/docker-compose.yml
wget -O app.py https://raw.githubusercontent.com/zknjjjx/iptv/main/speedtest/app.py
docker compose up -d
docker logs --tail 20 iptv-speedtest
```
无需构建镜像，不依赖 git / buildx。启动后不会立即测速，到网页点“立即测速”或等定时；测速中可点“停止测速”。更新程序：重新下载 app.py 后 `docker restart iptv-speedtest`。
没有 compose 时：
```bash
docker build -t iptv-speedtest https://github.com/zknjjjx/iptv.git#main:speedtest
docker run -d --name iptv-speedtest --restart unless-stopped --network host \
  -e PORT=8899 -v /opt/iptv-speedtest/data:/data iptv-speedtest
```

## 使用
浏览器打开 `http://软路由IP:8899`：

| 地址 | 内容 |
|---|---|
| `/m3u` | 优选 M3U 订阅 |
| `/txt` | 优选 TXT 订阅 |
| `/report` | 每条的码率、速度、分辨率 |
| `/status` | 运行状态 |
| `/refresh` | 立即重新测速 |

## 环境变量
| 变量 | 默认 | 说明 |
|---|---|---|
| SOURCES | 本仓库 iptv.m3u | 订阅源，逗号分隔，支持 m3u/txt |
| INTERVAL_HOURS | 12 | 重测间隔，0 为只跑一次 |
| KEEP | 1 | 每频道保留条数 |
| WORKERS | 32 | 并发数，弱软路由可调 16 |
| TIMEOUT | 8 | 单次请求超时（秒） |
| SMOOTH_RATIO | 1.2 | 速度需达到码率的倍数 |
| MIN_KBPS | 0 | 最低码率 |
| PORT | 8080 | 服务端口 |

> 软路由若开了代理，请让该容器直连，否则测的是代理线路。
