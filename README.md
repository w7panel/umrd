# UMRD - Userspace Memory Reclaimer Daemon

**版本**: 2.0.0  
**License**: Apache-2.0  
**cgroup支持**: 仅 cgroup v2

---

## 项目概述

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
- **cgroup**: 自动使用 cgroup v2，监控所有 cgroup

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
/sys/fs/cgroup/kubepods.slice/kubepods-burstable.slice
```

**注意**: 如果文件为空，程序会监控 **所有** cgroup。

### Blocklist

指定不回收的 cgroup 路径：

```
/sys/fs/cgroup/system.slice
/sys/fs/cgroup/init.scope
```

---

## 命令行参数

### 回收参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--reclaim-mode` | simple | 回收模式: simple/emm |
| `--ratio` | 0.02 | 基础回收比例 |
| `--ratio-anon` | 0.01 | 匿名页回收比例 (EMM) |
| `--ratio-file` | 0.02 | 文件页回收比例 (EMM) |
| `--interval` | 10s | 回收间隔 |
| `--interval-anon` | 10s | 匿名页回收间隔 |
| `--interval-file` | 20s | 文件页回收间隔 |
| `--swapout-limit` | 1.0 | swapout 比例上限 |
| `--pageout-limit` | 1.0 | pageout 比例上限 |

### 部署参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--mode` | 2 | 1=root cgroup, 2=所有cgroup |
| `--allowlist` | /run/umrd/allowlist.cfg | allowlist 文件路径 |
| `--blocklist` | /run/umrd/blocklist.cfg | blocklist 文件路径 |
| `--open-zram` | false | 启用 zram 压缩 |
| `--disable-oversell` | false | 禁用超卖模式 |
| `--verbose` | false | 详细日志 |

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

---

## 内核依赖

| 特性 | 路径 | 必须 |
|------|------|------|
| cgroup v2 | /sys/fs/cgroup/ | **是** |
| PSI | /proc/pressure/* | 是 |
| ZRAM | /sys/block/zram0/* | 否 |
| EMM | /sys/fs/cgroup/*/memory.emm.* | 否 |

---

## 构建

```bash
# 构建 wheel 包
./scripts/build.sh

# 构建 OCI 镜像 (使用 buildah)
./scripts/build-image.sh

# 推送镜像
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
├── Containerfile          # OCI 镜像
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

# 检查内存压力
cat /proc/pressure/memory

# 检查 zram
ls /sys/block/zram0/
cat /sys/block/zram0/mm_stat
```

### 常见问题

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| Pod 无法启动 | cgroup v1 不支持 | 使用 cgroup v2 内核 |
| 回收不生效 | 内存充足无需回收 | 检查 mem_save 中的 PSI 值 |
| ZRAM 未启用 | 内核不支持 | 检查内核模块 |
| 日志无输出 | quiet 模式 | 添加 `--verbose` 参数 |
