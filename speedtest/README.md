# iptv-speedtest

自研的直播源测速优选服务，跑在软路由 / NAS 上：

拉取订阅源 → 逐条测速（m3u8、ts、flv、RTSP） → 测出码率、分辨率、能否流畅播放 → 每个频道留下最好的一条 → 在局域网提供 M3U / TXT 订阅。

- 纯 Python 标准库，直接用官方 `python:3.12-alpine` 镜像，**无需构建**，不依赖 git / buildx
- 支持 amd64 / arm64 / armv7（斐讯 N1、iStoreOS、OpenWrt、群晖等）
- 全部设置在网页里完成，保存在 `data/config.json`

## 部署

```bash
mkdir -p /opt/iptv-speedtest && cd /opt/iptv-speedtest
wget --no-hsts -T 30 -O docker-compose.yml "https://9797.cc.cd/https://raw.githubusercontent.com/zknjjjx/iptv/main/speedtest/docker-compose.yml"
wget --no-hsts -T 30 -O app.py "https://9797.cc.cd/https://raw.githubusercontent.com/zknjjjx/iptv/main/speedtest/app.py"
docker compose up -d
docker logs --tail 20 iptv-speedtest
```

浏览器打开 `http://软路由IP:8899`。

> 国内拉不动 Docker Hub 时，先从镜像站拉再改名：
> ```bash
> docker pull docker.m.daocloud.io/library/python:3.12-alpine
> docker tag docker.m.daocloud.io/library/python:3.12-alpine python:3.12-alpine
> ```
> 备选镜像站：`docker.1ms.run/library/python:3.12-alpine`

### 更新程序

```bash
cd /opt/iptv-speedtest
wget --no-hsts -T 30 -O app.py "https://9797.cc.cd/https://raw.githubusercontent.com/zknjjjx/iptv/main/speedtest/app.py"
docker restart iptv-speedtest
docker logs --tail 20 iptv-speedtest
```

设置和上次结果都在 `data/` 里，更新不会丢。

## 网页面板

| 页签 | 内容 |
|---|---|
| 状态 | 运行状态、进度条、下次运行时间；**立即测速 / 停止测速**；订阅地址（带复制按钮）；测速中实时显示每个源的总数、已测、成功；下方是上次测速的统计 |
| 直播源 | **拉取代理**；添加 / 删除 / 暂停订阅源，填备注；每个源有「测试」按钮，看能不能拉到、有多少条 |
| 分组 | 按关键词规则分组（可增删、排序，从上到下匹配），或保留源里的原始分组 |
| 选项 | 定时、并发、超时、流畅判定、最低码率、最低分辨率、IP 类型、屏蔽词等 |
| 结果 | **成功**：每个频道选中的链接、码率、分辨率、实时倍率、首包延迟；**失败清单**：每条失败链接和原因，可按原因筛选、搜索、导出 txt |

停止测速不会覆盖上次的订阅结果。

## 订阅地址

| 地址 | 内容 |
|---|---|
| `http://软路由IP:8899/m3u` | 优选 M3U（带 EPG） |
| `http://软路由IP:8899/txt` | 优选 TXT（DIYP / TVBox 格式） |
| `/report` | 成功列表（JSON） |
| `/failed` | 失败清单（JSON） |
| `/api/status` | 运行状态（JSON） |

## 测速原理

**HLS（m3u8）**
- 主列表选 `BANDWIDTH` 最高的子流；没有 `.m3u8` 后缀但内容是 HLS 的也能识别
- 连续下载最近 3 个分片：第 1 个用来热身（CDN 回源、TCP 起步）并测首包延迟，后面的算速度
- `码率 = 分片大小 ÷ 分片时长`，`实时倍率 = 分片时长 ÷ 下载用时`

**ts / flv 直连流**
- 从收到第一个包开始计时，读约 7 秒
- 用 TS 的 PCR、FLV 的时间戳算出这段数据的真实播放时长，得到真实码率和实时倍率
- 跳转到 mp4 占位视频（例如“盗版提示”）的直接淘汰

**RTSP**
- `DESCRIBE → SETUP（TCP 交织）→ PLAY`，支持 301 / 302 跳转，接收约 6 秒数据
- 码率取 SDP 里的 `b=AS`，没有就按实收速率

