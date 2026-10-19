# iStoreOS 原生插件（不需要 Docker）

这个插件将同一份 `speedtest/app.py` 交给 OpenWrt 的 procd 管理，并在 LuCI 的「服务 → IPTV 测速优选」提供启停、监听地址、端口和管理密码入口。原来的 [Docker 部署](../README.md) 继续保留。

适用于有 Python 3 和现代 LuCI 的 OpenWrt/iStoreOS。已在斐讯 N1、iStoreOS 25.12.5、`aarch64_cortex-a53`、APK 3.0.5 实机安装。25.12 使用的是 OpenWrt 的 APK，不是 Android APK；不要在该系统使用 `opkg` 或 `.ipk`。

## 安装

将按下文构建的包传到路由器，先核对它的 SHA-256，再安装：

```sh
# 在路由器上执行；路径按实际上传位置调整。
sha256sum luci-app-iptv-speedtest-1.0.0-r3.aarch64_cortex-a53.apk
apk add --simulate --allow-untrusted ./luci-app-iptv-speedtest-1.0.0-r3.aarch64_cortex-a53.apk
apk add --allow-untrusted ./luci-app-iptv-speedtest-1.0.0-r3.aarch64_cortex-a53.apk
```

这是本地未签名包，因此仅对这一条安装命令使用 `--allow-untrusted`；不修改系统的信任设置，也不替换软件源。安装前的模拟操作应只新增插件及其依赖，若出现核心网络包升级或删除应停止检查。**不要执行整机 `apk upgrade`。**

依赖由包管理器处理：`python3-light`、`python3-urllib`、`python3-openssl`、`python3-logging`、`python3-codecs`、`luci-base`、`rpcd-mod-file`。`logging` 和 `idna` 编码在 OpenWrt 上是单独拆包的，只有 python3-light 不够。

全新安装默认不启动。在 LuCI 重新登录或刷新后，打开「服务 → IPTV 测速优选」，填写**本路由器实际的 LAN IPv4 地址**和空闲端口，勾选启用，保存并应用。

也可通过 SSH 操作，先把下面的占位符改为实际 LAN 地址：

```sh
uci set iptv_speedtest.main.listen_host='路由器实际LAN地址'
uci set iptv_speedtest.main.port='8899'
uci set iptv_speedtest.main.enabled='1'
uci commit iptv_speedtest
/usr/libexec/iptv-speedtest-ctl apply
```

插件只绑定指定地址，不修改网络、DNS、路由、防火墙或 WAN/LAN 配置，不重启路由器。端口冲突会报错，不会停止占用端口的其他服务。默认回环地址 `127.0.0.1` 不能供其他局域网设备访问；不建议使用 `0.0.0.0`，也不要向公网转发此端口。

## 开始使用

打开 `http://路由器LAN地址:8899/`。首次启动会生成随机管理密码，在 LuCI 点击「查看管理密码」即可取得。该密码用于读取管理配置、结果及修改操作；M3U/TXT 订阅不需要管理密码，所以只能对受信任的局域网开放。HTTP 本身不加密，不要公开访问。

应用网页继续提供直播源、分组、选项、成功和失败列表。插件首次创建配置时使用并发 **4**、**只手动测速**、**不启动即测速**，以减少软路由带宽负担；之后以网页保存的 `config.json` 为准。应用定时在 Python 服务内部执行，不创建 cron。路由器重启后由 procd 恢复服务，但不会因此自动触发测速，除非用户在应用设置中明确开启。

- M3U：`http://路由器LAN地址:8899/m3u`
- TXT：`http://路由器LAN地址:8899/txt`
- 运行状态：`http://路由器LAN地址:8899/api/status`

没有测速结果时订阅接口返回 **503**，这是预期行为，不是假造可用频道。完成一次测速后才生成结果。只测过样本不代表整个仓库的频道都可播放。

## 数据在哪里

| 路径 | 用途 |
|---|---|
| `/etc/config/iptv_speedtest` | 启用开关、监听 IPv4、端口 |
| `/etc/iptv-speedtest/config.json` | 直播源、分组、测速和定时设置 |
| `/etc/iptv-speedtest/best.m3u`、`best.txt` | 最近一轮输出 |
| `/etc/iptv-speedtest/report.json`、`failed.json`、`state.json` | 结果和状态 |
| `/etc/iptv-speedtest/admin-password` | 自动生成的管理密码，600 权限 |
| `/usr/share/iptv-speedtest/app.py` | 与 Docker 共用的程序 |

整个数据目录权限为 700；原生程序创建的文件使用私有权限。升级和重启保留数据。不会自动读取或迁移旧容器的 `/opt/iptv-speedtest/data`，也不会碰另一套筛选插件的 `/iptv.m3u`。如要迁移旧配置，应停本插件、备份双方目录后手工复制选定文件，保留原件。

## 构建 APK

在独立 Linux 开发机上准备 **apk-tools 3，且包含 `mkpkg` 子命令**。目标路由器自带的 apk 往往不包含构建命令，不用在路由器上安装编译工具。

```sh
# 在仓库根目录。APK_TOOL 换为开发机上实际的 apk-tools 3 完整路径。
sudo python3 speedtest/openwrt/build_apk.py \
  --apk /absolute/path/to/apk \
  --arch aarch64_cortex-a53 \
  --output /tmp/iptv-build
```

