# UMRD - Userspace Memory Reclaimer Daemon

**版本**: 2.0.0  
**License**: Apache-2.0

---

## 一键安装

```bash
# 下载并运行
curl -sL https://raw.githubusercontent.com/w7panel/umrd/main/install.sh | bash

# 或克隆后安装
git clone https://github.com/w7panel/umrd.git
cd umrd
./install.sh
```

---

## 项目概述

**UMRD (Userspace Memory Reclaimer Daemon)** 是用户空间内存回收守护进程，通过监控cgroup内存压力动态回收内存。

### 核心功能

| 功能 | 说明 |
|------|------|
| PSI感知回收 | 基于Linux PSI感知内存压力 |
| 双模式回收 | Simple模式 + EMM模式 |
| ZRAM压缩 | 自动启用内存压缩 |
| 超卖支持 | Page Reporting智能调度 |

---

## 安装方式

### 方式1: 一键安装 (推荐)

```bash
git clone https://github.com/w7panel/umrd.git
cd umrd
./install.sh
```

### 方式2: pip安装

```bash
pip install umrd
./install.sh
```

### 方式2: wheel文件

```bash
pip install umrd-2.0.0-py3-none-any.whl
sudo umrd-install --enable
```

### 方式3: 源码安装

```bash
git clone https://github.com/w7panel/umrd.git
cd umrd
pip install .
sudo umrd-install --enable
```

### 方式3: Kubernetes

```bash
kubectl apply -f k8s/daemonset.yaml
```

---

## 使用

```bash
# 查看帮助
umrd --help

# 查看状态
cat /run/umrd/status

# 查看日志
tail -f /run/umrd/umrd.log

# 查看cgroup回收详情
cat /run/umrd/cgroup_mem_save

# 热更新配置
echo "log_file_handler_level=debug" > /run/umrd/hot_reload.cfg
```

---

## 常用参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--mode` | 2 | 1=root cgroup, 2=所有cgroup |
| `--reclaim-mode` | simple | 回收模式: simple/emm |
| `--ratio` | 0.02 | 回收比例 |
| `--interval` | 10s | 回收间隔 |
| `--open-zram` | false | 启用ZRAM压缩 |

---



---

## 2. 文件结构

```
umrd/
├── __init__.py      # 包初始化文件
├── __main__.py      # 入口点: python -m umrd
├── cli.py           # 命令行参数解析 (148行)
├── umrd.py          # 主控制器类 (615行)
├── cgroup.py        # Cgroup操作封装 (899行)
├── cgtree.py        # Cgroup树管理 (346行)
└── util.py          # 工具函数集 (939行)
```

**总代码量**: 2947行 Python代码

---

## 3. 架构设计

### 3.1 类层次结构

```
UMRD (主控制器)
├── 职责: 解析/proc/cgroup，主循环控制
├── set_cpu_quota_and_offline() - CPU配额设置
├── check_feasibility() - 回收可行性判断
├── run() - 执行单次回收周期
└── loop() - 主循环

CgroupTree (树管理)
├── 职责: 管理cgroup层级结构，规则引擎
├── roots: Dict[str, CGroup] - 顶层cgroup
├── path_tree: OrderedDict - 规则树
├── try_update_rules() - 热更新规则
├── find_rootcg() - 发现root cgroup
└── try_reclaim() - 触发回收

CGroup (抽象基类)
│
├── NegativeCgroup  # 被阻塞的cgroup（占位符）
│
├── BasicCgroup     # 基础cgroup（仅扫描，不回收）
│   └── get_memsaving_recursive()
│
├── SimpleCgroup     # 简单回收模式
│   ├── do_reclaim() - 调用memory.reclaim
│   ├── _cal_reclaim_target() - 计算回收目标
│   └── reclaim_recursive() - 递归回收
│
└── EMMCgroup       # EMM增强回收模式
    ├── do_emm_age() - 页面老化
    ├── do_emm_reclaim() - 精细化回收
    └── reclaim_recursive() - 分离anon/file回收
```

### 3.2 数据流

```
主循环 (loop)
    │
    ├── 1. try_update_rules()    更新allowlist/blocklist
    │
    ├── 2. try_refresh(mode)    刷新cgroup树结构
    │       │
    │       └── refresh(level)  递归扫描子cgroup
    │
    ├── 3. run(mode)            执行回收逻辑
    │       │
    │       ├── PSI检查         io_some_avg10 >= 95? → 跳过
    │       ├── CPU检查         cpu_util >= threshold? → 跳过
    │       ├── 可行性检查      内存压力过高? → 跳过
    │       │
    │       └── try_reclaim()   执行回收
    │           │
    │           └── reclaim_recursive()
    │
    └── 4. report_statistics()  写入统计文件
```

---

## 4. 回收算法

