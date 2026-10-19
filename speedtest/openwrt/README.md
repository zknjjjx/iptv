# iStoreOS 原生 IPTV 插件

原生插件将同一份 `speedtest/app.py` 交给 procd 管理，不需要 Docker。原有 [Docker 部署](../README.md) 和独立管理网页保持兼容。

从 **1.0.0-r4** 起，在 **服务 → IPTV 测速优选** 内直接管理，不再需要跳转到 `192.168.2.254:8899`。页面沿用 LuCI 主题和顶部多页导航。

## 五个页面

| 页面 | 现有功能 |
|---|---|
| 状态与服务 | 启用/启停、监听 IPv4/端口、测速进度、上次/下次运行、来源统计、开始/停止测速、复制订阅地址 |
| 直播源 | 源名称/地址/启用开关、增删、单源解析测试、拉取代理 |
| 分组 | 规则分组/原分组、规则增删及排序、未匹配分组 |
| 测速选项 | 间隔/开始时间、并发/超时/复测、保留条数、流畅度/码率/分辨率筛选、IP 版本、黑名单、EPG、合并及启动即测速 |
| 测速结果 | 成功/失败列表、搜索、分页、测速指标、失败原因筛选及失败 TXT 导出 |

设置项目与原 Docker 版网页对齐，使用 LuCI 主题重新组织；旧独立网页保持兼容。列表中编辑后增删或排序不会丢失当前输入；各设置页可重新载入撤销未保存修改。

每页只保存自己的配置字段，不会用另一页的旧值覆盖其它设置。保存应用配置不自动测速；只有明确点击开始测速，或已配置的定时/启动即测速，才会运行。开始测速会消耗带宽及连接数，需确认。

“状态与服务”的“保存并应用”只提交本插件 UCI 并重启本插件，**不应用其它尚未提交的网络配置**。监听设置与其它页面的 JSON 应用配置分别保存。

## 认证与后台接口

