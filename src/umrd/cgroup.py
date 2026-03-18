import os
import sys
import abc
import time
import collections
from typing import List

from .util import ALLOWED, ANON_ONLY, FILE_ONLY, PAGESIZE, MAXMEMLIMIT
from .util import LOGGER, RuleItem, parse_textinfo, ReclaimParams, ReclaimStat
from .util import get_totalram_pages, get_zram, get_curr_time
from .util import cg_memory_current, cg_memory_max, cg_memory_stat
from .util import cg_has_interface, cg_write_value, cg_try_reclaim
from .util import cg_set_zram_priority, cg_get_zram_stat, cg_has_emm, cg_set_swappiness


def create_cgroup(tree, path: str, rule: RuleItem, params: ReclaimParams, is_cgroot: bool):
    if rule is None:
        return NegativeCgroup(path, rule)
    if params is None:
        return BasicCgroup(tree, path, rule, params, is_cgroot)
    if params.reclaim_mode == "simple":
        return SimpleCgroup(tree, path, rule, params, is_cgroot)
    if params.reclaim_mode == "emm":
        if tree.lru_gen == 0:
            LOGGER.info('Fallback from emm to simple for mglru disabled.')
            return SimpleCgroup(tree, path, rule, params, is_cgroot)
        return EMMCgroup(tree, path, rule, params, is_cgroot)

    LOGGER.critical("Unsupport relcaim mode %s", params.reclaim_mode)
    return None