### 4.1 Simple模式

基于`memory.reclaim`接口的直接回收：

```python
def _cal_reclaim_target(self):
    delta = current_psi - last_psi
    if delta < psi_threshold:
        integral = delta
    else:
        integral += delta
    
    err = psi_threshold / max(integral, 1)
    adj = (err / coeff_backoff) ** 2
    reclaim_mem = adj * ratio * current_mem
```

### 4.2 EMM模式

分离匿名页和文件页回收：

```
┌─────────────────────────────────────────┐
│           EMM回收决策                    │
├─────────────────────────────────────────┤
│ anon判断:                               │
│   swapout_ratio <= swapout_limit?       │
│   time_since_last_anon >= interval_anon?│
│                                         │
│ file判断:                               │
│   pageout_ratio <= pageout_limit?       │
│   time_since_last_file >= interval_file?│
│                                         │
│ 满足条件 → 分别调用memory.emm.reclaim   │
└─────────────────────────────────────────┘
```

### 4.3 超卖状态机

| 状态 | PSI条件 | page_reporting | compaction_proactiveness |
|------|---------|----------------|--------------------------|
| PROHIGH | mem_delta < 6000 && io_avg10 < 5 | 启用 | 40 |
| PRONORM | mem_delta < 10000 && io_avg10 < 30 | 启用 | 20 |
| PROLOW | 其他 | 禁用 | 0 |

---

## 5. 命令行参数

### 5.1 回收参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--swappiness` | 60 | 回收时的swappiness值 (0-200) |
| `--reclaim-mode` | simple | 回收模式 |
| `--psi-threshold` | 10000 | PSI阈值（微秒） |
| `--ratio` | 0.02 | 基础回收比例 |
| `--ratio-anon` | 0.01 | 匿名页回收比例 (EMM) |
| `--ratio-file` | 0.02 | 文件页回收比例 (EMM) |
| `--interval` | 10.0s | 回收间隔 |
| `--interval-anon` | 10.0s | 匿名页回收间隔 |
| `--interval-file` | 20.0s | 文件页回收间隔 |
| `--scan-interval` | 10.0s | 单cgroup扫描间隔 |
| `--swapout-limit` | 1.0 | swapout比例上限 |
| `--pageout-limit` | 1.0 | pageout比例上限 |
| `--pct-trigger-reclaim` | 0.0 | 触发回收的内存使用率 |

### 5.2 部署参数

| 参数 | 说明 |
|------|------|
| `--mode 1/2` | 1=root cgroup, 2=所有cgroup |
| `--allowlist` | 允许回收的cgroup列表 |
| `--blocklist` | 禁止回收的cgroup列表 |
| `--open-zram` | 启用zram压缩 |
| `--disable-oversell` | 禁用超卖模式 |
| `--set-offline` | 设置CPU离线调度 |
| `--cpu-quota-ratio` | CPU带宽限制 |
| `--hot-reload` | 热加载配置文件 |

---

## 6. 内核接口

### 6.1 内存接口 (cgroup v2)

| 接口 | 用途 |
|------|------|
| `/sys/fs/cgroup/*/memory.pressure` | PSI内存压力 |
| `/sys/fs/cgroup/*/memory.reclaim` | Simple模式回收 |
| `/sys/fs/cgroup/*/memory.emm.age` | EMM页面老化 |
| `/sys/fs/cgroup/*/memory.emm.reclaim` | EMM精细回收 |
| `/sys/fs/cgroup/*/memory.stat` | 内存统计 |
| `/sys/fs/cgroup/*/memory.current` | 当前内存使用量 |
| `/sys/fs/cgroup/*/memory.swap.current` | 当前swap使用量 |

### 6.2 系统接口

| 接口 | 用途 |
|------|------|
| `/proc/pressure/memory` | 全局PSI内存压力 |
| `/proc/pressure/io` | 全局PSI IO压力 |
| `/proc/meminfo` | 系统内存信息 |
| `/proc/sys/vm/page_reporting_enable` | Page Reporting开关 |
| `/proc/sys/vm/compaction_proactiveness` | 压缩积极性 |

### 6.3 ZRAM接口

| 接口 | 用途 |
|------|------|
| `/sys/block/zram0/disksize` | ZRAM设备大小 |
| `/sys/block/zram0/comp_algorithm` | 压缩算法 |
| `/sys/block/zram0/mm_stat` | 压缩统计 |

---

## 7. 输出文件

| 文件 | 说明 |
|------|------|
| `/run/umrd/status` | 运行状态 |
| `/run/umrd/mem_save` | 全局内存节省统计 |
| `/run/umrd/cgroup_mem_save` | 各cgroup节省详情 |
| `/run/umrd/monitored_cgroups.list` | 监控的cgroup列表 |
| `/run/umrd/reclaimed_in_last_period` | 上周期回收量 |
| `/run/umrd/umrd.log` | 日志文件 |
| `/run/umrd/hot_reload.cfg` | 热加载配置 |

