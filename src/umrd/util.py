import os
import sys
import mmap
import time
import logging
import argparse
from logging import handlers, Handler
from typing import NamedTuple, Dict

UMRD_VERSION = "2.0.0"
PAGESIZE = mmap.PAGESIZE

LOGGER = logging.getLogger('umrd')
LOGGER.setLevel(logging.DEBUG)

FORMATTER = logging.Formatter('%(asctime)s - Line %(lineno)d - %(message)s')

MAXMEMLIMIT = 9223372036854771712
totalram_pages = 0

# cgroup v2 常量
CGROUP_V2_ROOT = '/sys/fs/cgroup'
CGROUP_MEMORY_PATH = CGROUP_V2_ROOT
CGROUP_CPU_PATH = CGROUP_V2_ROOT

# cgroup v2 接口文件
CGROUP_MEMORY_STAT = 'memory.stat'
CGROUP_MEMORY_PRESSURE = 'memory.pressure'
CGROUP_MEMORY_CURRENT = 'memory.current'
CGROUP_MEMORY_MAX = 'memory.max'
CGROUP_MEMORY_LOW = 'memory.low'
CGROUP_MEMORY_HIGH = 'memory.high'
CGROUP_MEMORY_EMM = 'memory.emm'  # EMM接口(部分内核支持)
CGROUP_CGROUP_PROCS = 'cgroup.procs'
CGROUP_CONTROLLERS = 'cgroup.controllers'
CGROUP_SUBTREE_CONTROL = 'cgroup.subtree_control'

# cgroup v2 内存接口映射
def cg_memory_current(path):
    """读取cgroup v2 memory.current"""
    try:
        with open(os.path.join(path, CGROUP_MEMORY_CURRENT), 'r') as f:
            val = f.read().strip()
            return int(val) if val != 'max' else MAXMEMLIMIT
    except:
        return 0

def cg_memory_max(path):
    """读取cgroup v2 memory.max"""
    try:
        with open(os.path.join(path, CGROUP_MEMORY_MAX), 'r') as f:
            val = f.read().strip()
            return int(val) if val != 'max' else MAXMEMLIMIT
    except:
        return MAXMEMLIMIT

def cg_memory_stat(path):
    """读取并解析cgroup v2 memory.stat"""
    try:
        stat = {}
        with open(os.path.join(path, CGROUP_MEMORY_STAT), 'r') as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) == 2:
                    stat[parts[0].encode()] = parts[1]
        return stat
    except:
        return {}

def cg_has_interface(path, iface):
    return os.path.exists(os.path.join(path, iface))

def cg_write_value(path, iface, value):
    try:
        with open(os.path.join(path, iface), 'w') as f:
            f.write(str(value))
        return True
    except:
        return False

def cg_try_reclaim(path, target_bytes):
    if cg_has_interface(path, 'memory.reclaim'):
        return cg_write_value(path, 'memory.reclaim', str(target_bytes))
    return False

def cg_set_swappiness(path, swappiness):
    """cgroup v2无swappiness接口，跳过"""
    pass

def cg_set_zram_priority(path, priority):
    if cg_has_interface(path, 'memory.zram.priority'):
        return cg_write_value(path, 'memory.zram.priority', str(priority))
    return False

def cg_get_zram_stat(path):
    """获取zram统计"""
    stat = {'raw': 0, 'usage': 0}
    try:
        if cg_has_interface(path, 'memory.zram.raw_in_bytes'):
            with open(os.path.join(path, 'memory.zram.raw_in_bytes'), 'r') as f:
                stat['raw'] = int(f.read().strip())
    except:
        pass
    try:
        if cg_has_interface(path, 'memory.zram.usage_in_bytes'):
            with open(os.path.join(path, 'memory.zram.usage_in_bytes'), 'r') as f:
                stat['usage'] = int(f.read().strip())
    except:
        pass
    return stat

def cg_has_emm():
    """检查内核是否支持EMM接口"""
    return os.path.exists('/sys/kernel/mm/lru_gen/min_gen_size')

ANON_ONLY = 201
FILE_ONLY = 0

RECLAIM_MODES = [
    # Set to None means error status, scanner/reclaim should ignore the cgroup
    'simple',           # Simply use memory.reclaim
    'emm',              # Use memory.emm.age and memory.emm.reclaim
    'emm-compat',       # Use memory.emm.age and memory.emm.reclaim,
                        # and memory.reclaim, just in case
# TODO: 'simple-enhanced', # Try use swappiness to simulate Anon/File reclaim
]

