#!/usr/bin/env bash
# 应用偏高吞吐、高并发的 TCP 参数，强制覆盖本脚本的 sysctl 配置。
set -euo pipefail

print_config() {
  cat <<'EOF'
# neiheyouhua：高吞吐、高并发 TCP 配置。
# BBR 配合 fq 控制发送节奏，主要影响本机发起或终止的 TCP 连接。
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr

# TCP 初始缓冲区为 256 KiB，自动调节上限为 64 MiB，按流量需求增长。
net.core.rmem_max = 67108864
net.core.wmem_max = 67108864
net.ipv4.tcp_rmem = 4096 262144 67108864
net.ipv4.tcp_wmem = 4096 262144 67108864

# 提高收包处理预算和连接队列上限，接纳更多并发连接与突发流量。
net.core.netdev_max_backlog = 65536
net.core.netdev_budget = 600
net.core.netdev_budget_usecs = 4000
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65536

# 扩大出站端口池；全局复用满足内核安全条件的 TIME_WAIT 连接。
net.ipv4.ip_local_port_range = 10240 65535
net.ipv4.tcp_tw_reuse = 1
# 仅缩短孤立 FIN_WAIT_2 状态的等待，不改变 TIME_WAIT 时长。
net.ipv4.tcp_fin_timeout = 30

# 遇到 MTU 黑洞时启动探测；保留空闲前的拥塞窗口，提高恢复发送速度。
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_slow_start_after_idle = 0
# Fast Open 仍需应用配合启用。
net.ipv4.tcp_fastopen = 3

# 对启用 SO_KEEPALIVE 的连接更早检测失联，避免长期占用连接资源。
net.ipv4.tcp_keepalive_time = 300
net.ipv4.tcp_keepalive_intvl = 30
net.ipv4.tcp_keepalive_probes = 5
EOF
}

case "$*" in
  --dry-run) print_config; exit 0 ;;
  --help) echo '用法：bash neihe.sh [--dry-run | --help]'; exit 0 ;;
  '') ;;
  *) echo '用法：bash neihe.sh [--dry-run | --help]' >&2; exit 2 ;;
esac

if (( EUID != 0 )); then
  echo '请使用 root 或 sudo 执行。' >&2
  exit 1
fi
if [[ $(uname -s) != Linux ]]; then
  echo '仅支持 Linux 系统。' >&2
  exit 1
fi

# 先准备内核模块；内核未提供所需功能时直接报错。
available=$(sysctl -n net.ipv4.tcp_available_congestion_control)
if [[ " $available " != *" bbr "* ]]; then
  modprobe tcp_bbr
fi
modprobe sch_fq

# 只加载本次配置；成功后强制覆盖旧文件，不重新加载其他 sysctl 文件。
temp_config=$(mktemp)
trap 'rm -f "$temp_config"' EXIT
print_config > "$temp_config"
sysctl -p "$temp_config"
install -D -m 0644 "$temp_config" /etc/sysctl.d/99-sysctl.conf

echo '高吞吐、高并发参数已应用，配置已写入 /etc/sysctl.d/99-sysctl.conf。'
echo 'BBR 用于后续新建 TCP 连接；fq 默认队列用于新建网卡。'
echo '现有网卡请在维护窗口切换队列或重启后确认；脚本不会自动重启。'
