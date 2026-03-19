# Copyright (c) 2024 w7panel
# SPDX-License-Identifier: Apache-2.0
import io
import os
import sys
import time
import pstats
import argparse
import cProfile
import collections

from .util import LOGGER, ReclaimStat
from .util import CGROUP_V2_ROOT, CGROUP_CPU_PATH
from .util import parse_textinfo, get_global_pressure_some_avg10, get_global_pressure_some_total, get_cpu_util, get_zram
from .util import get_curr_time, detect_report_only, cg_memory_current, cg_memory_stat, cg_has_interface
from .cgtree import CgroupTree

CFS_PEROID_US = 100000
LOW_UTIL = 0
HIGH_UTIL = 1

OVERSELL_PROHIGH = 0
OVERSELL_PRONORM = 1
OVERSELL_PROLOW = 2

class UMRD:
    """
    Main entry class of UMRD
    """
    def __init__(self, conf: argparse.Namespace, cmd_conf: dict, profile: cProfile.Profile) -> None:
        self.pid: int = os.getpid()
        LOGGER.info('current pid = %s', self.pid)

        LOGGER.info('Configuration:')
        for key, val in vars(conf).items():
            LOGGER.info('  %s = %s', key, val)

        proc_cgroup_info = self.parse_proc_cgroup()
        self.cpu_cgroup = cpu_cgroup = ''
        try:
            cpu_cgroup = proc_cgroup_info['cpu']
            self.cpu_cgroup = os.path.join(CGROUP_CPU_PATH, cpu_cgroup)
        except KeyError:
            LOGGER.error('umrd process cgroup has no cpu cgroup information: %s', proc_cgroup_info)
        self.mem_cgroup = mem_cgpath = ''
        try:
            mem_cgpath = proc_cgroup_info['mem']
            self.mem_cgroup = os.path.join(CGROUP_V2_ROOT, mem_cgpath)
        except KeyError:
            LOGGER.error('umrd process cgroup has no mem cgroup information: %s', proc_cgroup_info)

        # 超卖不限制cpu使用率
        if conf.disable_oversell:
            # systemd deployed
            if 'UMRDSYSTEMD' in os.environ and cpu_cgroup != '':
                LOGGER.info('UMRD in UMRDSYSTEMD mode')
                self.set_cpu_quota_and_offline(conf)
            elif 'kubepods' in cpu_cgroup:
                # set a low priority.
                LOGGER.info('UMRD in K8S mode')
                self.nice = os.nice(19)
            else:
                LOGGER.info('UMRD in other mode')
                try:
                    cpu_cgroup = os.path.join(CGROUP_CPU_PATH, 'umrd-%s' % (self.pid))
                    os.makedirs(cpu_cgroup, exist_ok=True)
                    with open(os.path.join(cpu_cgroup, 'cgroup.procs'), 'wb') as _f:
                        _f.write(str(self.pid).encode('ascii'))
                    self.cpu_cgroup = cpu_cgroup
                    self.set_cpu_quota_and_offline(conf)
                except Exception as exp:
                    self.cpu_offline_clean()
                    self.cpu_cgroup = ''
                    self.nice = os.nice(19)
                    LOGGER.info('Failed to make %s and migrate umrd process to '
                                     'the new cpu cgroup for %s', self.cpu_cgroup, exp)

                try:
                    mem_cgroup = os.path.join(CGROUP_V2_ROOT, 'umrd-%s' % (self.pid))
                    os.makedirs(mem_cgroup, exist_ok=True)
                    with open(os.path.join(mem_cgroup, 'cgroup.procs'), 'wb') as _f:
                        _f.write(str(self.pid).encode('ascii'))
                    self.mem_cgroup = mem_cgroup
                except Exception as exp:
                    LOGGER.info('Failed to make %s and migrate umrd process to '
                                     'the new cgroup for %s', self.mem_cgroup, exp)

        LOGGER.info('UMRD cpu_cgroup=%s mem_cgroup=%s', self.cpu_cgroup, self.mem_cgroup)
        self.memcg_total_history = collections.deque((0, 0, 0, 0, 0, 0), maxlen=6)
        self.cgtree = CgroupTree(conf, cmd_conf, self.mem_cgroup)
        self.total_reclaim_stat = ReclaimStat()
        self.reclaim_stat = ReclaimStat()
        self.profile = profile
        self.last_cpu_used = 0
        self.last_cpu_total = 0

        self.init_cpu_qos = conf.init_cpu_qos
        self.cycle_sleep = conf.cycle_sleep

        self.global_mem_total_history = collections.deque((0, 0), maxlen=2)

    def set_cpu_quota_and_offline(self, conf: argparse.Namespace):
        with open(self.cpu_cgroup + '/cpu.cfs_period_us', 'wb') as _f:
            _f.write(str(CFS_PEROID_US).encode('utf8'))
            LOGGER.info('[set_cpu_quota_and_offline] set %s/cpu.cfs_period_us %s', \
                        self.cpu_cgroup, CFS_PEROID_US)
        cfs_quota_us = int(CFS_PEROID_US * conf.cpu_quota_ratio)
        with open(self.cpu_cgroup + '/cpu.cfs_quota_us', 'wb') as _f:
            _f.write(str(cfs_quota_us).encode('ascii'))
            LOGGER.info('[set_cpu_quota_and_offline] set %s/cpu.cfs_quota_us %s', \
                        self.cpu_cgroup, cfs_quota_us)
        if conf.set_offline and self.cpu_offline_condition():
            with open('/proc/sys/kernel/cpu_qos', 'wb') as _f:
                _f.write(b'1')
                LOGGER.info('[set_cpu_quota_and_offline] enable /proc/sys/kernel/cpu_qos')
            with open(self.cpu_cgroup + '/cpu.offline', 'wb') as _f:
                _f.write(b'1')
                LOGGER.info('[set_cpu_quota_and_offline] set %s/cpu.offline 1', self.cpu_cgroup)

    def parse_proc_cgroup(self):
        with open('/proc/%s/cgroup' % self.pid, 'rb') as _f:
            data = [line.strip() for line in _f]
        ret = {}
        for line in data:
            infos = line.split(b':')
            ctrler = infos[1]
            path = infos[2].decode('utf-8').rstrip('/')
            
            # cgroup v2: single unified hierarchy, format is "0::/path"
            # ctrler is empty in cgroup v2
            if not ctrler or ctrler == b'0':
                ret['cpu'] = path.lstrip('/')
                ret['mem'] = path.lstrip('/')
                continue
            
            # cgroup v1: multiple hierarchies
            if b'cpu' in ctrler:
                ret['cpu'] = path.lstrip('/')
            elif ctrler == b'memory':
                ret['mem'] = path.lstrip('/')
        return ret

    def cpu_offline_condition(self):
        if not os.path.exists('/proc/sys/kernel/cpu_qos'):
            LOGGER.error('[cpu_offline_condition] /proc/sys/kernel/cpu_qos not exist')
            return False
        cpu_offline_path = os.path.join(CGROUP_CPU_PATH, 'cpu.offline')
        if not os.path.exists(cpu_offline_path):
            LOGGER.error('[cpu_offline_condition] %s not exist', cpu_offline_path)
            return False

        with open('/proc/version', 'rb') as _f:
            ver = _f.readline()
            if b'5.4.203-1-tlinux4-0011' not in ver:
                LOGGER.debug('[cpu_offline_condition] ' + \
                             'kernel version is not 5.4.203-1-tlinux4-0011.x')
                return True
        pat = [b'Revert_sched_qos_remove', b'livepatch_0087_tk40011',
               b'livepatch_0088_tk400111', b'livepatch_0089_tk400112']

        with open('/proc/modules', 'rb') as _file:
            lines = _file.readlines()
        modules_group = b' '.join(lines)
        for item in pat:
            if item in modules_group:
                return True

        LOGGER.error('[cpu_offline_condition] Revert_sched_qos_remove, ' + \
                    'livepatch_0087_tk40011' + ' /proc/modules')
        return False

    def cpu_offline_clean(self):
        try:
            with open('/proc/sys/kernel/cpu_qos', 'wb') as _f:
                _f.write(str(self.init_cpu_qos).encode('utf-8'))
            with open(self.cpu_cgroup + '/cpu.offline', 'wb') as _f:
                _f.write(b'0')
        except Exception as exp:
            LOGGER.info('Failed to clean %s/cpu.offline for : %s', self.cpu_cgroup, exp)

        try:
            with open(os.path.join(CGROUP_CPU_PATH, 'cgroup.procs'), 'wb') as _f:
                _f.write(str(self.pid).encode('ascii'))
        except:
            LOGGER.info('Failed to migrate umrd process to cpu root cgroup.')

        if os.path.exists(self.cpu_cgroup):
            with open(self.cpu_cgroup + '/cgroup.procs', 'rb') as _f:
                procs = _f.readlines()
            if len(procs) == 0:
                os.rmdir(self.cpu_cgroup)
            else:
                LOGGER.info('Device is busy. Make sure these processes exit %s', \
                                 str([int(i) for i in procs]))

    def clean(self):
        with open(self.cgtree.conf.umrd_status, 'wb+') as _f:
            s = 'Pid: -\nStatus: Inactive(Dead)\nBootTimestamp: 0 s\n' + \
                'AccumReclaimSimple: 0 KB\n' + \
                'AccumReclaimAnon: 0 KB\nAccumReclaimFile: 0 KB\n' + \
                'LastReclaimTimestamp: 0 s\nLastReclaimCost: 0 s\n'
            _f.write(s.encode('ascii'))

        if not self.cgtree.conf.disable_oversell and self.cgtree.conf.page_reporting_supported == 1:
            self.cgtree.recover_pr_pro()

        if self.cgtree.conf.set_offline and self.cpu_offline_condition():
            self.cpu_offline_clean()
        try:
            with open(os.path.join(CGROUP_V2_ROOT, 'cgroup.procs'), 'wb') as _f:
                _f.write(str(self.pid).encode('ascii'))
        except:
            LOGGER.info('Failed to migrate umrd process to root cgroup.')
        with open(os.path.join(self.mem_cgroup, 'cgroup.procs'), 'rb') as _f:
            procs = _f.readlines()
        if len(procs) == 0:
            os.rmdir(self.mem_cgroup)
        else:
            LOGGER.info('Device is busy. Make sure these processes exit %s', \
                             str([int(i) for i in procs]))

        if self.cgtree.conf.profile and self.profile is not None:
            self.profile.disable()
            s_io = io.StringIO()
            sortby = 'cumtime'
            stat = pstats.Stats(self.profile, stream=s_io).sort_stats(sortby)
            stat.print_stats()
            profile_path = os.path.join(self.cgtree.conf.output_dir, 'profile')
            with open(profile_path, 'wb') as _f:
                _f.write(s_io.getvalue().encode('ascii'))

    def percgroup_normalize_and_get_monitored(self, compr_ratio: float):
        """
        Normalize cgroup's file save from up to bottom.
        """
        res = []
        for _cg in self.cgtree.roots.values():
            child_res = _cg.get_normalized_file_save(1, compr_ratio)
            res.extend(child_res)
        return res

    def check_feasibility(self):
        # During the first 10 seconds, umrd will not reclaim for the delta_total is
        # a large value (current psi some total). Return True directly when detect
        # that the memcg_total_history[0] is zero.
        if self.memcg_total_history[0] == 0:
            return True
        with open(os.path.join(self.mem_cgroup, 'memory.pressure'), 'rb') as psi:
            some = psi.readline().replace(b'=', b' ').split()
            some_total = float(some[8])
            delta_total = some_total - self.memcg_total_history[0]
        self.memcg_total_history.append(some_total)

        return delta_total < self.cgtree.conf.total_threshold

    def loop(self):
        mode = self.cgtree.conf.mode
        loop_count = 0

        # each cgroup has its own last_reclaim_time. this var is for the whole.
        last_report_time = 0

        reclaim_interval = []
        if self.cgtree.conf.reclaim_mode == "simple":
            reclaim_interval = [self.cgtree.conf.interval]
        elif self.cgtree.conf.reclaim_mode.startswith("emm"):
            reclaim_interval = [self.cgtree.conf.interval_anon,
                              self.cgtree.conf.interval_file]
        else:
            raise ValueError("Invalid conf mode %s" % (self.cgtree.conf.reclaim_mode))
        self.cgtree.conf.total_threshold = sum(reclaim_interval) / len(reclaim_interval) * 300000

        self.reclaim_stat.clear()

        report_only_flag = False

        with open(self.cgtree.conf.umrd_status, 'wb+') as _f:
            try:
                s = 'Pid: %d\nStatus: Active(Running)\nBootTimestamp: %d s\n' + \
                    'AccumReclaimSimple: 0 KB\n' + \
                    'AccumReclaimAnon: 0 KB\nAccumReclaimFile: 0 KB\n' + \
                    'LastReclaimTimestamp: 0 s\nLastReclaimCost: 0 s\n'
                s = s % (self.pid, self.cgtree.conf.boot_timestamp)
            except:
                s = 'Pid: -\nStatus: Active(Error)\nBootTimestamp: 0 s\n' + \
                    'AccumReclaimSimple: 0 KB\n' + \
                    'AccumReclaimAnon: 0 KB\nAccumReclaimFile: 0 KB\n' + \
                    'LastReclaimTimestamp: 0 s\nLastReclaimCost: 0 s\n'
            _f.write(s.encode('ascii'))

        while True:
            loop_count += 1
            start_time = get_curr_time()

            if detect_report_only(self.cgtree.conf.output_dir):
                if not report_only_flag:
                    LOGGER.info('enable report_only')
                    report_only_flag = True
                self.report_statistics_only()
                time.sleep(1)
                continue
            report_only_flag = False

            LOGGER.info('================  loop %d start  ================', loop_count)
            # reclaim
            util = self.run(mode, loop_count)

            reclaimed = self.reclaim_stat.total()
            reclaimed_anon = self.reclaim_stat.reclaimed_anon
            reclaimed_file = self.reclaim_stat.reclaimed_file
            self.total_reclaim_stat += self.reclaim_stat
            self.reclaim_stat.clear()

            curr_time = get_curr_time()
            cost_time = curr_time - start_time
            if util == HIGH_UTIL:
                LOGGER.info('skip for HIGH_UTIL.')
                LOGGER.info('cost: %f second', cost_time)
                time.sleep(max(0, self.cycle_sleep - cost_time))
                continue

            # write global data
            if curr_time - last_report_time > self.cgtree.conf.report_interval:
                last_report_time = curr_time
                self.report_statistics(reclaimed)

            LOGGER.info('total reclaimed: %sK', reclaimed)
            LOGGER.info('  anon=%sK file=%sk', reclaimed_anon, reclaimed_file)
            LOGGER.info('cost: %f second', cost_time)

            if self.cgtree.conf.reclaim_mode == 'simple':
                self.cgtree.accum_reclaim_simple += reclaimed
            elif self.cgtree.conf.reclaim_mode.startswith('emm'):
                self.cgtree.accum_reclaim_anon += reclaimed_anon
                self.cgtree.accum_reclaim_file += reclaimed_file

            if reclaimed > 0:
                with open(self.cgtree.conf.umrd_status, 'wb+') as _f:
                    try:
                        s = 'Pid: %d\nStatus: Active(Running)\nBootTimestamp: %d s\n' + \
                            'AccumReclaimSimple: %d KB\n' + \
                            'AccumReclaimAnon: %d KB\nAccumReclaimFile: %d KB\n' + \
                            'LastReclaimTimestamp: %d s\nLastReclaimCost: %.3f s\n'
                        s = s % (self.pid, self.cgtree.conf.boot_timestamp, \
                                self.cgtree.accum_reclaim_simple, \
                                self.cgtree.accum_reclaim_anon, \
                                self.cgtree.accum_reclaim_file, \
                                curr_time, cost_time)
                    except:
                        s = 'Pid: -\nStatus: Active(Error)\nBootTimestamp: 0 s\n' + \
                            'AccumReclaimSimple: 0 KB\n' + \
                            'AccumReclaimAnon: 0 KB\nAccumReclaimFile: 0 KB\n' + \
                            'LastReclaimTimestamp: 0 s\nLastReclaimCost: 0 s\n'
                    _f.write(s.encode('ascii'))

            time.sleep(max(0, self.cycle_sleep - cost_time))

    def oversell_psi_check(self):
        io_some_avg10 = get_global_pressure_some_avg10('io')
        mem_some_total = get_global_pressure_some_total('memory')
        self.global_mem_total_history.append(mem_some_total)
        if self.global_mem_total_history[0] == 0:
            mem_sometotal_delta = 0
        else:
            mem_sometotal_delta = self.global_mem_total_history[1] - \
             self.global_mem_total_history[0]

        if mem_sometotal_delta < 6000 and io_some_avg10 < 5:
            oversell_pro_status = OVERSELL_PROHIGH
        elif mem_sometotal_delta < 10000 and io_some_avg10 < 30:
            oversell_pro_status = OVERSELL_PRONORM
        else:
            oversell_pro_status = OVERSELL_PROLOW

        return oversell_pro_status, io_some_avg10, mem_some_total, mem_sometotal_delta

    def get_meminfo_for_pagereport(self):
        if os.path.exists('/proc/special_meminfo'):
            p_meminfo = parse_textinfo('/proc/special_meminfo')
            active_file = int(p_meminfo[b'SpecialActive(file):'])
            inactive_file = int(p_meminfo[b'SpecialInactive(file):'])
            dirty = int(p_meminfo[b'SpecialDirty:'])
            totalmem = int(p_meminfo[b'SpecialMemTotal:'])
        else:
            p_meminfo = parse_textinfo('/proc/meminfo')
            active_file = int(p_meminfo[b'Active(file):'])
            inactive_file = int(p_meminfo[b'Inactive(file):'])
            dirty = int(p_meminfo[b'Dirty:'])
            totalmem = int(p_meminfo[b'MemTotal:'])

        avail_reclaim = active_file + inactive_file - dirty
        oversell_cache_ratio = (active_file + inactive_file) * 100 / totalmem

        return oversell_cache_ratio, avail_reclaim, inactive_file


    def run(self, mode, loop_count) -> int:
        self.cgtree.try_update_rules()
        self.cgtree.try_refresh(mode)

        io_some_avg10 = None

        if not self.cgtree.conf.disable_oversell and self.cgtree.conf.page_reporting_supported == 1:
            oversell_pro_status, io_some_avg10, mem_some_total, mem_sometotal_delta = self.oversell_psi_check()
            oversell_cache_ratio, avail_reclaim, immed_reclaim = self.get_meminfo_for_pagereport()

            LOGGER.info('Oversell res: avail=%.2f, immed=%.2f, cache_ratio=%.2f',
                        avail_reclaim, immed_reclaim, oversell_cache_ratio)
            LOGGER.info('Oversell psi: io_some_avg10=%.2f, mem_some_total=%.2f, mem_sometotal_delta=%.2f',
                        io_some_avg10, mem_some_total, mem_sometotal_delta)

            if oversell_pro_status == OVERSELL_PROHIGH:
                self.cgtree.set_proactive(self.cgtree.conf.proactive_high)
                self.cgtree.set_pagereport_enable(1)
            elif oversell_pro_status == OVERSELL_PRONORM:
                self.cgtree.set_proactive(self.cgtree.conf.proactive_norm)
                self.cgtree.set_pagereport_enable(1)
            elif oversell_pro_status == OVERSELL_PROLOW:
                self.cgtree.set_pagereport_enable(0)
                self.cgtree.set_proactive(self.cgtree.conf.proactive_low)
                return LOW_UTIL

        if io_some_avg10 is None:
            io_some_avg10 = get_global_pressure_some_avg10('io')

        if io_some_avg10 >= 95:
            LOGGER.info('Global io pressure is too high (%s), skipping reclaim cycle.',
                         io_some_avg10)
            return LOW_UTIL

        if self.cgtree.conf.cpu_util_threshold != sys.maxsize:
            cpu_used, cpu_total = get_cpu_util()
            if cpu_used == -1 or cpu_total == -1:
                return LOW_UTIL
            if self.last_cpu_used == 0 or  self.last_cpu_total == 0:
                self.last_cpu_used = cpu_used
                self.last_cpu_total = cpu_total
                return LOW_UTIL
            if cpu_total == self.last_cpu_total:
                self.last_cpu_used = cpu_used
                return LOW_UTIL
            cpu_util = (cpu_used - self.last_cpu_used) * 100 / (cpu_total - self.last_cpu_total)
            self.last_cpu_used = cpu_used
            self.last_cpu_total = cpu_total
            if cpu_util >= self.cgtree.conf.cpu_util_threshold:
                LOGGER.info('System CPU Util is too high (%s).', str(cpu_util))
                return HIGH_UTIL

        if not self.check_feasibility():
            LOGGER.info('UMRD process pressure is too hign, skipping reclaim cycle.')
            return LOW_UTIL

        try:
            self.cgtree.try_reclaim(self.reclaim_stat)
        except Exception as exp:
            LOGGER.info('Skip reclaim: %s', exp)
        return LOW_UTIL

    def report_statistics_only(self):
        # Parse global meminfo which is in KBs
        meminfo_raw = parse_textinfo('/proc/meminfo')
        mem_total, swap_total, mem_free = (
            int(meminfo_raw[b'MemTotal:']) * 1024,
            int(meminfo_raw[b'SwapTotal:']) * 1024,
            int(meminfo_raw[b'MemFree:']) * 1024)
        mem_used = mem_total - mem_free

        zram_orig, zram_compr = get_zram()
        if zram_orig is None or zram_compr is None:
            compr_ratio = 1
            zram_orig = 0
            zram_compr = 0
        elif zram_orig == 0:
            compr_ratio = 0
        else:
            compr_ratio = zram_compr / zram_orig

        mem_limit, memsw_usage, anon_save, file_save = 0, 0, 0, 0
        for memcg in self.cgtree.roots.values():
            (cg_mem_limit, cg_msw_usage,
                cg_anon_save, cg_file_save) = memcg.get_memsaving_recursive(compr_ratio)
            mem_limit += cg_mem_limit
            memsw_usage += cg_msw_usage
            anon_save += cg_anon_save
            file_save += cg_file_save
        mem_limit = min(mem_limit, mem_total) or mem_total

        max_memusage = file_save + memsw_usage
        save_ratio = (anon_save + file_save) / (max_memusage or 1) * 100

        # write global_memsave
        with open(self.cgtree.conf.global_memsave, 'w', encoding='ascii') as _f:
            _f.write('total mem: %d kb\n' % (mem_total / 1024))
            _f.write('total swap: %d kb\n' % (swap_total / 1024))
            _f.write('anon save: %d bytes\n' % anon_save)
            _f.write('file save: %d bytes\n' % file_save)
            _f.write('max memusage: %d bytes\n' % max_memusage)
            _f.write('save ratio: %.2f %%\n' % save_ratio)
            _f.write('savepage limit: %d bytes\n' % mem_limit)
            _f.write('totalused memory: %d bytes\n' % mem_used)

    def report_statistics(self, reclaimed):
        # Parse global meminfo which is in KBs
        meminfo_raw = parse_textinfo('/proc/meminfo')
        mem_total, swap_total, mem_free = (
            int(meminfo_raw[b'MemTotal:']) * 1024,
            int(meminfo_raw[b'SwapTotal:']) * 1024,
            int(meminfo_raw[b'MemFree:']) * 1024)
        mem_used = mem_total - mem_free

        # Calc sum of memory limit of all reclaiming cgroups

        zram_orig, zram_compr = get_zram()
        if zram_orig is None or zram_compr is None:
            LOGGER.info("ZRAM not enabled")
            # TODO: We assume a compr_ratio = 1 here, to simulate that if zram
            # not used swap should goto a block device, ugly and need to be fixed.
            compr_ratio = 1
            zram_orig = 0
            zram_compr = 0
        elif zram_orig == 0:
            compr_ratio = 0
        else:
            compr_ratio = zram_compr / zram_orig

        mem_limit, memsw_usage, anon_save, file_save = 0, 0, 0, 0
        for memcg in self.cgtree.roots.values():
            # Read cgroups memory.stat
            (cg_mem_limit, cg_msw_usage,
                cg_anon_save, cg_file_save) = memcg.get_memsaving_recursive(compr_ratio)
            mem_limit += cg_mem_limit
            memsw_usage += cg_msw_usage
            anon_save += cg_anon_save
            file_save += cg_file_save
        mem_limit = min(mem_limit, mem_total) or mem_total

        # For root reclaim mode, use global available memory as memory limit
        if self.cgtree.conf.mode == 1:
            mem_limit = mem_total

        # TODO: If other swap device is in use, this will always return 0.
        global_anon_save = zram_orig - zram_compr

        file_save_limit = mem_limit - global_anon_save
        if file_save > file_save_limit:
            LOGGER.debug('[DEBUG] file_save(%s) larger than file_save_limit(%s)',
                            file_save, file_save_limit)
        file_save = max(0, min(file_save, file_save_limit))

        # max_memusage is the estimation of memory used before umrd reclaiming
        # It should be equal to `anon_save + file_save + memory_usage + zram_compr`
        # anon_save + memory_usage + zram_compr == memory_usage + zram_orig = memsw_usage
        # memsw_usage is the sum of cgtree.root cgroups' memsw_usage
        max_memusage = file_save + memsw_usage

        # calculate global file save
        file_save_avg = 0
        if self.cgtree.conf.has_cgroup_zram_stat:
            if self.cgtree.conf.use_emm_zram and cg_has_interface(CGROUP_V2_ROOT, 'memory.emm.workingset'):
                ret = parse_textinfo(os.path.join(CGROUP_V2_ROOT, 'memory.emm.workingset'))
            else:
                ret = cg_memory_stat(CGROUP_V2_ROOT)
            val = int(ret.get(b'workingset_refault_distance_avg_10m', 0)) + \
                    int(ret.get(b'workingset_valid_eviction_avg_10m', 0))
            file_save_avg = val * 4096 / 2

        save_ratio = (anon_save + file_save) / (max_memusage or 1) * 100

        # write global_memsave
        with open(self.cgtree.conf.global_memsave, 'w', encoding='ascii') as _f:
            _f.write('total mem: %d kb\n' % (mem_total / 1024))
            _f.write('total swap: %d kb\n' % (swap_total / 1024))
            _f.write('anon save: %d bytes\n' % anon_save)
            _f.write('file save: %d bytes\n' % file_save)
            _f.write('max memusage: %d bytes\n' % max_memusage)
            _f.write('save ratio: %.2f %%\n' % save_ratio)

            _f.write('savepage limit: %d bytes\n' % mem_limit)
            _f.write('totalused memory: %d bytes\n' % mem_used)
            _f.write('[for debug] filesave allavg: %d bytes\n' % file_save_avg)

        if self.cgtree.conf.debug:
            # Only report when reclaimed pages is non-zero
            if reclaimed > 0:
                LOGGER.debug('mem_total %d kb, swap_total %d kb, '
                                'mem_free %d kb, mem_limit %d kb',
                                mem_total / 1024, swap_total / 1024,
                                mem_free / 1024, mem_limit / 1024)
                LOGGER.debug('memsw_usage %d bytes, max_memusage %d bytes',
                                memsw_usage, max_memusage)
                LOGGER.debug('global_anon_save %d bytes, anon_save %d bytes, '
                                'file_save %d bytes save_ratio %.2f %%',
                                global_anon_save, anon_save, file_save, save_ratio)

            # write per cgroup data
            saved = self.percgroup_normalize_and_get_monitored(compr_ratio)
            if not os.path.exists(self.cgtree.conf.percgroup_memsave):
                with open(self.cgtree.conf.percgroup_memsave, 'wb'):
                    pass
            with open(self.cgtree.conf.percgroup_memsave, 'wb') as _f:
                res = ''
                for i in saved:
                    res += '%s anon_save=%d file_save=%d save_ratio=%.4f pct_usage=%.4f\n' % (
                        i[0], i[1], i[2], i[3], i[4])
                _f.write(res.encode('ascii'))

            # write reclaimed_in_last_period
            roots = self.cgtree.roots
            monitored_list = self.cgtree.debug_show_cgroups(roots)
            with open(self.cgtree.conf.monitored_cgroups, 'wb+') as _f:
                _f.write('\n'.join(monitored_list).encode('ascii'))

            with open(self.cgtree.conf.reclaimed_in_last_period, 'wb') as _f:
                _f.write(str(reclaimed).encode('ascii'))