# Reclaim parameters and default values, these are suppose be able to be override by
# cmdline arguments and configurable for each individual cgroup.
RECLAIM_PARAMS = {
    'swappiness': {
        'default': 60,
        'help': 'Swappiness value for cgroups being reclaimed, defaults to 60.'
    },
    'reclaim_mode': {
        'default': 'simple',
        'help': 'Reclaim modes to use, "simple" for direct reclaim using "memory.reclaim", '
                '"emm-*" for enhanced reclaiming',
        'choices': RECLAIM_MODES,
    },
    'psi_threshold': {
        'default': 10000,
        'help': 'PSI threshold between reclaim intervals, how much accumulated PSI stall '
                'time we can tolerate, UMRD will stop reclaim or reduce the amount of memory '
                'being reclaimed if this threshold is reached.'
    },
    'ratio':{
        'default': 0.02,
        'help': 'Ratio for calculating how much to reclaim.'
    },
    'ratio_anon':{
        'default': 0.01,
        'help': 'Same as "ratio", only effective for "emm" reclaim mode, '
                'overrides ratio for anon pages if set.'
    },
    'ratio_file':{
        'default': 0.02,
        'help': 'Same as "ratio", only effective for "emm" reclaim mode, '
                'overrides ratio for file pages if set.'
    },
    'interval':{
        'default': 10.0,
        'help': 'How frequently should we reclaim memory proactively (in seconds), '
                'UMRD will wait for given seconds before starting another reclaim cycle.'
    },
    'interval_anon':{
        'default': 10.0,
        'help': 'Same as "interval", only effective for "emm" reclaim mode, '
                'overrides ratio for file pages if set.'
    },
    'interval_file':{
        'default': 20.0,
        'help': 'Same as "interval", only effective for "emm" reclaim mode, '
                'overrides ratio for file pages if set. '
                'NOTE: Slab reclaim may also covered by this.'
    },
    'scan_interval':{
        'default': 10.0,
        'help': 'How frequently should we scan the single cgroup (in seconds). '
                'The value is less than or equal to interval.'
    },
    'report_interval':{
        'default': 10.0,
        'help': 'How frequently should we report the memory save results in log '
                'and mem_save.'
    },
    'zram_check_interval':{
        'default': 60.0,
        'help': 'How frequently should we check zram exist. The value should not be '
                'larger than reclaim interval.'
    },
    'age_interval':{
        'default': 30,
        'help': 'How frequently should we check the age of cgroups (in seconds). '
    },
    'swapout_limit':{
        'default': 1.0,
        'help': 'Limit swapout percentge of anon pages, UMRD will stop proactive '
                'memory reclaim if given uppper limit is hit.'
    },
    'pageout_limit':{
        'default': 1.0,
        'help': 'Limit pageout percentge of file pages, UMRD will stop proactive '
                'memory reclaim if given uppper limit is hit. Only works in emm* '
                'mode.'
    },
    'coeff_backoff':{
        'default': 4.0,
        'help': 'The backoff coefficient defines how sensitive '
                'we are to fluctuations around the target pressure.'
    },
    'max_backoff':{
        'default': 1.0,
        'help': 'Limit max adjustment.'
    },
    'adaptive':{
        'default': False,
        'help': 'Swappiness value for cgroups being reclaimed, defaults to 200.'
    },
    'save_limit':{
        'default': 1.0,
        'help': 'Limit the amount of savings on anonymous pages.'
    },
    'zram_priority':{
        'default': 0,
        'help': 'Zram priority value for cgroups being reclaimed, defaults to 0, '
                'i.e. no nothing. '
                'The value could be: 1, 2, 3, 4, and the comp algorithms are '
                '(1)lz4, (2)lzo-rle, (3)lz4hc, (4)zstd. '
                'Setting zram.priority also change the subcgroups\'. '
                'If the value is the minus, only the single cgroup is changed.'
    },
    'pct_trigger_reclaim':{
        'default': 0.0,
        'help': 'Reclaim those cgroups whose usage percentage (usage / total) '
                'exceed this threshold. The value should be in [0, 1].'
    },
}

# oversell params
PROACTIVE_HIGH = 40
PROACTIVE_NORM = 20
PROACTIVE_LOW = 0
OVERSELL_CYCLE = 10
OVERSELL_RATIO_FILE = 0.001

def get_curr_time():
    return time.time()