class CgroupStat:
    """
    Wrapper for stat on a single cgroup
    """
    __slots__ = ('path', 'compr_ratio', 'current', 'memsw_usage', 'memtotal', 'memfree',
                 'total_active_anon', 'total_inactive_anon', 'total_active_file', 'total_inactive_file',
                 'active_anon', 'inactive_anon', 'active_file', 'inactive_file', 'cur_total_lru',
                 'emm_anon_cold', 'emm_anon_total', 'emm_file_cold', 'emm_file_total',
                 'anon_save', 'file_save', 'swapout', 'is_cgroot'
                 )
    def __init__(self, path: str, is_cgroot: bool):
        self.path = path
        self.memtotal = 0
        self.memfree = 0
        self.total_active_anon = 0
        self.total_inactive_anon = 0
        self.total_active_file = 0
        self.total_inactive_file = 0
        self.active_anon = 0
        self.inactive_anon = 0
        self.active_file = 0
        self.inactive_file = 0
        self.cur_total_lru = 0
        self.compr_ratio = 0
        self.anon_save = 0
        self.file_save = 0
        self.emm_anon_cold = 0
        self.emm_anon_total = 0
        self.emm_file_cold = 0
        self.emm_file_total= 0

        self.is_cgroot = is_cgroot

    def update_stat(self):
        try:
            memtotal = cg_memory_max(self.path)
            if memtotal == MAXMEMLIMIT:
                self.memtotal = get_totalram_pages()
            else:
                self.memtotal = memtotal
            self.current = cg_memory_current(self.path)
            self.memfree = self.memtotal - self.current
        except:
            pass

        ret = cg_memory_stat(self.path)
        try:
            self.total_active_anon = int(ret.get(b'active_anon', 0))
            self.total_inactive_anon = int(ret.get(b'inactive_anon', 0))
            self.total_active_file = int(ret.get(b'active_file', 0))
            self.total_inactive_file = int(ret.get(b'inactive_file', 0))
            self.active_anon = self.total_active_anon
            self.inactive_anon = self.total_inactive_anon
            self.active_file = self.total_active_file
            self.inactive_file = self.total_inactive_file
            self.cur_total_lru = self.active_anon + self.inactive_anon + \
                                    self.active_file + self.inactive_file
        except:
            pass

        return ret

    def update_lru_gen(self):
        lru_gen_path = os.path.join(self.path, 'memory.emm.lru_gen')
        if not os.path.exists(lru_gen_path):
            self.emm_anon_cold = self.total_inactive_anon
            self.emm_anon_total = self.total_inactive_anon + self.total_active_anon
            self.emm_file_cold = self.total_inactive_file
            self.emm_file_total = self.total_inactive_file + self.total_active_file
            return

        total_anon = 0
        total_file = 0
        total_anon_cold = 0
        total_file_cold = 0
        try:
            with open(lru_gen_path, 'rb') as _f:
                list_mglru = _f.readlines()
                min_seq_idx_list = []
                len_list_mglru = len(list_mglru)
                for index in range(len_list_mglru):
                    str_line = list_mglru[index].strip()
                    if str_line.find(b'node') == 0:
                        min_seq_idx_list.append(index + 1)
                cnt_node = len(min_seq_idx_list)
                min_seq_idx_list.append(len_list_mglru + 1)

                for index in range(cnt_node):
                    curr_min_gen_idx = min_seq_idx_list[index]
                    max_seq_idx = min_seq_idx_list[index + 1] - 2
                    if max_seq_idx - curr_min_gen_idx <= 1:
                        continue
                    anon_cold = 0
                    file_cold = 0
                    for i in range(curr_min_gen_idx, max_seq_idx-1):
                        str_line = list_mglru[i].strip().split()
                        if anon_cold == 0:
                            anon_cold = int(str_line[-2])
                        if file_cold == 0:
                            file_cold = int(str_line[-1])
                    total_anon_cold += anon_cold
                    total_file_cold += file_cold

                for line in list_mglru:
                    str_line = line.strip()
                    if str_line.find(b'node') < 0:
                        str_line = str_line.split()
                        total_anon += int(str_line[-2])
                        total_file += int(str_line[-1])
        except:
            self.emm_anon_cold = self.total_inactive_anon
            self.emm_anon_total = self.total_inactive_anon + self.total_active_anon
            self.emm_file_cold = self.total_inactive_file
            self.emm_file_total = self.total_inactive_file + self.total_active_file
            return

        self.emm_anon_cold = total_anon_cold * 4096
        self.emm_anon_total = total_anon * 4096
        self.emm_file_cold = total_file_cold * 4096
        self.emm_file_total = total_file * 4096

    def update_compr_ratio(self, root_compr_ratio: float):
        self.compr_ratio = root_compr_ratio

    def update_usage(self):
        swap_current = 0
        try:
            if not self.is_cgroot:
                self.current = cg_memory_current(self.path)
            else:
                ret = parse_textinfo('/proc/meminfo')
                self.current = int(ret[b'MemTotal:']) * 1024 - int(ret[b'MemFree:']) * 1024

            try:
                if cg_has_interface(self.path, 'memory.swap.current'):
                    with open(os.path.join(self.path, 'memory.swap.current'), 'r') as f:
                        swap_current = int(f.read().strip())
            except:
                pass

            self.memsw_usage = self.current + swap_current

        except Exception:
            self.current = 0
            self.memsw_usage = 0
            LOGGER.info('%s CgroupStat update_usage failed', self.path)

        self.swapout = int(max(0, swap_current) / ((1 - self.compr_ratio) or 1))

    def update_anon_save(self):
        self.anon_save = max(0, self.memsw_usage - self.current)

    def update_file_save(self, ret):
        sub_usage_in_bytes = max(0, self.memsw_usage - self.current)
        sub_usage_in_bytes /= ((1 - self.compr_ratio) or 1)

        # If both active_file and inactive_file are 0, predicting file_save is impossible
        if (self.anon_save == 0) or (self.total_active_file == 0 and self.total_inactive_file == 0):
            self.file_save = 0
            return

        in_mem = self.memtotal - self.memfree
        max_file_save = max(0, self.memtotal - self.current - self.anon_save)
        file_anon_ratio = 3
        # if anon_save / (anon_save + in_mem) > 0.6, control the file save
        if self.anon_save > 1.5 * in_mem:
            file_anon_ratio = 1

        # Assume that file_save is less than three times of anon_save
        active_file_save = self.total_active_file / (self.total_active_anon or 1000) * sub_usage_in_bytes
        active_file_save = min(active_file_save, self.anon_save * file_anon_ratio)

        # We calculate three types of file_save, using inactive/active/total items.
        # The x is less than x_upper below. We estimate file save using x as lower,
        #   x_upper as upper bound.
        inactive_file_save = self.total_inactive_file / \
                    ((self.total_inactive_anon + sub_usage_in_bytes) or 1000) * sub_usage_in_bytes
        inactive_file_save = min(inactive_file_save, self.anon_save * file_anon_ratio)

        inactive_file_save_upper = self.total_inactive_file / \
                                (self.total_inactive_anon or 1000) * sub_usage_in_bytes

        total_file_save = (self.total_active_file + self.total_inactive_file) / \
                        ((self.total_active_anon + self.total_inactive_anon + sub_usage_in_bytes) \
                        or 1000) * sub_usage_in_bytes
        total_file_save = min(total_file_save, self.anon_save * file_anon_ratio)

        total_file_save_upper = (self.total_active_file + self.total_inactive_file) / \
                        ((self.total_active_anon + self.total_inactive_anon) or 1000) * \
                        sub_usage_in_bytes

        set_1 = list(filter(lambda x: x != 0, [active_file_save, inactive_file_save, \
                                                        total_file_save]))
        set_2 = list(filter(lambda x: x != 0, [active_file_save, inactive_file_save_upper, \
                                                        total_file_save_upper]))
        if (len(set_1) == 0 or len(set_2) == 0):
            self.file_save = 0
            return

        len_set_1, len_set_2 = len(set_1), len(set_2)

        file_save_avg = sum(set_1) / len_set_1
        file_save_avg_upper = sum(set_2) / len_set_2

        file_save_mid = sorted(set_1)[int(len_set_1 >> 1)]
        file_save_mid_upper = sorted(set_2)[int(len_set_2 >> 1)]

        if self.anon_save > in_mem:
            self.file_save = max(int(file_save_avg), int(file_save_mid))
            self.file_save = min(max_file_save, self.file_save)
            return

        # choose avg or mid according to which is close to the refault rate
        anon_cnt = self.total_inactive_anon
        file_cnt = self.total_inactive_file

        # Condition: para4 > 1/para2 and para4 < para3
        # Using anon_cnt and file_cnt to classify the business into different types.
        # If anon_cnt is larger, we estimate a smaller file_save.
        para1, para2, para3, para4 = 20, 0.2, 15, 10
        if anon_cnt >= int(para1 * file_cnt):
            self.file_save = min(int(file_save_avg), int(file_save_mid))
        elif file_cnt >= int(para2 * anon_cnt):
            self.file_save = max(int(file_save_avg_upper), int(file_save_mid_upper))
        elif anon_cnt >= int(para3 * file_cnt):
            left = max(int(file_save_avg), int(file_save_mid))
            right = min(int(file_save_avg), int(file_save_mid))
            self.file_save = int(left + (right - left) / ((para1 - para3) * file_cnt))
        elif anon_cnt >= int(para4 * file_cnt):
            self.file_save = max(int(file_save_avg), int(file_save_mid))
        elif file_cnt >= int(1/para4 * anon_cnt):
            left = max(int(file_save_avg_upper), int(file_save_mid_upper))
            right = max(int(file_save_avg), int(file_save_mid))
            self.file_save = int(left + (right - left) / ((para4 - 1/para2) * file_cnt))
        # Remove memfree limit. If the cgroup scaled down with memfree lower, which
        # makes file_save biased
        #   self.file_save = max(0, min(file_save, self.memfree - self.anon_save))
        self.file_save = min(max_file_save, self.file_save)

    def cal_pct_usage(self):
        if self.memtotal == 0:
            pct_usage = 0
        elif not self.is_cgroot:
            pct_usage = self.memsw_usage / self.memtotal
        else:
            pct_usage = self.current / self.memtotal
        pct_usage = max(0, min(pct_usage, 1))
        return pct_usage

