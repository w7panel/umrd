# UMRD - Userspace Memory Reclaimer Daemon

**版本**: 2.0.0  <!请在 pyproject.toml 中修改版本号 -->
**License**: [Apache-2.0](LICENSE)  
**cgroup支持**: 仅 cgroup v2

---

## 项目概述

> **致谢**: UMRD 2.0 基础代码源自 [Tencent Cloud umrd 1.0](https://github.com/TencentCloud/umrd)，并针对 cgroup v2 进行了深度适配和重构。

UMRD (Userspace Memory Reclaimer Daemon) 是用户空间内存回收守护进程，通过监控 cgroup 内存压力动态回收内存。

### 核心功能

| 功能 | 说明 |
|------|------|
| PSI感知回收 | 基于 Linux PSI 感知内存压力 |
| 双模式回收 | Simple 模式 + EMM 模式 |
| ZRAM压缩 | 自动启用内存压缩 |
| 超卖支持 | Page Reporting 智能调度 |
| K8s部署 | DaemonSet 方式部署 |

---

## 一键安装

```bash
# 方式1: 直接运行安装脚本
curl -sL https://raw.githubusercontent.com/w7panel/umrd/main/install.sh | bash

# 方式2: 克隆后安装
git clone https://github.com/w7panel/umrd.git
cd umrd
./install.sh
```

---

## Kubernetes 部署 (推荐)

### 前置要求

**1. cgroup v2**
```bash
# 检查 cgroup v2
ls /sys/fs/cgroup/
# 应显示 cgroup2... 而不是 memory/
```

**2. PSI (Pressure Stall Information)**
UMRD 需要 PSI 支持。如果 `/proc/pressure/memory` 不存在，需要启用 PSI：

```bash
# 检查 PSI
cat /proc/pressure/memory

# 如果不存在，启用 PSI (需要 root)
grubby --update-kernel=DEFAULT --args="psi=1"
reboot

# 验证
cat /proc/cmdline | grep psi
```

### 部署

```bash
# 应用 DaemonSet
kubectl apply -f k8s/daemonset.yaml

# 查看 Pod 状态
kubectl get pods -n kube-system -l app=umrd

# 查看日志
kubectl logs -n kube-system -l app=umrd -f

# 重启 Pod
kubectl rollout restart daemonset umrd -n kube-system
```

### K8s 部署说明

- **镜像**: `zpk.idc.w7.com/w7panel/umrd:latest`
- **Namespace**: `kube-system`
- **运行模式**: DaemonSet，每个节点一个 Pod
- **特权模式**: 需要 `privileged: true` 和 `hostPID: true`
- **cgroup**: CRI 自动挂载 cgroup v2，无需手动挂载
- **PSI**: 需要节点内核参数 `psi=1`

---

## 使用

```bash
# 查看帮助
umrd --help

# 查看状态
cat /run/umrd/status

# 查看日志
tail -f /run/umrd/umrd.log

# 查看全局内存统计
cat /run/umrd/mem_save

# 查看各 cgroup 回收详情
cat /run/umrd/cgroup_mem_save

# 热更新配置 (修改日志级别)
echo "log_file_handler_level=debug" > /run/umrd/hot_reload.cfg
```

---

## 配置文件

### Allowlist

指定要回收的 cgroup 路径，每行一个：

```
/sys/fs/cgroup/kubepods.slice
/sys/fs/cgroup/kubepods.slice/kubepods-besteffort.slice
```

**注意**: 如果文件为空，程序会监控 **所有** cgroup。

### Blocklist

指定不回收的 cgroup 路径：

```
/sys/fs/cgroup/system.slice
/sys/fs/cgroup/init.scope
```

---

## 命令行参数详解

### 回收模式

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--reclaim-mode` | simple | 回收模式: `simple`(直接回收) 或 `emm`(基于年龄回收) |
| `--mode` | 2 | 回收范围: `1`(仅 root cgroup) 或 `2`(所有 cgroup) |

**回收模式说明**:
- `simple`: 直接回收内存，根据内存压力计算回收量
- `emm`: Enhanced Memory Management，根据内存页年龄优先回收冷页面

---

### 回收阈值参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--pct-trigger-reclaim` | 0.6 | 触发回收的内存使用率阈值 (60%) |
| `--ratio` | 0.02 | 基础回收比例 (每次回收内存的 2%) |
| `--ratio-anon` | 0.01 | 匿名页回收比例 (EMM 模式) |
| `--ratio-file` | 0.02 | 文件页回收比例 (EMM 模式) |
| `--interval` | 10s | 回收间隔 (Simple 模式) |
| `--interval-anon` | 10s | 匿名页回收间隔 (EMM 模式) |
| `--interval-file` | 20s | 文件页回收间隔 (EMM 模式) |

**示例**: `--ratio-anon=0.0002` 表示每次回收匿名页内存的 0.02%

---

### Swap/Pageout 控制

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--swapout-limit` | 1.0 | swapout 比例上限 (0-1)，超过后优先回收文件页 |
| `--pageout-limit` | 1.0 | pageout 比例上限 (0-1) |
| `--swappiness` | 60 | 内核 swappiness 参数 (0-100) |

**示例**: `--swapout-limit=0.6` 表示当 swapout 比例超过 60% 时，修改 swappiness 优先回收文件页

---

### Force Reclaim (强制回收)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--force-reclaim` | false | 启用强制回收模式 |
| `--force-reclaim-limit` | 0.95 | 强制回收触发阈值 (95%) |
| `--force-reclaim-target` | 0.9 | 强制回收目标 (90%) |

**说明**: 当内存使用率超过 `--force-reclaim-limit` 时，大幅增加回收量直到使用率降到 `--force-reclaim-target`

---

### ZRAM 压缩

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--open-zram` | false | 启用 ZRAM 内存压缩 |
| `--comp-alg` | lzo-rle | ZRAM 压缩算法 |
| `--disk-size` | 0 | ZRAM 后端磁盘大小 (KB)，0 表示纯内存 |
| `--disk-path` | null | ZRAM 后端磁盘路径 |
| `--zram-reject-size` | -1 | 拒绝压缩大于此大小的页面 (B) |

**示例**: `--open-zram --disk-size=0` 启用纯内存 ZRAM

---

### Allowlist/Blocklist

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--allowlist` | /run/umrd/allowlist.cfg | Allowlist 文件路径 |
| `--allowlist-oversell` | /run/umrd/allowlist_oversell.cfg | 超卖模式 Allowlist |
| `--blocklist` | /run/umrd/blocklist.cfg | Blocklist 文件路径 |
| `--allowlist-empty` | false | 不设置 allowlist |
| `--blocklist-empty` | false | 不设置 blocklist |

**说明**:
- Allowlist: 指定要监控和回收的 cgroup
- Blocklist: 指定不监控和不回收的 cgroup
- 如果 allowlist 为空且未设置 `--allowlist-empty`，默认监控 `/sys/fs/cgroup/`

---

### CPU 控制

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--cpu-quota-ratio` | 1.0 | UMRD 进程 CPU 带宽限制比例 |
| `--cpu-util-threshold` | MAX | CPU 使用率超过此值时停止回收 |
| `--set-offline` | false | 将 UMRD 进程设置为离线调度器 |

**示例**: `--cpu-quota-ratio=0.05` 限制 UMRD 使用 5% CPU

---

### 日志参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--verbose` | false | 设置日志级别为 INFO |
| `--quiet` | false | 设置日志级别为 ERROR |
| `--debug` | false | 设置日志级别为 DEBUG，并输出监控 cgroup 列表 |
| `--logfile` | /run/umrd/umrd.log | 日志文件路径 |

---

### 其他参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--cycle-sleep` | 1.0 | 主循环睡眠时间 (秒) |
| `--output-dir` | /run/umrd | 输出目录 |
| `--hot-reload` | /run/umrd/hot_reload.cfg | 热更新配置文件路径 |
| `--disable-oversell` | false | 禁用超卖模式 |
| `--always-defaults` | false | 所有 cgroup 使用默认参数 |
| `--oneshot` | false | 只运行一次回收 (调试用) |
| `--profile` | false | 启用性能分析 (调试用) |

---

## 输出文件

| 文件 | 说明 |
|------|------|
| `/run/umrd/status` | 运行状态 (Pid, AccumReclaim, etc.) |
| `/run/umrd/mem_save` | 全局内存节省统计 |
| `/run/umrd/cgroup_mem_save` | 各 cgroup 节省详情 |
| `/run/umrd/monitored_cgroups.list` | 监控的 cgroup 列表 |
| `/run/umrd/umrd.log` | 日志文件 |
| `/run/umrd/umrd_version` | 版本号 |

### mem_save 字段说明

```
total mem: 1056684392 kb        # 物理内存总量
total swap: 1065072992 kb       # Swap 总量
anon save: 339968 bytes          # 匿名页节省量 (ZRAM 压缩)
file save: 3130462208 bytes      # 文件页节省量 (可回收)
max memusage: 413556711424 bytes # 最大内存使用量估算
save ratio: 0.76 %               # 节省比例
savepage limit: 1082044817408 bytes  # 回收限制
totalused memory: 740154490880 bytes  # 当前已用内存
```

### status 字段说明

```
Pid: 3704302                    # UMRD 进程 PID
Status: Active(Running)         # 运行状态
BootTimestamp: 1773898934 s     # 启动时间戳
AccumReclaimAnon: 0 KB          # 累计回收匿名页
AccumReclaimFile: 0 KB          # 累计回收文件页
LastReclaimTimestamp: 0 s        # 上次回收时间
LastReclaimCost: 0 s             # 上次回收耗时
```

---

## 内核依赖

| 特性 | 路径 | 必须 | 说明 |
|------|------|------|------|
| cgroup v2 | /sys/fs/cgroup/ | **是** | CRI 自动挂载 |
| PSI | /proc/pressure/* | **是** | 需要内核参数 `psi=1` |
| ZRAM | /sys/block/zram0/* | 否 | 内核模块 |
| EMM | /sys/fs/cgroup/*/memory.emm.* | 否 | 多代 LRU |

---

## K8s DaemonSet 参数配置

当前 DaemonSet 配置参数:

```yaml
args:
- "--mode=2"                    # 回收所有 cgroup
- "--swapout-limit=0.6"         # swapout 超过 60% 时优先回收文件页
- "--pct-trigger-reclaim=0.6"    # 内存使用 60% 时触发回收
- "--cpu-quota-ratio=0.05"      # UMRD 限制 5% CPU
- "--reclaim-mode=emm-compat"  # EMM + fallback to simple
- "--interval-anon=10"           # 匿名页回收间隔 10 秒
- "--ratio-anon=0.0002"         # 每次回收匿名页的 0.02%
- "--ratio-file=0"              # 不回收文件页
- "--open-zram"                 # 启用 ZRAM 压缩
- "--quiet"                     # 静默模式 (ERROR 级别)
```

---

## 构建

```bash
# 构建 wheel 包
./scripts/build.sh

# 或单独构建镜像
./scripts/build-image.sh

# 推送镜像
buildah push zpk.idc.w7.com/w7panel/umrd:2.0.0 docker://zpk.idc.w7.com/w7panel/umrd:2.0.0
buildah push zpk.idc.w7.com/w7panel/umrd:latest docker://zpk.idc.w7.com/w7panel/umrd:latest
```

---

## 项目结构

```
umrd/
├── src/umrd/              # 源代码
│   ├── __init__.py
│   ├── __main__.py        # 入口点: python -m umrd
│   ├── cli.py             # CLI 参数解析
│   ├── umrd.py            # 主控制器
│   ├── cgroup.py          # Cgroup 操作 (cgroup v2)
│   ├── cgtree.py          # Cgroup 树管理
│   └── util.py            # 工具函数
├── service/               # 部署文件
│   └── umrd.service       # systemd 服务
├── k8s/                   # Kubernetes 部署
│   └── daemonset.yaml
├── scripts/               # 构建脚本
├── Dockerfile             # Docker 镜像
├── pyproject.toml         # 项目配置
├── install.sh             # 一键安装脚本
└── README.md
```

---

## 故障排查

```bash
# 查看运行状态
cat /run/umrd/status

# 查看详细日志
tail -f /run/umrd/umrd.log

# 检查 cgroup v2
ls /sys/fs/cgroup/

# 检查 PSI
cat /proc/pressure/memory

# 检查内核参数
cat /proc/cmdline | grep psi

# 检查 zram
ls /sys/block/zram0/
cat /sys/block/zram0/mm_stat
```

### 常见问题

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| Pod 无法启动 "PSI not found" | PSI 未启用 | 节点添加 `psi=1` 内核参数 |
| Pod 无法启动 "cgroup v2 not found" | 使用 cgroup v1 | 切换到 cgroup v2 内核 |
| 回收不生效 | 内存充足无需回收 | 检查 mem_save 中的 pct_usage |
| AccumReclaim 全为 0 | 内存使用率低于阈值 | 检查 pct-trigger-reclaim 值 |
| ZRAM 未启用 | 内核不支持 | 检查内核模块 |
| 日志无输出 | quiet 模式 | 添加 `--verbose` 参数 |
| mem_save 数据异常 | cgroup v2 重复计算 | 已修复 v2.0.0 版本 |

---

## 安装

### Kubernetes (推荐)

```bash
# 部署
kubectl apply -f k8s/daemonset.yaml

# 验证
kubectl get pods -n kube-system -l app=umrd
kubectl logs -n kube-system -l app=umrd
```

### Systemd

```bash
# 克隆并安装
git clone https://github.com/w7panel/umrd.git
cd umrd
sudo ./install.sh

# 验证
systemctl status umrd
cat /run/umrd/status
```

### 源码安装

```bash
git clone https://github.com/w7panel/umrd.git
cd umrd
pip install .
```

## 发布

版本号在 `src/umrd/_version.py` 中定义。

```bash
# 1. 修改版本号
vim src/umrd/_version.py

# 2. 构建（自动更新文档）
./scripts/build.sh

# 3. 推送镜像
buildah push zpk.idc.w7.com/w7panel/umrd:2.0.0 docker://zpk.idc.w7.com/w7panel/umrd:2.0.0
buildah push zpk.idc.w7.com/w7panel/umrd:latest docker://zpk.idc.w7.com/w7panel/umrd:latest

# 4. 推送代码
git add -A && git commit -m "release: v2.0.0" && git push
```

---

## License

本项目基于 [Apache License 2.0](LICENSE) 开源。