class ReclaimParams:
    """
    Data class for commonly used reclaim params.
    Using plain class with __slots__ for better performance and compatibility,
    this is supposed to be shared by cgroups.
    """
    __slots__ = RECLAIM_PARAMS.keys()

    def __hint__(self):
        """
        This is here purely for type hinting, we need to find a smarter way to do this.
        """
        self.swappiness: int = None
        self.swapout_limit: float = None
        self.pageout_limit: float = None
        self.reclaim_mode: str = None
        self.psi_threshold: int = None
        self.coeff_backoff: int = None
        self.max_backoff: float = None
        self.ratio: float = None
        self.ratio_file: float = None
        self.ratio_anon: float = None
        self.interval: float = None
        self.interval_file: float = None
        self.interval_anon: float = None
        self.scan_interval: float = None
        self.report_interval: float = None
        self.zram_check_interval: float = None
        self.age_interval: int = None
        self.adaptive: bool = None
        self.save_limit: float = None
        self.zram_priority: int = None
        self.pct_trigger_reclaim: float = None

    def __init__(self, **kwargs):
        self.__hint__()
        for key, val in kwargs.items():
            if not key in RECLAIM_PARAMS.keys():
                raise AttributeError("Unknown reclaim parameter %s" % key)

        for key, val in RECLAIM_PARAMS.items():
            setattr(self, key, kwargs.get(key, val['default']))

        if self.ratio_file is None:
            self.ratio_file = self.ratio

        if self.ratio_anon is None:
            self.ratio_anon = self.ratio

    def __eq__(self, p: 'ReclaimParams') -> bool:
        """Overrides the default implementation"""
        if not isinstance(p, ReclaimParams):
            return False
        res = self.swappiness == p.swappiness and \
              self.swapout_limit == p.swapout_limit and \
              self.pageout_limit == p.pageout_limit and \
              self.reclaim_mode == p.reclaim_mode and \
              self.psi_threshold == p.psi_threshold and \
              self.coeff_backoff == p.coeff_backoff and \
              self.max_backoff == p.max_backoff and \
              self.ratio == p.ratio and \
              self.ratio_file == p.ratio_file and \
              self.ratio_anon == p.ratio_anon and \
              self.interval == p.interval and \
              self.interval_file == p.interval_file and \
              self.interval_anon == p.interval_anon and \
              self.scan_interval == p.scan_interval and \
              self.report_interval == p.report_interval and \
              self.zram_check_interval == p.zram_check_interval and \
              self.age_interval == p.age_interval and \
              self.adaptive == p.adaptive and \
              self.save_limit == p.save_limit and \
              self.pct_trigger_reclaim == p.pct_trigger_reclaim
        return res

    @classmethod
    def update_default_values(cls, conf: argparse.Namespace) -> dict:
        confs = {
            key: getattr(conf, key) for key, val in RECLAIM_PARAMS.items()
        }
        new_default_params = cls(**confs)
        if not new_default_params.validate_default_values():
            raise AttributeError("Invalid Default Reclaim Params fron arguments.")
        return confs

    def validate_default_values(self) -> bool:
        if self.swapout_limit < 0:
            LOGGER.info('Invalid swapout_limit %f (<0)', self.swapout_limit)
            return False

        if self.pageout_limit < 0:
            LOGGER.info('Invalid pageout_limit %f (<0)', self.pageout_limit)
            return False

        if self.swappiness < 0 or self.swappiness > 200:
            LOGGER.info('Invalid swappiness %f. Please set between 0 and 200', self.swappiness)
            return False

        if self.ratio < 0:
            LOGGER.info('Invalid reclaim_ratio %f', self.ratio)
            return False

        if self.interval <= 0:
            LOGGER.info('Invalid sleep_interval %f', self.interval)
            return False

        if self.interval_file <= 0:
            LOGGER.info('Invalid interval_file %f', self.interval_file)
            return False

        if self.interval_anon <= 0:
            LOGGER.info('Invalid interval_anon %f', self.interval_anon)
            return False

        if self.scan_interval <= 0:
            LOGGER.info('Invalid scan_interval %f', self.scan_interval)
            return False

        if self.report_interval <= 0:
            LOGGER.info('Invalid report_interval %f', self.report_interval)
            return False

        if self.zram_check_interval <= 0:
            LOGGER.info('Invalid zram_check_interval %f', self.zram_check_interval)
            return False

        if self.age_interval <= 0:
            LOGGER.info('Invalid age_interval %f', self.age_interval)
            return False

        if self.psi_threshold < 0:
            LOGGER.info('Invalid pressure %d (<0)', self.psi_threshold)
            return False

        if self.max_backoff < 0:
            LOGGER.info('Invalid max_backoff %f (<0)', self.max_backoff)
            return False

        if self.coeff_backoff < 0:
            LOGGER.info('Invalid coeff_backoff %d (<0)', self.coeff_backoff)
            return False

        if self.reclaim_mode not in RECLAIM_MODES:
            LOGGER.info('Invalid reclaim_mode %s', self.reclaim_mode)
            return False

        if self.save_limit < 0:
            LOGGER.info('Invalid save_limit %f', self.save_limit)
            return False

        if self.pct_trigger_reclaim < 0 or self.pct_trigger_reclaim > 1:
            LOGGER.info('Invalid pct_trigger_reclaim %f', self.pct_trigger_reclaim)
            return False

        return True

    def validate_runtime_config(self) -> bool:
        return self.validate_default_values()

    def read_conf(self, conf: Dict[str, str]) -> bool:
        for _k, _v in conf.items():
            if _k in RECLAIM_PARAMS:
                try:
                    setattr(self, _k, type(RECLAIM_PARAMS[_k]["default"])(_v))
                except ValueError as exp:
                    LOGGER.info('Parsng rules failed: setting=(%s,%s) exp=%s', _k, _v, exp)
                    self.reclaim_mode = None
                except Exception as exp:
                    LOGGER.info('Unknown parse error: setting=(%s,%s) exp=%s', _k, _v, exp)
                    self.reclaim_mode = None

        return self.validate_runtime_config()