class CgroupZramStat(CgroupStat):
    __slots__ = ('zram_raw_in_bytes', 'zram_usage_in_bytes')
    def update_usage(self):
        super().update_usage()

        self.zram_raw_in_bytes = 0
        self.zram_usage_in_bytes = 0
        if self.is_cgroot:
            return

        try:
            zram_stat = cg_get_zram_stat(self.path)
            self.zram_raw_in_bytes = zram_stat.get('raw', 0)
            self.zram_usage_in_bytes = zram_stat.get('usage', 0)
        except:
            LOGGER.info('%s CgroupZramStat update_usage failed', self.path)

    def update_compr_ratio(self, _root_compr_ratio: float):
        if self.zram_raw_in_bytes == 0:
            self.compr_ratio = 0
            self.zram_usage_in_bytes = 0
        else:
            self.compr_ratio = self.zram_usage_in_bytes / self.zram_raw_in_bytes

    def update_anon_save(self):
        self.anon_save = max(0, self.zram_raw_in_bytes - self.zram_usage_in_bytes)

    def update_file_save(self, ret):
        max_file_save = max(0, self.memtotal - self.current - self.anon_save)
        val = 0
        try:
            ws_refault_distance = int(ret[b'workingset_refault_distance_avg_10m']) * 4096
            ws_valid_eviction = int(ret[b'workingset_valid_eviction_avg_10m']) * 4096
            ws_refault_distance = min(ws_refault_distance, self.memtotal)
            val = (ws_refault_distance + ws_valid_eviction) / 2
            self.file_save = min(val, max_file_save)
        except:
            super().update_file_save(ret)

