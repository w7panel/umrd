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

## 快速部署

### 前置要求

**1. cgroup v2**
```bash
ls /sys/fs/cgroup/
# 应显示 cgroup2... 而不是 memory/
```

**2. PSI (Pressure Stall Information)**
```bash
# 检查 PSI 是否存在
cat /proc/pressure/memory

# 如果不存在，启用 PSI (需要 root)
grubby --update-kernel=DEFAULT --args="psi=1"
reboot
```

### Kubernetes 部署 (推荐)

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

### Systemd 部署

```bash
# 一键安装
curl -sL https://raw.githubusercontent.com/w7panel/umrd/main/install.sh | bash

# 或手动安装
git clone https://github.com/w7panel/umrd.git
cd umrd
sudo ./install.sh

# 验证
systemctl status umrd
cat /run/umrd/status
```

---

## 使用

```bash
# 查看帮助
umrd --help

# 查看状态
cat /run/umrd/status

# 查看全局内存统计
cat /run/umrd/mem_save

# 查看日志
tail -f /run/umrd/umrd.log

# 热更新配置 (修改日志级别)
echo "log_file_handler_level=debug" > /run/umrd/hot_reload.cfg
```

---

## 常用参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--reclaim-mode` | simple | 回收模式: `simple`(直接回收) 或 `emm`(基于年龄回收) |
| `--mode` | 2 | 回收范围: `1`(仅 root cgroup) 或 `2`(所有 cgroup) |
| `--pct-trigger-reclaim` | 0.6 | 触发回收的内存使用率阈值 |
| `--ratio-anon` | 0.01 | 匿名页回收比例 |
| `--open-zram` | false | 启用 ZRAM 内存压缩 |
| `--quiet` | false | 设置日志级别为 ERROR |

### K8s DaemonSet 配置

```yaml
args:
- "--mode=2"
- "--pct-trigger-reclaim=0.6"
- "--reclaim-mode=emm-compat"
- "--ratio-anon=0.0002"
- "--open-zram"
- "--quiet"
```

---

## 输出文件

| 文件 | 说明 |
|------|------|
| `/run/umrd/status` | 运行状态 (Pid, AccumReclaim, etc.) |
| `/run/umrd/mem_save` | 全局内存节省统计 |
| `/run/umrd/umrd.log` | 日志文件 |

### mem_save 字段说明

```
total mem: 1056684392 kb        # 物理内存总量
anon save: 339968 bytes          # 匿名页节省量 (ZRAM 压缩)
file save: 3130462208 bytes      # 文件页节省量
save ratio: 0.76 %               # 节省比例
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
cat /proc/cmdline | grep psi

# 检查 zram
ls /sys/block/zram0/
cat /sys/block/zram0/mm_stat
```

### 常见问题

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| Pod 无法启动 "PSI not found" | PSI 未启用 | 节点添加 `psi=1` 内核参数 |
| 回收不生效 | 内存充足无需回收 | 检查 mem_save 中的 pct_usage |
| AccumReclaim 全为 0 | 内存使用率低于阈值 | 检查 pct-trigger-reclaim 值 |
| ZRAM 未启用 | 内核不支持 | 检查内核模块 |

---

## 构建发布

```bash
# 构建
./scripts/build.sh

# 推送镜像
buildah push zpk.idc.w7.com/w7panel/umrd:2.0.0 docker://zpk.idc.w7.com/w7panel/umrd:2.0.0
buildah push zpk.idc.w7.com/w7panel/umrd:latest docker://zpk.idc.w7.com/w7panel/umrd:latest

# 推送代码
git add -A && git commit -m "release: v2.0.0" && git push
```

---

## License

本项目基于 [Apache License 2.0](LICENSE) 开源。