class ReclaimStat:
    """
    Reclaim data collector
    """
    __slots__ = [
        'reclaimed_anon',
        'reclaimed_file',
        'reclaimed_simple',
    ]

    def __init__(self):
        self.reclaimed_anon: int = 0
        self.reclaimed_file: int = 0
        self.reclaimed_simple: int = 0

    def __iadd__(self, other: 'ReclaimStat'):
        self.reclaimed_anon += other.reclaimed_anon
        self.reclaimed_file += other.reclaimed_file
        self.reclaimed_simple += other.reclaimed_simple

        return self

    def total(self) -> int:
        return self.reclaimed_anon + self.reclaimed_file + self.reclaimed_simple

    def clear(self):
        self.reclaimed_anon = 0
        self.reclaimed_file = 0
        self.reclaimed_simple = 0


# Rule used for allow_rules
# Allowed for reclaim, itself and its children
ALLOWED = 0
# Blocked from reclaim, but still scan its child cgroup (because child cgroup is targed for reclaim)
SCAN_ONLY = 2
# TODO: Use a unified rule system?

class RuleItem(NamedTuple):
    path: str
    type: str
    params: ReclaimParams

def detect_wait(wait, output_dir):
    if not wait:
        return
    while True:
        if os.path.exists(os.path.join(output_dir, 'enable')):
            return
        time.sleep(60)

def detect_report_only(output_dir):
    return os.path.exists(os.path.join(output_dir, 'report_only'))