class CGroup(abc.ABC):
    __slots__ = ('path', 'rule', 'params', 'tree',
                 'children', 'total_history', 'cgstat',
                 'force_reclaim')

    @abc.abstractmethod
    def refresh(self, level: int):
        """
        Read cgroup statistic from system and refresh children layout.
        """

    @abc.abstractmethod
    def reclaim_recursive(self, reclaim_control: ReclaimStat):
        """
        Reclaim this and children cgroup, this should also take care of
        memory.stat and PSI update, since these params are usually only used
        for reclaming, binging it here should be reasonable.
        """

    @abc.abstractmethod
    def get_memsaving_recursive(self, compr_ratio: float):
        """
        Get the calculated memory saving value, recursively,
        return mem_total, memsw_usage, anon_save, file_save
        """


class NegativeCgroup(CGroup):
    """
    Blocked Cgroup, just a struct placeholder
    """
    def __init__(self, path: str, rule: RuleItem):
        self.rule = rule

    def refresh(self, level):
        # No refresh for NegativeCgroup
        pass

    def set_zram_priority(self, zram_priority):
        # No set zram priority for NegativeCgroup
        pass

    def reclaim_recursive(self, reclaim_control: ReclaimStat):
        return

    def get_memsaving_recursive(self, compr_ratio):
        return 0, 0, 0, 0

    def get_normalized_file_save(self, file_save_ratio, compr_ratio):
        return []


class BasicCgroup(CGroup):
    """
    Wrapper for operations on a single cgroup
    """
    def __init__(self, tree, path: str, rule: RuleItem, params: ReclaimParams, is_cgroot: bool) -> None:
        self.tree = tree
        self.path = path
        self.rule = rule
        self.params = params
        self.total_history = collections.deque([0] * 2, maxlen=2)

        self.children = {}

        LOGGER.debug('[Add cgroup] %s %s has_cgroup_zram_stat=%s', self.__class__.__name__, \
                     path, tree.conf.has_cgroup_zram_stat)
        if tree.conf.has_cgroup_zram_stat:
            self.cgstat = CgroupZramStat(path, is_cgroot)
        else:
            self.cgstat = CgroupStat(path, is_cgroot)

        self.force_reclaim = False

    def set_swappiness(self, swappiness: int):
        self.swappiness = swappiness
        cg_set_swappiness(self.path, swappiness)

    def set_zram_priority(self, zram_priority: int):
        if not self.tree.conf.has_cgroup_zram_stat:
            return
        if zram_priority == 0:
            return
        if zram_priority < -4 or zram_priority > 4:
            LOGGER.info('%s invalid zram_priority %s', self.path, zram_priority)
            return
        # cgroup v2: use helper function to check and write
        if cg_set_zram_priority(self.path, zram_priority):
            LOGGER.debug('set_zram_priority=%s %s', zram_priority, self.path)
        if zram_priority < 0:
            return
        for child in self.children.values():
            child.set_zram_priority(zram_priority)

    def get_memsaving_recursive(self, compr_ratio: float):
        mem_limit = 0
        msw_usage = 0
        anon_save = 0
        file_save = 0
        for child in self.children.values():
            child_mem_limit, child_msw_usage, child_anon_save, child_file_save = (
                child.get_memsaving_recursive(compr_ratio))
            mem_limit += child_mem_limit
            msw_usage += child_msw_usage
            anon_save += child_anon_save
            file_save += child_file_save

        return mem_limit, msw_usage, anon_save, file_save

    def get_normalized_file_save(self, file_save_ratio: float, compr_ratio: float):
        """
        Normalize cgroup's file save and return a list of saving details

        Args:
            file_save_ratio: Ratio to normalize current cgroup's file save. It comes from parent.
            compr_ratio: Global zram compressed ratio.

        Returns:
            A list of (cg.path, cg.anon_save, cg.file_save, save_ratio).
            The list contains current cgroup's saving detail and its children's.
        """
        res = []

        if self.rule.type == ALLOWED:
            self.cgstat.file_save = int(self.cgstat.file_save * file_save_ratio)
            total_orig_mem = self.cgstat.current + self.cgstat.anon_save + self.cgstat.file_save
            if not self.tree.conf.has_cgroup_zram_stat and not self.cgstat.is_cgroot:
                cg_zram_compr = self.cgstat.memsw_usage - self.cgstat.current
                total_orig_mem += cg_zram_compr
            save_ratio = (self.cgstat.anon_save + self.cgstat.file_save) / (total_orig_mem or 1)
            pct_usage = self.cgstat.cal_pct_usage()
            res.append((self.path, self.cgstat.anon_save, self.cgstat.file_save, \
                        save_ratio, pct_usage))

            sum_child_file_save = 0
            for child in self.children.values():
                if child.rule is not None and child.rule.type == ALLOWED:
                    sum_child_file_save += child.cgstat.file_save
            if sum_child_file_save != 0:
                ratio = self.cgstat.file_save / sum_child_file_save
            else:
                # The value of ratio doesn't matter.
                ratio = file_save_ratio
        else:
            ratio = file_save_ratio

        for child in self.children.values():
            child_res = child.get_normalized_file_save(ratio, compr_ratio)
            res.extend(child_res)

        return res

    # BasicCgroup don't do reclaiming nor statistic
    def refresh_statistic(self, curr_time):
        pass

    def refresh(self, level: int):
        """
        Refresh children and other status
        Raise FileNotFound exception if CG is dead before/during refresh
        """
        if level > 0 or level <= -2:
            prev_children = self.children
            self.children = {}
            level -= 1

            # This block may raise FileNotFound
            for entry in os.scandir(self.path):
                if entry.is_dir():
                    path = entry.path
                    cgroup = prev_children.get(path)
                    rule = self.tree.get_path_rule(path)
                    if cgroup and cgroup.rule == rule:
                        if rule is not None and rule.params is not None and \
                            rule.params.zram_priority is not None and \
                            issubclass(type(cgroup), SimpleCgroup) and \
                            cgroup.zram_priority != rule.params.zram_priority:
                            cgroup.zram_priority = rule.params.zram_priority
                            cgroup.set_zram_priority(rule.params.zram_priority)
                        self.children[path] = cgroup
                    else:
                        self.children[path] = create_cgroup(
                            self.tree, path, rule, rule.params if rule else None, False)

            for child in self.children.values():
                try:
                    child.refresh(level)
                except FileNotFoundError:
                    # Dead child will be cleared in next refresh iteration
                    # by the os.scandir iter above
                    pass
                except Exception as exp:
                    LOGGER.info('%s refresh failed for: %s', self.path, exp)

    # BasicCgroup is for SCAN_ONLY, simply reclaim child cgroup
    def reclaim_recursive(self, reclaim_control: ReclaimStat):
        for _cg in self.children.values():
            _cg.reclaim_recursive(reclaim_control)


