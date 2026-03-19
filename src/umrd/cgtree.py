# Copyright (c) 2024 w7panel
# SPDX-License-Identifier: Apache-2.0
import re
import os
import time
import collections
from typing import Dict

from .util import ALLOWED, SCAN_ONLY
from .util import LOGGER, RuleItem, ReclaimParams, ReclaimStat, RECLAIM_PARAMS
from .util import CGROUP_V2_ROOT
from .util import enable_oversell, disable_oversell, set_log_level, ensure_zram
from .cgroup import create_cgroup, SimpleCgroup

class CgroupTree:
    def __init__(self, conf, cmd_conf, umrd_cgroup='') -> None:
        self.roots = {}
        self.conf = conf
        self.cmd_conf = cmd_conf
        self.umrd_cgroup = umrd_cgroup

        self.path_tree: Dict[str, RuleItem] = None
        self.block_regex = None

        self.allow_last_modified = None
        self.block_last_modified = None

        self.lru_gen = 0

        self.accum_reclaim_simple = 0
        self.accum_reclaim_anon = 0
        self.accum_reclaim_file = 0
        self.page_reporting_status = -1
        self.compaction_proactiveness_status = -1
        if self.conf.disable_oversell:
            disable_oversell(conf)
        else:
            enable_oversell(conf)
            if self.conf.page_reporting_supported == 1:
                # status used to record current val
                if self.conf.init_page_reporting_enable != -1:
                    with open('/proc/sys/vm/page_reporting_enable', 'rb') as _f:
                        self.page_reporting_status = int(_f.readline().decode('utf-8'))
                if self.conf.init_compaction_proactiveness != -1:
                    with open('/proc/sys/vm/compaction_proactiveness', 'rb') as _f:
                        self.compaction_proactiveness_status = int(_f.readline().decode('utf-8'))

    def debug_show_cgroups(self, cgitem):
        res = []
        for _p, _cg in cgitem.items():
            if _cg.rule is None:
                continue
            if _cg.rule.type == ALLOWED:
                res_list = [_p]
                for _k in RECLAIM_PARAMS.keys():
                    res_list.append('%s=%s' % (_k, getattr(_cg.params, _k)))
                res.append(' '.join(res_list))
            child_res = self.debug_show_cgroups(_cg.children)
            res.extend(child_res)
        return res

    # allowlist/blocklist not empty
    def try_update_rules(self):
        allow_updated = False
        block_updated = False

        # 检查超卖、日志等级、启动zram等参数是否有变化
        self.check_hot_reload()

        oversell = (not self.conf.disable_oversell) and self.conf.page_reporting_supported == 1
        # 如果超卖 那么切换到超卖对应的 allowlist_oversell
        if oversell:
            allowlist = self.conf.allowlist_oversell
        else:
            allowlist = self.conf.allowlist

        if allowlist:
            if not os.path.exists(allowlist):
                # In case allowlist file is deleted
                with open(allowlist, 'wb') as allow_file:
                    pass

            allow_modified_time = time.ctime(os.path.getmtime(allowlist))
            if self.allow_last_modified != allow_modified_time:
                self.allow_last_modified = allow_modified_time
                allow_updated = True

        if self.conf.blocklist:
            if not os.path.exists(self.conf.blocklist):
                # In case block_list file is deleted
                with open(self.conf.blocklist, 'wb'):
                    pass

            block_modified_time = time.ctime(os.path.getmtime(self.conf.blocklist))
            if self.block_last_modified != block_modified_time:
                self.block_last_modified = block_modified_time
                block_updated = True
        else:
            self.block_regex = re.compile(self.umrd_cgroup)

        # If allow/block is updated, they have to be updated as a whole
        if not allow_updated and not block_updated:
            return

        new_path_tree = {}

        with open(allowlist, encoding='ascii') as _f:
            lines = [line for line in _f]

        if not lines:
            if oversell:
                content = CGROUP_V2_ROOT + ' ' + 'interval_anon=5 ratio_anon=0.002 interval_file=10 ratio_file=0.0002'
            else:
                content = CGROUP_V2_ROOT + '/'
            with open(allowlist, 'wb') as allow_file:
                allow_file.write(content.encode('ascii'))
            lines = [content]

        for line in lines:
            conf = line.strip().split()
            if len(conf) == 0:
                continue
            path = conf.pop(0)
            path = path.rstrip('/') if path != '/' else path
            params = ReclaimParams()
            dict_conf = self.cmd_conf.copy()
            if not self.conf.always_defaults and conf:
                for _tok in conf:
                    _k, _v = _tok.split('=')
                    _k = _k.replace('-', '_')
                    dict_conf[_k] = _v
            params.read_conf(dict_conf)
            new_path_tree[path] = RuleItem(path, ALLOWED, params)

        with open(self.conf.blocklist, encoding='ascii') as _f:
            block_list = sorted([self.umrd_cgroup] + [i.strip() for i in _f])
            block_list = [i.rstrip('/') if i != '/' else i for i in block_list]
            if self.conf.mode == 1:
                block_list = [CGROUP_V2_ROOT + '/'] + block_list
            block_list = list(set(block_list) - set(['']))

        # block_updated is true means that block_list is not None
        if block_updated:
            if '.*' in block_list:
                new_path_tree = {}
            elif block_list:
                self.block_regex = re.compile('|'.join(block_list))
                new_path_tree = {path: rule for path, rule in new_path_tree.items()
                                 if not self.block_regex.match(path)}
            else:
                self.block_regex = None

        # update allow_rules
        if new_path_tree is not None:
            self.path_tree = {}
            for path, rule in new_path_tree.items():
                path_tokenized = path.split(os.sep)
                path_parents = path_tokenized.copy()

                while len(path_parents) > 1:
                    path_parent = os.path.join("/", *path_parents)
                    path_parents.pop()

                    if path_parent == path:
                        continue

                    existing_rule = new_path_tree.get(path_parent)
                    if existing_rule and existing_rule.type == ALLOWED and \
                        existing_rule.params == rule.params:
                        LOGGER.warning("Dropping rule for '%s' because its parent "
                                       "'%s' is included.", path, path_parent)
                        path_tokenized = None
                        break

                if path_tokenized:
                    path = os.path.join("/", *path_tokenized)
                    self.path_tree[path] = rule
                    while path_tokenized:
                        path_tokenized.pop()
                        path = os.path.join("/", *path_tokenized)
                        self.path_tree.setdefault(path, RuleItem(path, SCAN_ONLY, None))

            # Sort it for better lookup performance
            self.path_tree = collections.OrderedDict(sorted(self.path_tree.items(), reverse=True))
        else:
            self.path_tree = {}

    def get_path_rule(self, path) -> RuleItem:
        if self.block_regex and self.block_regex.match(path):
            return None

        rule = self.path_tree.get(path, None)
        if rule:
            return rule

        # TODO: Need to rework the Rule Engine to be token tree based for
        # less memory usage and faster search
        record_path, record_rule = "", None
        for _path, rule in self.path_tree.items():
            if path.startswith(_path) and len(_path) > len(record_path):
                record_path = _path
                record_rule = rule

        return record_rule

    # roots might not be the root cgroups
    # when delete allowlist, old root cgroups (non-root cgroup)
    # might be changed to child cgroup
    def find_rootcg(self):
        prev_cgroups = self.roots
        self.roots = {}

        with open('/proc/mounts', encoding='ascii', newline='') as proc_mnt:
            for mount in proc_mnt:
                mount = mount.split()

                # Check for valid cgroup memory mount
                if mount[0] == 'cgroup2':
                    pass  # cgroup v2 supported
                elif mount[0] == 'cgroup' and mount[-3].endswith(',memory'):
                    pass
                elif mount[0] == 'memory' and mount[2] == 'cgroup':
                    pass
                else:
                    continue

                mnt_point = mount[1]

                rule = self.get_path_rule(mnt_point)
                if rule is None:
                    LOGGER.warning('Rootcg not being included in reclaim lists')
                    continue

                cgroup = prev_cgroups.get(mnt_point)
                if cgroup and cgroup.rule == rule:
                    if rule.params is not None and \
                        rule.params.zram_priority is not None and \
                        issubclass(type(cgroup), SimpleCgroup) and \
                        cgroup.zram_priority != rule.params.zram_priority:
                        cgroup.zram_priority = rule.params.zram_priority
                        cgroup.set_zram_priority(rule.params.zram_priority)
                    self.roots[mnt_point] = cgroup
                else:
                    self.roots[mnt_point] = create_cgroup(self, mnt_point, rule, rule.params, True)

        # Add ALLOWED paths from path_tree as roots
        for path, rule in self.path_tree.items():
            if rule.type == ALLOWED and path not in self.roots:
                if os.path.isdir(path):
                    cgroup = prev_cgroups.get(path)
                    if cgroup and cgroup.rule == rule:
                        self.roots[path] = cgroup
                    else:
                        self.roots[path] = create_cgroup(self, path, rule, rule.params, False)


    def check_lru(self):
        if not os.path.exists('/sys/kernel/mm/lru_gen/enabled'):
            return
        with open('/sys/kernel/mm/lru_gen/enabled', 'rb') as _f:
            lru_gen_enabled = _f.read().strip()
            lru_gen_enabled = int(lru_gen_enabled[-1])
        lru_gen = lru_gen_enabled > 0

        if self.lru_gen != lru_gen:
            self.lru_gen = lru_gen
            # A lru_gen switch will cause all historically data gets
            # invalidated, so drop old data, re-scan everything later.
            self.roots = {}

    def try_refresh(self, mode):
        self.check_lru()
        self.find_rootcg()

        if mode == 1:
            level = 0
        else:
            level = -2

        for _cg in self.roots.values():
            try:
                _cg.refresh(level)
            except FileNotFoundError as exp:
                if os.path.isdir(exp.filename):
                    # CG is dead, but keep it here since this is top level
                    pass
                else:
                    raise exp
            except Exception as exp:
                raise exp

    def try_reclaim(self, rc: ReclaimStat):
        for memcg in self.roots.values():
            memcg.reclaim_recursive(rc)

    def set_proactive(self, val):
        # /proc/sys/vm/compaction_proactiveness not exists
        if self.conf.init_compaction_proactiveness == -1:
            return
        if self.compaction_proactiveness_status != val:
            try:
                with open('/proc/sys/vm/compaction_proactiveness', 'wb+') as _f:
                    _f.write(str(val).encode('utf-8'))
                self.compaction_proactiveness_status = val
                LOGGER.info('Oversell: set compaction_proactiveness as %s', val)
            except Exception as exp:
                LOGGER.info('Oversell: failed to set compaction_proactiveness as %s for : %s', val, exp)

        if val == self.conf.proactive_high:
            try:
                with open('/proc/sys/vm/compact_memory', 'wb') as _f:
                    _f.write(b'1')
                LOGGER.info('Oversell: set compact_memory as 1')
            except Exception as exp:
                LOGGER.info('Oversell: failed to set compact_memory as 1 for : %s', exp)

    def set_pagereport_enable(self, val):
        # /proc/sys/vm/page_reporting_enable not exists
        if self.conf.init_page_reporting_enable == -1:
            return
        if self.page_reporting_status == val:
            return
        try:
            with open('/proc/sys/vm/page_reporting_enable', 'wb+') as _f:
                _f.write(str(val).encode('utf-8'))
            self.page_reporting_status = val
            LOGGER.info('Oversell: set page_reporting_enable as %s', val)
        except Exception as exp:
            LOGGER.info('Oversell: failed to set page_reporting_enable as %s for : %s', val, exp)

    def recover_pr_pro(self):
        self.set_proactive(self.conf.init_compaction_proactiveness)
        self.set_pagereport_enable(self.conf.init_page_reporting_enable)

    def check_hot_reload(self):
        hot_conf = {}
        with open(self.conf.hot_reload, 'r') as _f:
            for l in _f.readlines():
                if '=' in l:
                    k, v = l.split('=')
                    k = k.strip()
                    v = v.strip()
                hot_conf[k] = v
            if 'log_file_handler_level' in hot_conf:
                set_log_level(self.conf.log_file_handler, hot_conf['log_file_handler_level'])
            if 'log_console_handler_level' in hot_conf:
                set_log_level(self.conf.log_console_handler, hot_conf['log_console_handler_level'])
            if 'oversell' in hot_conf:
                oversell = int(hot_conf['oversell'])
                if oversell == 0 and self.conf.disable_oversell != True:
                    self.conf.disable_oversell = True
                    disable_oversell(self.conf)
                if oversell == 1 and self.conf.disable_oversell != False:
                    self.conf.disable_oversell = False
                    enable_oversell(self.conf)
            if 'open_zram' in hot_conf and self.conf.open_zram != True and int(hot_conf['open_zram']) == 1:
                self.conf.open_zram = True
                ensure_zram(self.conf.comp_alg, self.conf.use_emm_zram, self.conf.disk_path, self.conf.disk_size, self.conf.zram_reject_size)
