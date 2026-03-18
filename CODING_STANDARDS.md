# UMRD 开发规范

## 1. 项目概述

**UMRD (Userspace Memory Reclaimer Daemon)** - 用户空间内存回收守护进程

| 项目信息 | 值 |
|---------|-----|
| 版本 | 2.0.0 |
| License | Apache-2.0 |
| Python | >=3.8 |
| 目标平台 | Linux (cgroup v2) |
| 仓库 | https://github.com/w7panel/umrd |

## 2. 架构约束

### 2.1 cgroup 版本

```
⚠️ 重要: UMRD 2.0+ 仅支持 cgroup v2
```

| cgroup v1 路径 | cgroup v2 路径 |
|---------------|----------------|
| `/sys/fs/cgroup/memory/` | `/sys/fs/cgroup/` |
| `memory.memsw.usage_in_bytes` | `memory.current` + `memory.swap.current` |
| `memory.swappiness` | 不存在 (v2无此接口) |
| `memory.reclaim` | 厂商特定接口 |
| `memory.zram.*` | 同上 |

### 2.2 目录结构

```
umrd/
├── src/umrd/              # 源代码
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py             # 命令行入口
│   ├── cgroup.py          # cgroup 封装
│   ├── cgtree.py          # cgroup 树管理
│   ├── umrd.py            # 主程序
│   └── util.py            # 工具函数
├── service/               # systemd 服务
├── k8s/                   # Kubernetes 部署
├── examples/               # 配置示例
├── scripts/                # 构建脚本
└── tests/                  # 测试
```

## 3. 代码规范

### 3.1 路径使用

```python
# ✅ 正确: 使用变量
from .util import CGROUP_V2_ROOT
path = os.path.join(CGROUP_V2_ROOT, 'memory.current')

# ❌ 错误: 硬编码路径
path = '/sys/fs/cgroup/memory/current'  # v1 路径
path = 'CGROUP_V2_ROOT/memory.current'   # 字面量
```

### 3.2 cgroup v2 接口封装

所有 cgroup v2 接口访问必须通过 util.py 中的 helper 函数:

```python
# 必须使用的 helper 函数
cg_memory_current(path)      # 读取 memory.current
cg_memory_max(path)          # 读取 memory.max
cg_memory_stat(path)         # 读取 memory.stat
cg_has_interface(path, iface) # 检查接口是否存在
cg_write_value(path, iface, value)  # 写入接口
cg_try_reclaim(path, bytes)  # memory.reclaim
cg_set_zram_priority(path, priority)  # zram priority
cg_get_zram_stat(path)       # 获取 zram 统计
```

### 3.3 变量定义

```python
# cgroup v2 常量 (在 util.py 中)
CGROUP_V2_ROOT = '/sys/fs/cgroup'
CGROUP_CPU_PATH = '/sys/fs/cgroup'
CGROUP_MEMORY_PATH = '/sys/fs/cgroup'

# 接口文件名常量
CGROUP_MEMORY_CURRENT = 'memory.current'
CGROUP_MEMORY_MAX = 'memory.max'
CGROUP_MEMORY_STAT = 'memory.stat'
CGROUP_MEMORY_PRESSURE = 'memory.pressure'
CGROUP_CGROUP_PROCS = 'cgroup.procs'
```

### 3.4 异常处理

```python
# ✅ 正确: 静默处理可选接口
try:
    with open(os.path.join(path, 'memory.zram.raw_in_bytes'), 'r') as f:
        stat['raw'] = int(f.read().strip())
except:
    pass  # 接口不存在时静默降级

# ❌ 错误: 静默处理致命错误
try:
    os.open(...)  # 可能致命
except:
    pass  # 不应静默
```

### 3.5 类型注解

```python
# 使用 type: ignore 标注已知类型问题
some_list.append(value)  # type: ignore
```

## 4. 文档规范

### 4.1 路径示例

文档中的 cgroup 路径示例必须使用 v2 格式:

```markdown
# ✅ 正确
/sys/fs/cgroup/kubepods/burstable
/sys/fs/cgroup/system.slice

# ❌ 错误
/sys/fs/cgroup/memory/kubepods/burstable
/sys/fs/cgroup/memory/system.slice
```

### 4.2 内核接口文档

| v1 接口 | v2 接口 | 说明 |
|---------|---------|------|
| `memory.memsw.usage_in_bytes` | `memory.current` + `memory.swap.current` | 内存使用量 |
| `memory.swappiness` | - | v2 不支持 |
| `memory.pressure` | `memory.pressure` | PSI 压力 (相同) |

## 5. Git 规范

### 5.1 提交信息格式

```
<type>: <subject>

<body>
```

类型:
- `feat`: 新功能
- `fix`: 修复
- `docs`: 文档
- `refactor`: 重构
- `perf`: 性能

### 5.2 分支

```
main     # 主分支
```

## 6. 发布规范

### 6.1 版本号

遵循 Semantic Versioning: `MAJOR.MINOR.PATCH`

- `MAJOR`: 破坏性变更 (如 cgroup v2 移植)
- `MINOR`: 新功能
- `PATCH`: 修复

### 6.2 发布步骤

1. 更新版本号
   - `pyproject.toml`: `version = "x.y.z"`
   - `src/umrd/util.py`: `UMRD_VERSION = "x.y.z"`
   - `README.md`: `**版本**: x.y.z`

2. 构建 wheel
   ```bash
   python3 -m venv build-env
   source build-env/bin/activate
   pip install build wheel
   python3 -m build --wheel
   ```

3. 提交并打标签
   ```bash
   git add -A
   git commit -m "release: vx.y.z"
   git tag vx.y.z
   git push origin main --tags
   ```

## 7. 测试清单

发布前必须验证:

- [ ] Python 语法检查: `python3 -m py_compile src/umrd/*.py`
- [ ] 模块导入: `python3 -c "from umrd import *"`
- [ ] CLI 帮助: `umrd --help`
- [ ] Wheel 构建成功
- [ ] 无 v1 路径遗留: `grep -r "cgroup/memory" .`
- [ ] 版本号一致

## 8. 依赖关系

### 8.1 硬依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | >=3.8 | 运行环境 |

### 8.2 内核依赖

| 特性 | 必须 | 说明 |
|------|------|------|
| PSI | 是 | `/proc/pressure/*` |
| cgroup v2 | 是 | `/sys/fs/cgroup/` |
| ZRAM | 否 | 内存压缩 |
| EMM | 否 | 精细回收 |

## 9. 配置路径

| 配置项 | 默认路径 |
|--------|---------|
| Allowlist | `/run/umrd/allowlist.cfg` |
| Blocklist | `/run/umrd/blocklist.cfg` |
| 日志 | `/run/umrd/umrd.log` |
| 状态 | `/run/umrd/status` |
| 环境配置 | `/etc/umrd/umrd.env` |