class SimpleCgroup(BasicCgroup):
    """
    Wrapper for operations on a single cgroup
    """
    # We may override swappiness according to save rate
    __slots__ = ('swappiness', 'zram_priority',
                 'last_scan_time', 'last_reclaimed_time',
                 'integral', )

    def __init__(self, tree, path: str, rule: RuleItem, params: ReclaimParams, is_cgroot: bool):
        super().__init__(tree, path, rule, params, is_cgroot)
        self.integral = 0
        self.last_scan_time = 0
        self.last_reclaimed_time = 0
        self.set_swappiness(params.swappiness)
        self.zram_priority = params.zram_priority
        self.set_zram_priority(params.zram_priority)

        self.cgstat.update_usage()

    def get_memsaving_recursive(self, compr_ratio: float):
        self.cgstat.update_usage()
        stat = self.cgstat.update_stat()
        self.cgstat.update_compr_ratio(compr_ratio)
        self.cgstat.update_anon_save()
        self.cgstat.update_file_save(stat)

        mem_limit = self.cgstat.memtotal
        msw_usage = self.cgstat.memsw_usage
        anon_save = self.cgstat.anon_save
        file_save = self.cgstat.file_save

        for _cg in self.children.values():
            child_mem_limit, child_msw_usage, child_anon_save, child_file_save = (
                _cg.get_memsaving_recursive(compr_ratio))
            mem_limit += child_mem_limit
            msw_usage += child_msw_usage
            anon_save += child_anon_save
            file_save += child_file_save

        return mem_limit, msw_usage, anon_save, file_save

    # Cgroupfs IO
    def do_reclaim(self, reclaim_mem: int):
        target_bytes = reclaim_mem * 1024
        if cg_try_reclaim(self.path, target_bytes):
            LOGGER.debug('reclaim=%sK %s', reclaim_mem, self.path)
            return reclaim_mem
        
        LOGGER.debug('[do_reclaim] %s no memory.reclaim interface or failed', self.path)
        return 0

    def refresh_statistic(self, curr_time):
        """
        Update PSI and memory.stat
        """
        if curr_time - self.last_scan_time < self.params.scan_interval:
            return
        self.last_scan_time = curr_time
        try:
            psi_fd = os.open(os.path.join(self.path, 'memory.pressure'), os.O_RDONLY, 0o400)
            some = os.read(psi_fd, 128).splitlines()[0].replace(b'=', b' ').split()
            os.close(psi_fd)
            self.total_history.append(int(some[8]))
        except:
            pass

        self.cgstat.update_stat()

    # TODO(katrinzhou): Complete here
    # Given: self.total_active_anon, self.total_inactive_anon, self.total_active_file, self.total_inactive_file,
    #        self.total_history, self.integral
    # Solve: self.reclaim_ratio, self.sleep_interval
    def adjust_parameters(self):
        pass

    def _cal_reclaim_target(self):
        if self.force_reclaim:
            if not self.tree.conf.has_cgroup_zram_stat:
                mem = self.cgstat.current
            else:
                mem = self.cgstat.current - self.cgstat.zram_usage_in_bytes
            reclaim_mem = int(self.params.ratio * mem / (PAGESIZE * 4))
            return reclaim_mem * 4

        delta = (self.total_history[1] - self.total_history[0]) / self.params.interval
        if delta < self.params.psi_threshold:
            self.integral = delta
        else:
            self.integral += delta

        err = self.params.psi_threshold / max(self.integral, 1)
        adj = (err / self.params.coeff_backoff) ** 2
        if self.integral > self.params.psi_threshold:
            self.integral -= self.params.psi_threshold
        else:
            adj = min(adj * self.params.max_backoff, self.params.max_backoff) + 0.5

        # reclaim accept num_pages (4K * n)
        if not self.tree.conf.has_cgroup_zram_stat:
            reclaim_mem = int(adj * self.params.ratio * self.cgstat.current / (PAGESIZE * 4))
        else:
            mem = self.cgstat.current - self.cgstat.zram_usage_in_bytes
            reclaim_mem = int(adj * self.params.ratio * mem / (PAGESIZE * 4))

        return reclaim_mem * 4

    def _should_reclaim(self, curr_time: int):
        if self.cgstat.current == 0:
            # This might be outdated for at most `self.params.interval` seconds
            return False

        pct_usage = self.cgstat.cal_pct_usage()
        if pct_usage < self.params.pct_trigger_reclaim:
            return False

        if self.tree.conf.has_cgroup_zram_stat:
            anon_save = self.cgstat.zram_raw_in_bytes - self.cgstat.zram_usage_in_bytes
            total_mem = self.cgstat.memsw_usage - self.cgstat.zram_usage_in_bytes
            if anon_save >= total_mem * self.params.save_limit:
                return False
        if self.force_reclaim:
            return True
        if curr_time - self.last_reclaimed_time < self.params.interval:
            return False
        return True

    def __update_swappiess(self):
        # Limit reclaim percent to less than 0.6
        anon_in_mem = self.cgstat.total_inactive_anon + self.cgstat.total_active_anon
        swapout_anon_ratio = self.cgstat.swapout / (
            (self.cgstat.swapout + anon_in_mem) or 1)

        if swapout_anon_ratio > self.params.swapout_limit:
            # modify swappiness and keep reclaiming as much file pages as possible
            target_swappiness = 0
        else:
            target_swappiness = self.params.swappiness

        if self.swappiness != target_swappiness:
            self.set_swappiness(target_swappiness)

    def update_force_reclaim(self):
        # When mem used exceeds certain percent of memtotal, reduce memory as soon as possible if
        # force_reclaim is enabled
        if self.force_reclaim:
            self.cgstat.update_usage()
        if not self.tree.conf.force_reclaim:
            self.force_reclaim = False
            return
        p = (self.cgstat.memtotal - self.cgstat.memfree) / (self.cgstat.memtotal or 1)
        if self.force_reclaim and p < self.tree.conf.force_reclaim_target:
            self.force_reclaim = False
        if p >= self.tree.conf.force_reclaim_limit:
            self.force_reclaim = True

        LOGGER.debug('[update_force_reclaim] %s %s %s', self.force_reclaim, p, self.path)

    def reclaim_recursive(self, reclaim_control: ReclaimStat):
        curr_time = get_curr_time()

        self.update_force_reclaim()

        if not self._should_reclaim(curr_time):
            for _cg in self.children.values():
                _cg.reclaim_recursive(reclaim_control)
            return

        self.refresh_statistic(curr_time)
        # Return when cgroup current less than 4M
        if self.cgstat.current < 1048576 * 4:
            return

        self.__update_swappiess()

        reclaim_target = self._cal_reclaim_target()
        reclaim_prev = reclaim_control.total()
        for _cg in self.children.values():
            _cg.reclaim_recursive(reclaim_control)
        rest_reclaim = reclaim_target - (reclaim_control.total() - reclaim_prev)

        if rest_reclaim > 0:
            reclaim_control.reclaimed_simple += self.do_reclaim(rest_reclaim)

        self.last_reclaimed_time = curr_time

