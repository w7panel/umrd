---
name: umrd-development
description: UMRD 项目开发规范 - cgroup v2 内存回收守护进程
license: Apache-2.0
---

# UMRD 开发规范

## 项目信息

| 项目 | 值 |
|------|-----|
| 版本 | 2.0.0 |
| 语言 | Python >=3.8 |
| 平台 | Linux cgroup v2 |
| 仓库 | https://github.com/w7panel/umrd |

## 核心约束

### cgroup v2 only

⚠️ UMRD 2.0+ **仅支持 cgroup v2**，不支持 v1

| v1 | v2 |
|-----|-----|
| `/sys/fs/cgroup/memory/` | `/sys/fs/cgroup/` |
| `memory.memsw.usage_in_bytes` | `memory.current` + `memory.swap.current` |
| `memory.swappiness` | 不存在 |

### PSI (Pressure Stall Information)

⚠️ UMRD **必须**依赖 PSI 进行内存压力检测

- 路径: `/proc/pressure/memory`
- 启用方式: 内核参数 `psi=1`
- CentOS/RHEL: `grubby --update-kernel=DEFAULT --args="psi=1" && reboot`

```bash
# 检查
cat /proc/cmdline | grep psi

# 如果不存在，添加参数
grubby --update-kernel=DEFAULT --args="psi=1"
reboot
```

## 代码规范

### 路径必须使用变量

```python
# ✅ 正确
from .util import CGROUP_V2_ROOT
path = os.path.join(CGROUP_V2_ROOT, 'memory.current')

# ❌ 错误
path = '/sys/fs/cgroup/memory/current'
```

### 使用 helper 函数

所有 cgroup v2 接口必须通过 util.py 封装:

```python
cg_memory_current(path)      # memory.current
cg_memory_max(path)          # memory.max
cg_memory_stat(path)         # memory.stat
cg_has_interface(path, iface) # 检查接口存在
cg_try_reclaim(path, bytes)  # memory.reclaim
```

### 异常处理

```python
# ✅ 可选接口静默降级
try:
    with open(os.path.join(path, 'memory.zram.raw_in_bytes')) as f:
        stat = int(f.read())
except:
    pass

# ❌ 致命错误不能静默
```

## K8s DaemonSet 配置

```yaml
args:
- "--mode=2"                    # 回收所有 cgroup
- "--swapout-limit=0.6"         # swapout >60% 时优先回收文件页
- "--pct-trigger-reclaim=0.6"    # 内存使用 60% 触发回收
- "--reclaim-mode=emm-compat"   # EMM + fallback to simple
- "--interval-anon=10"           # 匿名页回收间隔 10s
- "--ratio-anon=0.0002"         # 每次回收 0.02%
- "--ratio-file=0"              # 不回收文件页
- "--open-zram"                 # 启用 ZRAM
- "--quiet"                     # ERROR 级别日志
```

### 回收模式

| 模式 | 说明 |
|------|------|
| `simple` | 直接回收 |
| `emm` | 基于页龄回收 (需内核支持) |
| `emm-compat` | EMM + 自动 fallback 到 simple |

## 输出文件

### /run/umrd/status

```
Pid: 3704302
Status: Active(Running)
AccumReclaimAnon: 0 KB    # 累计回收匿名页
AccumReclaimFile: 0 KB    # 累计回收文件页
LastReclaimTimestamp: 0 s
```

### /run/umrd/mem_save

```
total mem: xxx kb          # 物理内存
anon save: xxx bytes       # ZRAM 压缩量
file save: xxx bytes       # 可回收文件页
max memusage: xxx bytes    # 最大内存使用估算
save ratio: xx%           # 节省比例
```

## 发布检查清单

- [ ] `python3 -m py_compile src/umrd/*.py` 语法检查
- [ ] `python3 -c "from umrd import *"` 模块导入
- [ ] `grep -r "cgroup/memory" .` 无 v1 路径残留
- [ ] `grep -r "sys/kernel/psi" .` 无 PSI sysfs 路径硬编码
- [ ] 版本号一致: pyproject.toml, README.md

## 构建命令

```bash
# 构建 (自动更新版本)
./scripts/build.sh

# 推送镜像
buildah push zpk.idc.w7.com/w7panel/umrd:2.0.0 docker://zpk.idc.w7.com/w7panel/umrd:2.0.0
buildah push zpk.idc.w7.com/w7panel/umrd:2.0.0latest docker://zpk.idc.w7.com/w7panel/umrd:2.0.0latest
```

## 目录结构

```
src/umrd/
├── __init__.py
├── __main__.py      # 入口
├── cli.py            # CLI
├── umrd.py          # 主控制器
├── cgroup.py        # cgroup 封装
├── cgtree.py        # cgroup 树管理
└── util.py          # 工具函数
```