浏览器只调用 LuCI 同源 `/ubus` 的 `iptv-speedtest` 对象；没有 iframe、重定向或浏览器跨端口管理请求。使用 [rpcd 可执行扩展协议](https://openwrt.org/docs/techref/rpcd)，由 LuCI 会话和 ACL 保护管理操作。

桥接程序只接受固定方法，目标来自固定 UCI 字段并通过本机 IPv4 绑定检查。后台密码只在路由器服务端读取并发送到本机后台，不返回给前端。新版 ACL 不再授予密码文件读取权限，也移除了“查看管理密码”按钮。服务控制仍通过只接受固定操作的控制器，文件 ACL 限定其路径。

原后台 API 和 Docker 网页保留兼容，因此此更新**不是关闭后台 8899 端口**。外部播放器仍使用：

- `http://路由器LAN地址:8899/m3u`
- `http://路由器LAN地址:8899/txt`

LuCI 页面仅展示和复制这些地址，不带用户导航离开 LuCI。首次没有结果时订阅返回 503。`127.0.0.1` 监听只能本机访问；若需要电视访问，保持监听实际 LAN 地址。不建议使用 `0.0.0.0`，不要向公网转发无认证的订阅端口。

普通后台请求超时 5 秒；单源解析测试最多等待 25 秒，适配 rpcd 默认 30 秒限制。慢源可能超时，后台已启动的拉取可能尚未结束，界面不自动重复请求。请求体及普通响应限制 1 MiB，结果读取上限 16 MiB，分页每次最多 100 条。达到保护限制时报错，不修改或丢弃频道数据。

## 安装及更新

适用于有 Python 3 和现代 LuCI 的 OpenWrt/iStoreOS。N1 参考平台是 iStoreOS 25.12.5 / `aarch64_cortex-a53`，使用 **OpenWrt APK v3，不是 Android APK**，不要使用该机不适用的 `.ipk`/`opkg`。

先备份旧 APK、包文件、UCI 和整个数据目录。备份含私有源及密码，设为私有，勿提交 Git。

```sh
umask 077
b=/root/iptv-speedtest-backup-$(date +%Y%m%d-%H%M%S)
mkdir -p "$b"
tar -czf "$b/config-data.tar.gz" -C / etc/config/iptv_speedtest etc/iptv-speedtest
# 同时在此目录保留当前已安装版本的 APK。
```

上传新版后核对 SHA-256 并模拟安装；应只更新本插件，不升级/删除核心软件包。

```sh
sha256sum luci-app-iptv-speedtest-1.0.0-r4.aarch64_cortex-a53.apk
apk add --simulate --allow-untrusted ./luci-app-iptv-speedtest-1.0.0-r4.aarch64_cortex-a53.apk
apk add --allow-untrusted ./luci-app-iptv-speedtest-1.0.0-r4.aarch64_cortex-a53.apk
```

本地未签名包仅在这一条命令使用 `--allow-untrusted`，不改全局信任或软件源，**不执行整机 `apk upgrade`**。

更新仅重启已启用的本插件，通过 rpcd reload 注册方法/ACL，并清除 LuCI 菜单索引。不重启路由器、网络或 uhttpd，不改变网络、DNS、防火墙、WAN/LAN、主路由设置。旧会话未取得新权限时，重新登录 LuCI 后刷新。

全新安装不自动启动。在服务页选择监听地址/端口，勾选启用后保存并应用。初始并发 4、手动模式、不启动即测速；已有配置以磁盘数据为准，升级不重置。

依赖：`python3-light python3-urllib python3-openssl python3-logging python3-codecs luci-base rpcd-mod-file`。OpenWrt 的 logging 和 idna 编码为拆分依赖，仅 python3-light 不足。

## 数据和服务

| 路径 | 用途 |
|---|---|
| `/etc/config/iptv_speedtest` | 启用、监听 IPv4、端口 |
| `/etc/iptv-speedtest/config.json` | 源、分组、测速、定时 |
| `/etc/iptv-speedtest/best.m3u`、`best.txt` | 最近输出，升级保留 |
| `/etc/iptv-speedtest/report.json`、`failed.json`、`state.json` | 结果和状态 |
| `/etc/iptv-speedtest/admin-password` | 后台私有密码，权限 600，不送前端 |
| `/usr/libexec/rpcd/iptv-speedtest` | 固定 RPC 桥接 |
| `/usr/share/iptv-speedtest/app.py` | Docker/原生共用后台 |

数据目录权限 700。不会自动迁移或改动 Docker 的 `/opt/iptv-speedtest/data`，也不碰其它插件的 `/iptv.m3u`。定时由 Python 后台执行，不写 cron。

```sh
ubus call service list '{"name":"iptv-speedtest"}'
ubus -v list iptv-speedtest
/etc/init.d/iptv-speedtest enabled
/usr/libexec/iptv-speedtest-ctl start
/usr/libexec/iptv-speedtest-ctl stop
/usr/libexec/iptv-speedtest-ctl restart
```

本机 root 调试有管理权限；远程 LuCI 请求仍必须有会话及 ACL。`stop` 临时停止，永久禁用请在服务页取消启用后保存并应用。

## 回滚

先停止本插件；先模拟、再安装备份中的明确旧 APK。若模拟不选择旧本地版本或涉及其它包，先排查，不强制覆盖核心包。

```sh
/usr/libexec/iptv-speedtest-ctl stop
apk add --simulate --allow-untrusted /root/你的备份目录/luci-app-iptv-speedtest-1.0.0-r3.aarch64_cortex-a53.apk
apk add --allow-untrusted /root/你的备份目录/luci-app-iptv-speedtest-1.0.0-r3.aarch64_cortex-a53.apk
tar -xzf /root/你的备份目录/config-data.tar.gz -C /
/etc/init.d/rpcd reload
rm -f /tmp/luci-indexcache.*
/usr/libexec/iptv-speedtest-ctl apply
```

只恢复本插件，不覆盖整个 `/etc`。卸载保留数据目录；先 `apk del --simulate luci-app-iptv-speedtest` 查看依赖处理，不删除共享依赖或其它插件。

## 构建与检查

独立 Linux 开发机使用可信来源 apk-tools 3（含 `mkpkg`，参考版本 3.0.8），不在路由器安装构建工具：

```sh
sudo python3 speedtest/openwrt/build_apk.py \
  --apk /absolute/path/to/apk.static \
  --arch aarch64_cortex-a53 --output /tmp/iptv-build
python3 -m unittest discover -s speedtest/tests -v
node speedtest/tests/test_luci_views.js
find speedtest/openwrt/files/www -name '*.js' -exec node --check {} \;
sh -n speedtest/openwrt/files/etc/init.d/iptv-speedtest
```

临时目录组装，不下载依赖、不嵌入私有数据，生成 APK 和 SHA-256。root 仅用于统一包所有者。保留 SDK `Makefile`，保持 `openwrt/` 与 `app.py` 相对路径；SDK 编译及旧版 LuCI/IPK 兼容本次未实测。

自动化测试用本机 HTTP 或 mock，不触发公网全量测速。接口/格式/解析、程序识别的码率/分辨率，与电视播放器持续解码是不同层级：没有实际播放就不声称验证了播放。保留旧版样本输出，不代表本次重新测速或所有频道可用。

仓库未声明独立 LICENSE，本次不擅自添加许可授权；包元数据 `NOASSERTION` 不构成新许可。