class EMMCgroup(SimpleCgroup):
    """
    Wrapper for operations on a emm cgroup
    """
    __slots__ = ('need_age', 'age_history')
    AGE_BOTH = 201

    def __init__(self, tree, path: str, rule: RuleItem, params: ReclaimParams, is_cgroot: bool):
        super().__init__(tree, path, rule, params, is_cgroot)
        self.last_reclaimed_time = [0, 0]
        self.integral = 0
        self.need_age = False
        self.do_emm_age()
        self.age_history = 0

    def do_emm_age(self, swappiness: int = AGE_BOTH):
        emm_age_path = os.path.join(self.path, 'memory.emm.age')
        if not os.path.exists(emm_age_path):
            LOGGER.debug('%s memory.emm.age not available', self.path)
            return

        string = 'max %d' % (swappiness)
        try:
            LOGGER.info('Trying to age %s with (%s)', self.path, string)
            with open(emm_age_path, 'w') as _f:
                _f.write(string)
            self.last_scan_time = 0
        except Exception as ex:
            LOGGER.info('%s unable to age %s: %s', self.path, string, ex)

    def do_emm_reclaim(self, size: int, swappiness: int = 100):
        emm_reclaim_path = os.path.join(self.path, 'memory.emm.reclaim')
        if not os.path.exists(emm_reclaim_path):
            LOGGER.debug('%s memory.emm.reclaim not available', self.path)
            self.need_age = True
            return 0

        string = '%dK %d' % (size, swappiness)
        try:
            with open(emm_reclaim_path, 'w') as _f:
                _f.write(string)
        except BlockingIOError:
            LOGGER.debug('[do_emm_reclaim] %s unable to reclaim too much: %s', self.path, string)
            self.need_age = True
        except FileNotFoundError:
            LOGGER.info('%s file not found', self.path)
        except PermissionError:
            LOGGER.critical('%s permission denied. reclaim_info = %s', self.path, string)
            sys.exit(1)
        except Exception as exp:
            LOGGER.info('%s skip reclaim for %s', self.path, exp)
        else:
            return size
        return 0

    def _should_reclaim(self, curr_time: int):
        # 关闭超卖的才检查内存水位线；母机超卖情况下，需要尽早回收内存
        # 命令行禁用超卖 或者 不支持超卖特性都视为 关闭超卖 
        if self.tree.conf.disable_oversell or self.tree.conf.page_reporting_supported != 1:
            pct_usage = self.cgstat.cal_pct_usage()
            if pct_usage < self.params.pct_trigger_reclaim:
                return False, False

        if self.tree.conf.has_cgroup_zram_stat:
            anon_save = self.cgstat.zram_raw_in_bytes - self.cgstat.zram_usage_in_bytes
            total_mem = self.cgstat.memsw_usage - self.cgstat.zram_usage_in_bytes
            if anon_save >= total_mem * self.params.save_limit:
                return False, False

        if self.force_reclaim:
            return True, True

        should_reclaim_anon = (curr_time - self.last_reclaimed_time[0]) \
                                >= self.params.interval_anon
        should_reclaim_file = (curr_time - self.last_reclaimed_time[1]) \
                                >= self.params.interval_file

        if not should_reclaim_anon and not should_reclaim_file:
            return False, False

        # Limit anon reclaimed percent to less than swapout_limit
        anon_in_mem = self.cgstat.total_inactive_anon + self.cgstat.total_active_anon
        swapout_anon_ratio = self.cgstat.swapout / (
            (self.cgstat.swapout + anon_in_mem) or 1)
        should_reclaim_anon = should_reclaim_anon and \
                                (swapout_anon_ratio <= self.params.swapout_limit)

        # Limit file reclaimed percent to less than pageout_limit
        file_in_mem = self.cgstat.total_inactive_file + self.cgstat.total_active_file
        pageout_file_ratio = self.cgstat.file_save / (
            (self.cgstat.file_save + file_in_mem) or 1)
        should_reclaim_file = should_reclaim_file and \
                                (pageout_file_ratio <= self.params.pageout_limit)

        should_reclaim_anon = should_reclaim_anon and self.params.ratio_anon > 0
        should_reclaim_file = should_reclaim_file and self.params.ratio_file > 0

        return should_reclaim_anon, should_reclaim_file

    # If possible, update the formula here
    def _cal_reclaim_target(self, reclaim_anon, reclaim_file):
        reclaim_mem = [0, 0]

        if self.force_reclaim:
            ratio_anon = min(0.1, 10 * self.params.ratio_anon or 0.1)
            ratio_file = min(0.1, 10 * self.params.ratio_file or 0.1)
            reclaim_mem[0] = int(ratio_anon * self.cgstat.emm_anon_cold / PAGESIZE)
            reclaim_mem[1] = int(ratio_file * self.cgstat.emm_file_cold / PAGESIZE)
            return reclaim_mem

        max_sleep_interval = max(self.params.interval_anon, self.params.interval_file)
        delta = (self.total_history[1] - self.total_history[0]) / max_sleep_interval
        if delta < self.params.psi_threshold:
            self.integral = delta
        else:
            self.integral += delta
        err = self.params.psi_threshold / max(self.integral, 1)
        adj = (err / self.params.coeff_backoff) ** 2
        if self.integral > self.params.psi_threshold:
            self.integral -= self.params.psi_threshold
        else:
            adj = min(adj * self.params.max_backoff, self.params.max_backoff) + 0.5

        if reclaim_anon:
            rec = self.cgstat.emm_anon_cold * (0.2 + 0.02 * self.params.interval_anon)
            # TODO: check 100
            reclaim_mem[0] = int(100 * adj * self.params.ratio_anon * rec / (PAGESIZE * 4))
        if reclaim_file:
            rec = self.cgstat.emm_file_cold * (0.2 + 0.02 * self.params.interval_file)
            reclaim_mem[1] = int(100 * adj * self.params.ratio_file * rec / (PAGESIZE * 4))
        return reclaim_mem

    def reclaim_recursive(self, reclaim_control: ReclaimStat):
        curr_time = get_curr_time()

        self.update_force_reclaim()

        reclaim_type = self._should_reclaim(curr_time)

        if not any(reclaim_type):
            for _cg in self.children.values():
                _cg.reclaim_recursive(reclaim_control)
            return

        self.refresh_statistic(curr_time)
        if self.cgstat.current < 1048576 * 4:
            return

        self.cgstat.update_lru_gen()

        inactive_pages = self.cgstat.emm_file_cold + self.cgstat.emm_anon_cold
        emm_pages_total = self.cgstat.emm_anon_total + self.cgstat.emm_file_total
        inactive_pages_percent = inactive_pages / (emm_pages_total or 1)
        if emm_pages_total > 0 and inactive_pages_percent < 0.1:
            self.need_age = True

        # Use a fixup for lru_gen aging since size for lru_gen have no purticular meaning
        # other than a indicator of if we should partially walk the page table.

        if self.need_age:
            self.do_emm_age()
            self.need_age = False
        elif curr_time - self.age_history > self.params.age_interval and self.is_active_age_enabled():
            self.do_emm_age()
            self.age_history = curr_time

        self.cgstat.update_lru_gen()
        reclaim_target = self._cal_reclaim_target(*reclaim_type)

        for _cg in self.children.values():
            _cg.reclaim_recursive(reclaim_control)

        reclaimed_anon = reclaimed_file = 0
        if reclaim_target[0] > 0:
            reclaimed_anon = self.do_emm_reclaim(reclaim_target[0], ANON_ONLY)
            reclaim_control.reclaimed_anon += reclaimed_anon
            self.last_reclaimed_time[0] = curr_time
        if reclaim_target[1] > 0:
            reclaimed_file = self.do_emm_reclaim(reclaim_target[1], FILE_ONLY)
            reclaim_control.reclaimed_file += reclaimed_file
            self.last_reclaimed_time[1] = curr_time
        if reclaimed_anon or reclaimed_file:
            LOGGER.debug('reclaim=%sK %s', reclaimed_anon + reclaimed_file, self.path)
            LOGGER.debug('  anon=%sK file=%sk', reclaimed_anon, reclaimed_file)

    def is_active_age_enabled(self):
        if self.params.age_interval > 0:
            return True
        return False