构建器在一次性临时目录组装文件，不执行仓库其他脚本，不下载依赖，不写路由器；要求 root 是为了把包中文件所有者统一为 root，而不是开发机用户名。它生成 `.apk` 和 `.sha256`，不嵌入配置、密码或频道结果，并规范目录及文件权限。兼容参数会保留父目录节点，已验证目标 APK 3.0.5 能解包。

构建时对 apk 工具本身使用可信来源和固定版本。本次使用 Alpine apk-tools-static 3.0.8 的 `apk.static`，而不是路由器上的 apk。使用其他发行版工具时也必须确认是 APK v3。

### OpenWrt SDK

也提供 `Makefile`。把整个 `speedtest/` 目录复制到 SDK 的 `package/iptv-speedtest/`，保持 `openwrt/` 与 `app.py` 的相对位置，然后选择包并编译：

```sh
make menuconfig
make package/iptv-speedtest/openwrt/compile V=s
```

SDK 按其固件版本决定生成 APK 或 IPK。**SDK 路径尚未在本次任务实编译；旧固件 IPK 安装及旧版 LuCI 兼容性未实测。**本次实机使用的是上述独立 APK 构建器。

## 检查和服务管理

```sh
ubus call service list '{"name":"iptv-speedtest"}'
/etc/init.d/iptv-speedtest enabled && echo '开机自启已打开'
logread -e iptv-speedtest
/usr/libexec/iptv-speedtest-ctl stop
/usr/libexec/iptv-speedtest-ctl start
/usr/libexec/iptv-speedtest-ctl restart
```

`start` 要求 UCI 已启用；`stop` 是临时停止。永久停用请关闭 UCI 的 enabled 并执行控制器的 `apply`，它会同时移除本插件的开机自启链接。保存并应用只提交这个 UCI 配置，不会应用其他尚未提交的网络设置。

LuCI 需要重新登录才会取得新增的 ACL；不需要重启 uhttpd/rpcd。若只是菜单缓存未更新，可以在维护时清理 LuCI 自身的索引缓存后刷新，勿通过重启网络来解决。插件 ACL 只开放本配置、服务列表、固定密码文件和只接受固定操作的控制器。

## 升级和回滚

修改前先备份本插件。备份里可能有私有订阅及管理密码，不要上传到公共仓库。

```sh
umask 077
mkdir -p /root/iptv-speedtest-backup
cp -a /etc/config/iptv_speedtest /root/iptv-speedtest-backup/
cp -a /etc/iptv-speedtest /root/iptv-speedtest-backup/
# 再安装更高版本的本地包；升级只重启本插件。
apk add --allow-untrusted ./新版本.apk
```

恢复旧版时先停止服务，安装保留的明确版本 APK（`apk add --allow-untrusted ./旧版本.apk`，务必先用 `--simulate` 确认包管理器会选择这个本地版本），再恢复备份配置及数据并启动。若版本约束阻止降级，应先排查约束，不使用强制覆盖核心包的选项。不要用覆盖整个 `/etc` 的方式回滚。

只撤销此次插件安装：

```sh
/usr/libexec/iptv-speedtest-ctl stop
/etc/init.d/iptv-speedtest disable
apk del luci-app-iptv-speedtest
```

卸载脚本停止并禁用服务，**保留 `/etc/iptv-speedtest` 数据目录**。无需删除其他插件、容器数据或网络配置。包管理器可能清理不再被使用的依赖，先用 `apk del --simulate luci-app-iptv-speedtest` 查看影响；若想保留某个共享依赖，先把它显式加入 world。需要再次安装时保留的数据可继续使用。

## 测试与许可证

开发机测试（无公网访问）：

```sh
python3 -m unittest discover -s speedtest/tests -v
node --check speedtest/openwrt/files/www/luci-static/resources/view/iptv-speedtest/config.js
sh -n speedtest/openwrt/files/etc/init.d/iptv-speedtest
```

本次 10 项自动化测试通过，覆盖密码文件、权限、配置持久化、HTTP 认证、订阅接口、跨站请求限制、精简 Python 的 `tzset` 兼容、不设密码的原 Docker 用法，以及复测期间的停止和防重复触发。

N1 实机已验证：APK 完整性及隔离解包、Python 导入和解析、安装及两次本插件升级、procd 运行、开机自启链接、LuCI 登录后的页面路由和固定密码读取、管理接口认证、M3U/TXT HTTP 200、升级后配置与结果保留。网络、DHCP、firewall 配置的安装前后 SHA-256 相同；没有整机重启，也未用重启验证开机自启。

为避免全量测速抢占带宽，只用 3 条仓库内的公开频道做过低并发样本测试：CGTN 英语被保留，程序识别为 1280×720、约 2132 kbps；CCTV-1 被流畅度规则淘汰，CGTN 俄语超时。该轮最终用时约 121 秒；测试时暴露了旧程序在复测状态不接受停止的问题，已修复并加入回归测试。之后恢复仓库完整订阅配置、并发 4、手动模式，并保留这 1 条真实样本输出供接口验证。未跑全量频道筛选，未在电视或播放器做持续解码播放验证，不能把样本可达性当成所有频道可播放。

文件格式和接口检查、短时真实源测速，与电视端长时间播放是不同层次的测试，不应混为一谈。

仓库当前没有独立 LICENSE 文件，已有 README 的使用说明原样保留；本次不擅自替作者授予开源许可证。APK 元数据使用 `NOASSERTION`，并不构成新的许可授权。