### 7.1 status格式

```
Pid: 12345
Status: Active(Running)
BootTimestamp: 1709123456 s
AccumReclaimSimple: 123456 KB
AccumReclaimAnon: 234567 KB
AccumReclaimFile: 345678 KB
LastReclaimTimestamp: 1709123500 s
LastReclaimCost: 0.123 s
```

---

## 8. systemd部署

```ini
[Unit]
Description=Userspace Memory Reclaim Daemon (1.8.17.eks)
ConditionPathExists=/proc/pressure

[Service]
Environment="UMRDSYSTEMD=1"
ExecStart=/usr/bin/python3 -m umrd \
    --standalone-cgroup \
    --swapout-limit=0.6 \
    --pct-trigger-reclaim 0.6 \
    --cpu-quota-ratio 0.05 \
    --mode=2 \
    --reclaim-mode=emm \
    --interval-anon=10 \
    --ratio-anon=0.0002 \
    --ratio-file=0 \
    --open-zram \
    --quiet
```

---

## 9. 配置文件格式

### 9.1 Allowlist

```
/sys/fs/cgroup/kubepods/burstable
    interval_anon=5 ratio_anon=0.002
    interval_file=10 ratio_file=0.0002
```

### 9.2 Hot Reload

```
log_file_handler_level=debug
log_console_handler_level=info
oversell=1
open_zram=1
```

---

## 10. 依赖关系

### 10.1 Python依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| dnspython | 2.6.1 | DNS解析（可选） |

### 10.2 内核依赖

| 特性 | 路径 | 必须 |
|------|------|------|
| PSI | /proc/pressure/* | 是 |
| cgroup v2 | /sys/fs/cgroup/ | 是 |
| ZRAM | /sys/block/zram0/* | 否 |
| LRU Gen | /sys/kernel/mm/lru_gen/* | 否 |
| EMM | /sys/fs/cgroup/*/memory.emm.* | 否 |

---

## 11. 调优指南

| 场景 | 推荐配置 |
|------|----------|
| 内存紧张 | `ratio=0.05, interval=5` |
| 内存充足 | `ratio=0.01, interval=20` |
| 延迟敏感 | `psi-threshold=5000, swapout-limit=0.3` |
| 高密度超卖 | `enable oversell, proactive_high` |

---

## 12. 故障排查

```bash
# 查看运行状态
cat /run/umrd/status

# 查看日志
tail -f /run/umrd/umrd.log

# 查看各cgroup回收详情
cat /run/umrd/cgroup_mem_save

# 检查内核接口
cat /proc/pressure/memory
```

### 常见问题

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| 回收不生效 | cgroup不在allowlist | 检查配置文件 |
| CPU占用高 | psi阈值过低 | 调高psi-threshold |
| ZRAM未启用 | 内核不支持 | 检查内核配置 |

---

## 项目结构 (维护者参考)

```
umrd/
├── src/umrd/              # 源代码
│   ├── __init__.py
│   ├── __main__.py        # 入口点: python -m umrd
│   ├── cli.py             # CLI参数解析
│   ├── umrd.py            # 主控制器
│   ├── cgroup.py          # Cgroup操作
│   ├── cgtree.py          # Cgroup树管理
│   └── util.py            # 工具函数
├── service/               # 部署文件
│   └── umrd.service       # systemd服务
├── examples/              # 配置示例
│   ├── allowlist.example
│   ├── blocklist.example
│   └── umrd.env.example
├── scripts/               # 构建脚本
│   ├── build.sh           # 构建全部 (wheel + OCI镜像)
│   ├── build-image.sh     # 仅构建OCI镜像
│   ├── deploy-k8s.sh      # K8s部署
│   └── test.sh            # 测试
├── k8s/                   # Kubernetes部署
│   └── daemonset.yaml
├── tests/                 # 单元测试
│   └── test_umrd.py
├── Dockerfile             # Docker镜像 (兼容buildah)
├── Containerfile          # OCI镜像构建文件
├── pyproject.toml         # 项目配置
├── requirements.txt       # 依赖
├── requirements-dev.txt   # 开发依赖
├── install.sh             # 一键安装脚本
├── RELEASE.md            # 发布说明
├── .gitignore
├── .dockerignore
└── README.md
```

### 构建与发布

```bash
# 构建wheel和OCI镜像
./scripts/build.sh

# 仅构建OCI镜像
./scripts/build-image.sh

# 推送镜像
buildah push w7panel/umrd:latest docker://w7panel/umrd:latest

# 发布到PyPI
twine upload dist/*
```