def detect_cgroup_path():
    """Auto-detect the best cgroup path to reclaim."""
    candidates = [
        os.path.join(CGROUP_V2_ROOT, 'kubepods'),
        os.path.join(CGROUP_V2_ROOT, 'kubepods', 'burstable'),
        os.path.join(CGROUP_V2_ROOT, 'system.slice'),
        CGROUP_V2_ROOT,
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return ""

def auto_create_config(conf):
    """Auto-create default allowlist if not exists."""
    allowlist_path = conf.allowlist
    blocklist_path = conf.blocklist
    oversell_allowlist_path = conf.allowlist_oversell

    if conf.allowlist_empty:
        return

    allowdir = os.path.dirname(allowlist_path)
    os.makedirs(allowdir, exist_ok=True)

    if not os.path.exists(allowlist_path):
        cgroup_path = detect_cgroup_path()
        if cgroup_path:
            with open(allowlist_path, 'w') as f:
                f.write(cgroup_path + '\n')
            print(f'[UMRD] Auto-created allowlist: {allowlist_path} -> {cgroup_path}')

    if not os.path.exists(oversell_allowlist_path):
        cgroup_path = detect_cgroup_path()
        if cgroup_path:
            with open(oversell_allowlist_path, 'w') as f:
                f.write(cgroup_path + '\n')

    if not conf.blocklist_empty and conf.blocklist:
        blockdir = os.path.dirname(blocklist_path)
        os.makedirs(blockdir, exist_ok=True)
        if not os.path.exists(blocklist_path):
            with open(blocklist_path, 'w') as f:
                f.write(os.path.join(CGROUP_V2_ROOT, 'umrd') + '\n')

def check_cgroup_v2() -> bool:
    """检查cgroup v2是否可用."""
    cgroup_path = CGROUP_V2_ROOT
    
    if not os.path.exists(cgroup_path):
        LOGGER.error('cgroup v2 not found at %s', cgroup_path)
        LOGGER.error('UMRD requires cgroup v2 to be properly mounted')
        return False
    
    controllers_path = os.path.join(cgroup_path, CGROUP_CONTROLLERS)
    if os.path.exists(controllers_path):
        with open(controllers_path, 'r') as f:
            controllers = f.read().strip()
            if 'memory' in controllers:
                return True
    
    memory_current = os.path.join(cgroup_path, 'memory.current')
    if os.path.exists(memory_current):
        return True
    
    LOGGER.error('cgroup v2 memory controller not available')
    LOGGER.error('Please ensure memory controller is enabled')
    return False

def check_psi() -> bool:
    """检查PSI是否可用."""
    psi_path = '/proc/pressure/memory'
    if os.path.exists(psi_path):
        return True
    LOGGER.error('PSI (Pressure Stall Information) not found')
    LOGGER.error('UMRD requires /proc/pressure/memory')
    LOGGER.error('Please ensure kernel 4.20+ with PSI support')
    return False

def check_conf(conf: argparse.Namespace) -> bool:
    os.makedirs(conf.output_dir, exist_ok=True)
    
    if not check_cgroup_v2():
        return False
    
    if not check_psi():
        return False
    
    detect_wait(conf.wait, conf.output_dir)

    if 'DISK_SIZE' in os.environ:
        conf.disk_size = int(os.environ['DISK_SIZE']) # MB
        conf.disk_size = max(0, conf.disk_size)
    else:
        conf.disk_size = 0

    if 'ZRAM_REJECT_SIZE' in os.environ:
        conf.zram_reject_size = int(os.environ['ZRAM_REJECT_SIZE'])
        conf.zram_reject_size = min(max(conf.zram_reject_size, -1), 4096)
    else:
        conf.zram_reject_size = -1

    if conf.disk_size > 0:
        conf.disk_path = '/opt/eklet-agent/swap.blk'
    else:
        conf.disk_path = None

    LOGGER.info('disk_path = %s, disk_size = %s, zram_reject_size = %s',
                conf.disk_path, conf.disk_size, conf.zram_reject_size)

    if os.path.exists('/proc/sys/kernel/cpu_qos'):
        with open('/proc/sys/kernel/cpu_qos', 'rb') as _f:
            conf.init_cpu_qos = int(_f.readline().decode('utf-8'))
    else:
        conf.init_cpu_qos = -1


    if conf.logfile:
        dir_log_file = os.path.dirname(conf.logfile)
        os.makedirs(dir_log_file, exist_ok=True)
        if not os.path.exists(conf.logfile):
            with open(conf.logfile, 'w') as _f:
                pass
        file_handler = handlers.RotatingFileHandler(conf.logfile, backupCount=3, maxBytes=50000000)
        file_handler.setLevel(level=logging.INFO)
        file_handler.setFormatter(FORMATTER)
        LOGGER.addHandler(file_handler)
        conf.log_file_handler = file_handler

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level=logging.DEBUG)
    handler.setFormatter(FORMATTER)
    if conf.quiet:
        log_level = 'quiet'
        handler.setLevel(logging.ERROR)
        LOGGER.error('Quiet mode enabled.')
    elif conf.verbose:
        log_level = 'verbose'
        handler.setLevel(logging.INFO)
        LOGGER.info('Verbose mode enabled.')
    elif conf.debug:
        log_level = 'debug'
        handler.setLevel(logging.DEBUG)
        LOGGER.debug('Debug mode enabled.')
    else:
        log_level = 'debug'
        handler.setLevel(logging.DEBUG)
        LOGGER.info('Debug mode enabled. No log level is specified.')
    LOGGER.addHandler(handler)
    conf.log_console_handler = handler

    if conf.allowlist_empty:
        conf.allowlist = ''
        conf.allowlist_oversell = ''
    else:
        if conf.allowlist == '':
            LOGGER.info('Allowlist not set. If allowlist_empty is set True, ' + \
                         'allowlist will be ignored.')
            return False
        if conf.allowlist_oversell == '':
            LOGGER.info('Allowlist not set. If allowlist_empty is set True, ' + \
                         'allowlist will be ignored.')
            return False
        auto_create_config(conf)

    if conf.blocklist_empty:
        conf.blocklist = ''
    else:
        if conf.blocklist == '':
            LOGGER.info('Blocklist not set. If blocklist_empty is set True, ' + \
                         'blocklist will be ignored.')
            return False
        auto_create_config(conf)

    conf.percgroup_memsave = os.path.join(conf.output_dir, 'cgroup_mem_save')
    conf.global_memsave = os.path.join(conf.output_dir, 'mem_save')
    conf.reclaimed_in_last_period = os.path.join(conf.output_dir, 'reclaimed_in_last_period')
    conf.monitored_cgroups = os.path.join(conf.output_dir, 'monitored_cgroups.list')
    conf.umrd_status = os.path.join(conf.output_dir, 'status')

    with open(os.path.join(conf.output_dir, 'umrd_version'), 'wb+') as _f:
        _f.write(UMRD_VERSION.encode('ascii'))

    conf.boot_timestamp = get_curr_time()
    pid = os.getpid()
    with open(conf.umrd_status, 'wb+') as _f:
        s = 'Pid: %d\nStatus: Booting\nBootTimestamp: %d s\n' + \
            'AccumReclaimSimple: 0 KB\n' + \
            'AccumReclaimAnon: 0 KB\nAccumReclaimFile: 0 KB\n' + \
            'LastReclaimTimestamp: 0 s\nLastReclaimCost: 0 s\n'
        s = s % (pid, conf.boot_timestamp)
        _f.write(s.encode('ascii'))

    nokmem = False
    with open('/proc/cmdline', 'rb') as _f:
        cmdline = _f.readlines()
        cmdline = b'|'.join(cmdline)
        if b'cgroup.memory=nokmem' in cmdline:
            nokmem = True

    conf.has_cgroup_zram_stat = False
    if not nokmem and os.path.exists(os.path.join(CGROUP_V2_ROOT, 'memory.zram.raw_in_bytes')):
        conf.has_cgroup_zram_stat = True

    # 定义变量
    oversell = "1" if not conf.disable_oversell else "0"
    open_zram = "1" if conf.open_zram else "0"

    # 创建一个字典来存储键值对
    hot_config = {
        "log_file_handler_level": log_level,
        "log_console_handler_level": log_level,
        "oversell": oversell,
        "open_zram": open_zram
    }

    # 打开文件并写入内容
    if not os.path.exists(conf.hot_reload):
        with open(conf.hot_reload, 'w') as _f:
            for key, value in hot_config.items():
                _f.write(f"{key}={value}\n")

    return True

def clear_umrd_cgroup():
    root_cgroup = CGROUP_V2_ROOT
    if not os.path.exists(root_cgroup):
        return
    umrd_cgroups = [i for i in os.listdir(root_cgroup) if i.startswith('umrd-')]
    for i in umrd_cgroups:
        _cg = os.path.join(root_cgroup, i)
        procs_file = os.path.join(_cg, CGROUP_CGROUP_PROCS)
        if os.path.exists(procs_file):
            with open(procs_file, 'rb') as _f:
                procs = _f.readlines()
                if len(procs) == 0:
                    try:
                        os.rmdir(_cg)
                    except OSError:
                        pass