**分辨率**：从视频数据里解析 H.264 / H.265 的 SPS（m3u8、ts、flv、RTSP 都支持），或取 m3u8 的 `RESOLUTION`。源本身不给视频数据时显示 `-`。

**流畅判定**
- 实时倍率 ≥「流畅倍率」→ 流畅
- 实时倍率 <「剔除倍率」→ 淘汰
- 介于两者之间 → **勉强**：保留但排在后面，某个频道只剩这种源时也会留一条
- 直连流和 RTSP 是服务器按实时推送，倍率最多≈1，能跟上实时（≥1）就算流畅
- 第一轮测完后，不流畅和勉强的会用低并发（「复测并发」）**再测一遍**，取较好的一次，避免并发抢带宽造成误判

**选优**：同名频道（忽略“高清 / HD / 空格 / 横杠”）里，流畅的优先，再按码率从高到低，保留「每频道保留条数」条。

## 选项说明

| 选项 | 默认 | 说明 |
|---|---|---|
| 启动即测速 | 关 | 容器启动后是否马上测一轮 |
| 每天开始时间 | 空 | 如 `03:00`；以此为基准按间隔重复。留空 = 从上次运行起算 |
| 重测间隔（小时） | 12 | 例：03:00 + 6 = 每天 3、9、15、21 点。有开始时间时填 0 = 每天一次；没有开始时间时 0 = 只手动 |
| 每频道保留条数 | 1 | 多留几条可在播放器里切换 |
| 并发数 | 32 | N1 这类软路由日常建议 16 |
| 超时（秒） | 8 | 连不上多久放弃 |
| 流畅倍率 | 1.2 | 实时倍率达到它算流畅 |
| 剔除倍率 | 0.5 | 低于它淘汰 |
| 复测并发 | 4 | 第二轮复测的并发 |
| 最低码率 kbps | 0 | 如 2000 ≈ 只要高清 |
| 最低分辨率 | 不限 | 576p / 720p / 1080p / 4K，低于它的淘汰 |
| 分辨率未知时保留 | 开 | 关掉则测不出分辨率的也淘汰 |
| IP 类型 | 全部 | 全部 / 仅 IPv4 / 仅 IPv6 |
| 屏蔽关键词 | 购物,测试,广告 | 频道名或链接含这些词就丢弃 |
| 丢弃点播占位视频 | 开 | 去掉 mp4 等点播和占位视频 |
| 合并同名频道 | 开 | `CCTV-1 高清` = `CCTV1` |
| EPG 地址 | fanmingming | 写进 M3U 头 |

**拉取代理**（直播源页）：只用于下载订阅源列表，不影响测速。先经代理拉，失败再直连，再试 jsDelivr 镜像。可选：`https://9797.cc.cd/`、`https://gh-proxy.com/`、`https://ghfast.top/`、`https://gh.llkk.cc/`。

## 默认分组

央视频道 → 卫视频道 → 科教文卫 → 港澳台 → 数字付费 → 少儿动画，其余归「地方频道」。都可以在「分组」页改。

## 环境变量

只在第一次启动时作为默认值，之后以网页设置为准。

| 变量 | 默认 | 说明 |
|---|---|---|
| PORT | 8080（compose 里是 8899） | 网页和订阅端口 |
| SOURCES | 本仓库 iptv.m3u | 订阅源，逗号分隔 |
| INTERVAL_HOURS | 12 | 重测间隔 |
| START_TIME | 空 | 每天开始时间，如 `03:00` |
| KEEP | 1 | 每频道保留条数 |
| WORKERS | 32 | 并发数 |
| TIMEOUT | 8 | 超时秒数 |
| PROXY | 空 | 拉取代理前缀 |
| ADMIN_PASSWORD | 空 | 设置后，改设置和触发测速需要密码（订阅地址不受影响） |
| TZ | Asia/Shanghai | 时区 |

## 常见问题

- **测得都很慢 / 大量不流畅**：软路由若开了代理，让这个容器直连，否则测的是代理线路；并发调到 16，必要时调高超时。
- **RTSP 源全失败**：RTSP 组播转单播源一般只有对应地区、对应运营商的网络能播。
- **某个源拉不下来**：在「直播源」页点「测试」看原因；设置拉取代理，或换一个代理。
- **看日志**：用 `docker logs --tail 20 iptv-speedtest`；不要用 `-f`，它会一直占着终端。
