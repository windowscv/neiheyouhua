<!-- 说明高吞吐内核配置的使用、生效范围和隔离测试方法。 -->
# Linux 高吞吐、高并发网络优化

`neihe.sh` 为通用 Linux 服务器提供偏进取的 TCP 配置，面向 Web/API、下载和代理等高吞吐、高并发业务。需要 root 权限及支持 BBR、fq 的内核；Debian 13 可使用发行版内核。依赖 Bash、procps、kmod 和 coreutils。

## 使用

保留原有一键调用方式，root 用户可去掉 `sudo`：

```bash
curl -fsSL https://raw.githubusercontent.com/windowscv/neiheyouhua/master/neihe.sh | sudo bash
```

只预览参数，不修改系统：

```bash
curl -fsSL https://raw.githubusercontent.com/windowscv/neiheyouhua/master/neihe.sh | bash -s -- --dry-run
```

本地使用：

```bash
bash neihe.sh --dry-run
sudo bash neihe.sh
```

脚本成功应用参数后，**强制覆盖 `/etc/sysctl.d/99-sysctl.conf`**。重复运行生成相同配置，不做旧版检测或迁移，不自动重启。

## 参数策略

| 调整 | 设置 | 目的 |
| --- | --- | --- |
| 拥塞控制与队列 | BBR + fq | 对本机 TCP 连接控制发送节奏 |
| TCP 缓冲区 | 初始 256 KiB，上限 64 MiB | 为大带宽、高 RTT 链路留出自动调节空间 |
| 收包队列 | 65536 个包 | 容纳更多突发流量 |
| 单轮收包预算 | 600 个包 / 4000 微秒 | 增加收包处理预算，仍保留时间上限 |
| 完成握手的连接队列上限 | 65535 | 接纳更多等待应用处理的连接 |
| SYN 队列上限 | 65536 | 接纳更多并发握手 |
| 出站端口范围 | 10240–65535 | 扩大自动分配端口池 |
| TIME_WAIT 复用 | 全局开启 | 复用满足内核安全条件的连接 |
| 孤立 FIN_WAIT_2 超时 | 30 秒 | 加快回收无应用持有的半关闭连接 |
| MTU 探测 | 检测到黑洞时启用 | 应对部分路径 MTU 问题 |
| 空闲后的慢启动 | 关闭窗口衰减 | 提高恢复发送时的吞吐，可能产生更大突发 |
| Fast Open | 客户端与服务端允许使用 | 应用配合时减少握手等待 |
| Keepalive | 空闲 300 秒，间隔 30 秒，5 次探测 | 更早清理失联连接；需应用启用 SO_KEEPALIVE |

64 MiB 是上限，不会在创建连接时全部分配；更多连接、较大缓冲区和队列也可能增加内存占用及排队延迟。这组参数选择吞吐和并发优先，实际性能取决于 CPU、内存、带宽、RTT 和应用。

TCP 重试、TIME_WAIT 数量上限、通用套接字默认缓冲区、连接跟踪超时、转发和虚拟内存策略均不在本配置中重设。系统已有的其他配置仍可能影响它们。

## 生效与应用配合

- BBR 的默认选择主要影响后续新建 TCP 连接，不会改变已有连接的算法，也不改变纯转发流量或 UDP/QUIC 的拥塞控制。
- `default_qdisc` 不替换现有网卡的队列。需要在维护窗口按实际网卡配置 fq，或重启后检查；这里不自动改网卡、路由或重启机器。
- 删除旧配置中的某个参数不会自动重置其当前运行值。重启后才会按剩余启动配置重新设置。
- 扩大端口范围后，固定业务端口可以按需通过 `net.ipv4.ip_local_reserved_ports` 预留；设置时保留已有预留项。
- 应用的 `listen()` backlog、连接数、工作进程和文件描述符限制仍需配合调整。需要大量连接的 systemd 服务，可通过该服务的 override 设置 `LimitNOFILE=1048576`，然后重启该服务；脚本不改全局资源限制。
- 参数应用失败会返回非零状态，不覆盖持久化文件。已经成功设置的运行值可能部分生效，需根据报错处理后重试。

检查当前默认算法和实际网卡队列：

```bash
sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc
tc qdisc show
ss -tin
```

内核行为参考：[TCP 参数](https://docs.kernel.org/networking/ip-sysctl.html)、[收包预算和缓冲区](https://docs.kernel.org/admin-guide/sysctl/net.html)、[fq](https://manpages.debian.org/trixie/iproute2/tc-fq.8.en.html)。

## 测试

测试执行真实脚本，在 Debian 13 容器内验证覆盖配置、保留其他系统文件、重复运行、预览、权限、模块失败和参数应用失败。仅替代内核命令边界，避免测试改变宿主机内核。请在隔离容器中运行，不要直接在生产机运行测试：

```bash
docker run --rm \
  --mount type=bind,src="$PWD",dst=/repo,readonly \
  --workdir /repo debian:13-slim \
  bash -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 && python3 -B -m unittest discover -s tests -v'
```

语法检查：

```bash
bash -n neihe.sh
```