def parse_textinfo(path: str):
    info = {}
    try:
        info_fd = os.open(path, os.O_RDONLY, 0o0400)
        # Assuming read files won't exceed 4096 chars
        info_read = os.read(info_fd, 4096)
        os.close(info_fd)
        return dict(
            (tok[0], tok[1]) for tok in [line.split() for line in info_read.splitlines()]
        )
    except:
        pass
    return info

def get_kernel_version():
    with open('/proc/version', 'rb') as _f:
        ver = _f.readline()
    return ver

def enable_lru_gen():
    if not os.path.exists('/sys/kernel/mm/lru_gen/enabled'):
        LOGGER.debug('lru_gen not supported')
        return
    LOGGER.debug('  >>> echo Y > /sys/kernel/mm/lru_gen/enabled')
    try:
        with open('/sys/kernel/mm/lru_gen/enabled', 'wb') as _f:
            _f.write(b'Y')
    except:
        LOGGER.info('enable lru_gen failed')
        sys.exit(1)

def enable_wujing():
    if not os.path.exists('/proc/sys/vm/wujing_enable'):
        LOGGER.debug('/proc/sys/vm/wujing_enable not supported')
        return
    LOGGER.debug('  >>> echo 1 > /proc/sys/vm/wujing_enable')
    try:
        with open('/proc/sys/vm/wujing_enable', 'wb') as _f:
            _f.write(b'1')
    except:
        LOGGER.info('enable wujing failed')
        sys.exit(1)

def set_totalram_pages():
    global totalram_pages
    ret = parse_textinfo('/proc/meminfo')
    totalram_pages = int(ret[b'MemTotal:']) * 1024

def get_totalram_pages():
    return totalram_pages

def set_swappiness(ver):
    if not b'5.4.203-1-tlinux4-0011' in ver:
        return

    kube_path = os.path.join(CGROUP_V2_ROOT, 'kubepods')
    if not os.path.exists(kube_path):
        return

    swappiness_path = os.path.join(kube_path, 'memory.swappiness')
    if os.path.exists(swappiness_path):
        with open(swappiness_path, 'wb') as _f:
            _f.write(b'0')

    for root, dirs, files in os.walk(kube_path):
        for directory in dirs:
            cg_path = os.path.join(root, directory, 'memory.swappiness')
            if os.path.exists(cg_path):
                with open(cg_path, 'wb') as _f:
                    _f.write(b'0')

def check_zram():
    with open('/proc/swaps', 'rb') as _f:
        swaps = _f.readlines()
    for line in swaps:
        if b'zram0' in line:
            return True
    return False

def modprobe_emm_modules():
    emm_modules = ['emm_extentions', 'emm_coreutils', 'emm_zram']
    with open('/proc/modules', 'rb') as _f:
        installed_modules = b''.join(_f.readlines())
    for module in emm_modules:
        if module.encode('ascii') in installed_modules:
            continue
        cmd = 'modprobe %s' % module
        if os.system(cmd):
            LOGGER.info(cmd + ' failed')
            sys.exit(1)

def set_swapcache_fastfree():
    if not os.path.exists('/proc/sys/vm/swapcache_fastfree'):
        LOGGER.debug('/proc/sys/vm/swapcache_fastfree not supported')
        return
    LOGGER.debug('  >>> echo 1 > /proc/sys/vm/swapcache_fastfree')
    try:
        with open('/proc/sys/vm/swapcache_fastfree', 'wb') as _f:
            _f.write(b'1')
    except:
        LOGGER.info('enable swapcache_fastfree failed')
        sys.exit(1)

def set_ramdisk_swaptune():
    if not os.path.exists('/proc/sys/vm/ramdisk_swaptune'):
        LOGGER.debug('/proc/sys/vm/ramdisk_swaptune not supported')
        return
    LOGGER.debug('  >>> echo 1 > /proc/sys/vm/ramdisk_swaptune')
    try:
        with open('/proc/sys/vm/ramdisk_swaptune', 'wb') as _f:
            _f.write(b'1')
    except:
        LOGGER.info('enable ramdisk_swaptune failed')
        sys.exit(1)

def init_wujing(ver, use_emm_zram):
    set_totalram_pages()
    set_swappiness(ver)

    if use_emm_zram:
        modprobe_emm_modules()
        set_swapcache_fastfree()
        set_ramdisk_swaptune()
    else:
        enable_wujing()

    enable_lru_gen()

def remodprobe_default_zram(comp_alg=None):
    LOGGER.debug('Resetting zram...')
    LOGGER.debug('  >>> modprobe -r zram')
    if os.system('modprobe -r zram'):
        LOGGER.info('cannot remove zram')

    LOGGER.debug('  >>> modprobe zram num_devices=1')
    if os.system('modprobe zram num_devices=1'):
        LOGGER.warning('cannot modprobe zram, zram may not be available')

def remodprobe_emm_zram(comp_alg=None):
    LOGGER.debug('Resetting zram...')
    LOGGER.debug('  >>> modprobe -r emm_zram')
    if os.system('modprobe -r emm_zram'):
        LOGGER.info('cannot remove emm_zram')
    if os.system('modprobe emm_zram'):
        LOGGER.warning('cannot modprobe emm_zram, zram may not be available')

def check_loop_dev(disk_path):
    '''
    Check whether loop0 attach backing_file disk_path

    Return (bool):
        True: loop0 device has done. no need to do anything
        False: loop0 device is to be create
        Exception: unable to resolve those exception automatically
    '''
    if os.path.exists('/sys/block/loop0/loop/backing_file'):
        # check if disk file has been set as loop device
        with open('/sys/block/loop0/loop/backing_file', 'rb') as _f:
            loop0_backfile = _f.readline().decode('utf-8').strip()
        if loop0_backfile == disk_path:
            if os.path.exist(disk_path):
                return True
            if os.system('losetup -d /dev/loop0'):
                raise Exception('losetup -d /dev/loop0 failed')
            return False
        else:
            raise Exception('loop0 has backing_file %s' % loop0_backfile)
    return False

def create_empty_dev(disk_path, disk_size):
    if os.path.exists(disk_path):
        disk_stat = os.stat(disk_path)
        disk_stat_blknum = int(disk_stat.st_blocks * 512 / 1024 ** 2)
        if disk_stat_blknum == disk_size:
            return

        LOGGER.info('  >>> rm %s', disk_path)
        rm_flag = True
        for _ in range(3):
            if not os.path.exists(disk_path):
                rm_flag = False
                break
            if not os.system('rm %s' % disk_path):
                rm_flag = False
                break
            time.sleep(1)
        if rm_flag:
            raise Exception('rm %s failed' % disk_path)

    LOGGER.info('  >>> fallocate -l %sM %s', disk_size, disk_path)
    alloc_flag = True
    for _ in range(3):
        if not os.system('fallocate -l %sM %s' % (disk_size, disk_path)):
            alloc_flag = False
            break
        time.sleep(1)
    if alloc_flag:
        raise Exception('fallocate -l %sM %s failed' % (disk_size, disk_path))

    disk_stat = os.stat(disk_path)
    disk_stat_blknum = int(disk_stat.st_blocks * 512 / 1024 ** 2)
    if disk_stat_blknum != disk_size:
        raise Exception('fallocate -l %sM %s success, but get %s disk block num. target is %s' % \
                        (disk_stat_blknum, disk_size))

def set_loop_dev(disk_path, disk_size):
    LOGGER.info('  >>> losetup --direct-io=on /dev/loop0 %s', disk_path)
    losetup_flag = True
    for i in range(2):
        if not os.system('losetup --direct-io=on /dev/loop0 %s' % disk_path):
            losetup_flag = False
            break
        LOGGER.info('  round %s: trying losetup...', i + 1)
        time.sleep(1)
    if losetup_flag:
        raise Exception('losetup --direct-io=on /dev/loop0 %s failed' % disk_path)

def ensure_zram(comp_alg=None, use_emm_zram=False,
                disk_path='/opt/eklet-agent/swap.blk',
                disk_size=0, zram_reject_size=-1):
    if check_zram():
        return
    if use_emm_zram:
        remodprobe_emm_zram(comp_alg)
    else:
        remodprobe_default_zram(comp_alg)

    for _ in range(3):
        LOGGER.debug('  >>> echo 1 > /sys/block/zram0/reset')
        try:
            with open('/sys/block/zram0/reset', 'wb') as _f:
                _f.write(b'1')
        except Exception as exp:
            LOGGER.debug('reset failed for %s, retrying...', exp)
            time.sleep(2)
            continue
        break
    time.sleep(2)

    if comp_alg is not None:
        LOGGER.debug('  >>> echo %s > /sys/block/zram0/comp_algorithm', comp_alg)
        try:
            with open('/sys/block/zram0/comp_algorithm', 'wb') as _f:
                _f.write(comp_alg.encode('ascii'))
        except:
            LOGGER.info('Set zram comp_algorithm as default.')

    memtotal = int(get_totalram_pages() / 1024) # KB

    if disk_size != 0 and disk_path:
        set_flag = True
        for _ in range(3):
            try:
                if check_loop_dev(disk_path):
                    set_flag = False
                    break
                create_empty_dev(disk_path, disk_size)
                set_loop_dev(disk_path, disk_size)
                LOGGER.debug('  >>> echo /dev/loop0 > /sys/block/zram0/backing_dev')
                with open('/sys/block/zram0/backing_dev', 'wb') as _f:
                    _f.write(b'/dev/loop0')

                LOGGER.debug('  >>> echo %s > /sys/block/zram0/reject_size', zram_reject_size)
                with open('/sys/block/zram0/reject_size', 'wb') as _f:
                    _f.write(str(zram_reject_size).encode('ascii'))
                set_flag = False
                break
            except Exception as exp:
                LOGGER.info('%s', exp)
        if set_flag:
            sys.exit(1)
        memtotal += disk_size * 1024

    memtotal = '%sK' % memtotal
    if not os.path.exists('/sys/block/zram0'):
        LOGGER.warning('zram module not loaded, skipping zram setup')
        return

    LOGGER.debug('  >>> echo %s > /sys/block/zram0/disksize', memtotal)
    with open('/sys/block/zram0/disksize', 'wb') as _f:
        _f.write(str(memtotal).encode('ascii'))
    time.sleep(2)

    if os.system('mkswap /dev/zram0'):
        LOGGER.warning('mkswap /dev/zram0 failed, skipping zram swap setup')
        return
    os.system('swapon --version')
    os.system('swapon -s')
    if os.system('swapon -p 10 /dev/zram0'):
        LOGGER.warning('swapon /dev/zram0 failed')
        return

    if check_zram():
        return
    LOGGER.warning('zram setup incomplete, continuing without zram')


def close_zram(conf: argparse.Namespace):
    conf.open_zram = False
    if check_zram():
        for _ in range(5):
            if not os.system('swapoff /dev/zram0'):
                LOGGER.info('  >>> swapoff /dev/zram0 success')
                return
        LOGGER.error('  >>> swapoff /dev/zram0 failed')


def get_cpu_util():
    with open('/proc/stat', 'rb') as _f:
        cpu_total = -1
        cpu_used = -1
        lines = _f.readlines()
        for line in lines:
            if line.find(b'cpu ') >= 0:
                list_cpu = line.split()
                user = int(list_cpu[1])
                nice = int(list_cpu[2])
                system = int(list_cpu[3])
                idle = int(list_cpu[4])
                iowait = int(list_cpu[5])
                irq = int(list_cpu[6])
                softirq = int(list_cpu[7])
                cpu_total = user + nice + system + idle + iowait + irq + softirq
                cpu_used = user + nice + system + irq + softirq
                break

    return cpu_used, cpu_total


def enable_oversell(conf: argparse.Namespace):
    if os.path.exists('/proc/sys/vm/page_reporting_supported'):
        with open('/proc/sys/vm/page_reporting_supported', 'rb') as _f:
            conf.page_reporting_supported = int(_f.readline().decode('utf-8'))
    else:
        conf.page_reporting_supported = -1

    if conf.page_reporting_supported == 1:
        if os.path.exists('/proc/sys/vm/page_reporting_enable'):
            with open('/proc/sys/vm/page_reporting_enable', 'rb') as _f:
                conf.init_page_reporting_enable = int(_f.readline().decode('utf-8'))
        else:
            conf.init_page_reporting_enable = -1

        if os.path.exists('/proc/sys/vm/compaction_proactiveness'):
            with open('/proc/sys/vm/compaction_proactiveness', 'rb') as _f:
                conf.init_compaction_proactiveness = int(_f.readline().decode('utf-8'))
        else:
            conf.init_compaction_proactiveness = -1

        conf.proactive_high = PROACTIVE_HIGH
        conf.proactive_norm = PROACTIVE_NORM
        conf.proactive_low = PROACTIVE_LOW
        conf.cycle_sleep = OVERSELL_CYCLE
        conf.interval_file = OVERSELL_CYCLE
        conf.ratio_file = OVERSELL_RATIO_FILE
        # In oversell mode, turn off zram
        # close_zram(conf)



def disable_oversell(conf: argparse.Namespace):
    conf.page_reporting_supported = -1
    conf.init_page_reporting_enable = -1
    conf.init_compaction_proactiveness = -1
    # conf.open_zram = True
    # ensure_zram(conf.comp_alg, conf.use_emm_zram, conf.disk_path, conf.disk_size, conf.zram_reject_size)


def get_global_pressure_some_avg10(type_of_pressure: str):
    try:
        with open('/proc/pressure/' + type_of_pressure, 'rb') as psi:
            some = psi.readline().replace(b'=', b' ').split()
            return float(some[2])
    except Exception as exp:
        LOGGER.info('get_global_pressure_some_avg10 %s failed exp: %s', type_of_pressure, exp)
    return -1

def get_global_pressure_some_total(type_of_pressure: str):
    try:
        with open('/proc/pressure/' + type_of_pressure, 'rb') as psi:
            some = psi.readline().replace(b'=', b' ').split()
            return int(some[8])
    except Exception as exp:
        LOGGER.info('get_global_pressure_some_total %s failed exp: %s', type_of_pressure, exp)
    return -1

def get_zram(zrampath='/sys/block/zram0/mm_stat'):
    if not os.path.exists(zrampath):
        return None, None

    with open(zrampath, 'rb') as _f:
        stats = _f.readline().split()
        return int(stats[0]), int(stats[1])


def set_log_level(handler: Handler, mode: str):
    if mode == 'quiet':
        handler.setLevel(logging.ERROR)
    elif mode == 'verbose':
        handler.setLevel(logging.INFO)
    else:
        handler.setLevel(logging.DEBUG)
