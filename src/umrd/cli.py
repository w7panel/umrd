#!/usr/bin/env python3
#
# SPDX-License-Identifier: Apache-2.0

# Note: UMRD needs to answer two questions: how much memory to offload
# and what memory to offload.

# Note: There is no portability issue, it's only for Linux

import os
import sys
import time
import atexit
import argparse
import cProfile

from .umrd import UMRD
from .util import check_conf, clear_umrd_cgroup, get_kernel_version, init_wujing
from .util import ensure_zram
from .util import UMRD_VERSION, RECLAIM_PARAMS, ReclaimParams

UMRD_DESCRIPTION = """
Userspace Memory Reclaimer Daemon (UMRD %s)

UMRD monitors multi cgroups and dynamically reclaims memory using
psi memory pressure data.

UMRD targets psi and cumulative memory delays of PRESSURE microseconds
over the sampling period of INTERVAL seconds.

Corrective action scales exponentially with the error between observed
pressure and target pressure. High psi reduces the amount of reclaimed
pages. vice versa.
""" % UMRD_VERSION

PARSER = argparse.ArgumentParser(prog="UMRD", description=UMRD_DESCRIPTION,
                                 formatter_class=argparse.RawTextHelpFormatter)

RECLAIM_PARAMS_PARSER = PARSER.add_argument_group('Default Reclaim Parameters')
for param, desc in RECLAIM_PARAMS.items():
    RECLAIM_PARAMS_PARSER.add_argument("--%s" % param.replace('_', '-'),
                                       type=type(desc['default']), **desc)

PARSER.add_argument('--force-reclaim', action='store_true',
                    help='When memused exceeds certain percent of memtotal, increase ratio and '
                    'decrease interval to reclaim more memory.')
PARSER.add_argument('--force-reclaim-limit', type=float, default=0.95,
                    help='The limit ratio of memtotal to force reclaiming.')
PARSER.add_argument('--force-reclaim-target', type=float, default=0.9,
                    help='The target ratio of memtotal. Stop forcing reclaiming when memused '
                    'reaching the target.')

PARSER.add_argument('--open-zram', action='store_true',
                    help='Control whether zram is enabled during deployment.')
PARSER.add_argument('--comp-alg', type=str, default='lzo-rle',
                    help='The compressed algorithm used in zram device.')

# allowlist. e.g., /tmp/allowlist. The content is like:
#   /sys/fs/cgroup/kubepods/burstable/pod1
#   /sys/fs/cgroup/kubepods/burstable/pod2
# if parent cgroup and child cgroup are both in allowlist, only keeps parent cgroup in the list
PARSER.add_argument('--allowlist', type=str, default='/run/umrd/allowlist.cfg',
                    help='Allowlist file path, one cgroup path per line. '
                    'Default empty, allow non cgroup. If set *, allow all cgroups.')
PARSER.add_argument('--allowlist_oversell', type=str, default='/run/umrd/allowlist_oversell.cfg',
                    help='Allowlist file path for oversell, one cgroup path per line. '
                    'Default empty, allow non cgroup. If set *, allow all cgroups.')
# blocklist
PARSER.add_argument('--blocklist', type=str, default='/run/umrd/blocklist.cfg',
                    help='Blocklist file path, one cgroup path per line. '
                    'Default empty, block non cgroup.')
PARSER.add_argument('--debug', action='store_true',
                    help='The level of logger will be set to DEBUG. Also, output '
                    'monitored cgroups to /run/umrd/monitored-cgroups.list. '
                    'Warning: This setting hurts performance.')
PARSER.add_argument('--verbose', action='store_true',
                    help='The level of logger will be set to INFO.')
PARSER.add_argument('--quiet', action='store_true',
                    help='The level of logger will be set to ERROR.')
PARSER.add_argument('--allowlist_empty', action='store_true',
                    help='Do not set allowlist.')
PARSER.add_argument('--blocklist_empty', action='store_true',
                    help='Do not set blocklist.')
PARSER.add_argument('--mode', type=int, default=2, choices=[1, 2], help="""
The agent is able to reclaim in different modes.
    1: reclaim root cgroup;
    2: reclaim all cgroups;
""")
PARSER.add_argument('--standalone-cgroup', action='store_true',
                    help='Create a standalone cgroup for UMRD process.')
PARSER.add_argument('--oneshot', action='store_true',
                    help='(Debug) Run the reclaim cycle only once.')
PARSER.add_argument('--always-defaults', action='store_true',
                    help='The reclaimed cgroups always use default parameters.')
PARSER.add_argument('--profile', action='store_true',
                    help='Profile UMRD.')
PARSER.add_argument('--cpu-util-threshold', type=int, default=sys.maxsize, 
                    help='CPU utilization is higher than this value to stop recycling.')
PARSER.add_argument('--set-offline', action='store_true',
                    help='Set to offline scheduler.')
PARSER.add_argument('--cpu-quota-ratio', type=float, default=1.0,
                    help='Limit UMRD CPU bandwidth.')
PARSER.add_argument('--wait', action='store_true',
                    help='If true, poll and detect the start file to make UMRD work.')
PARSER.add_argument('--logfile', type=str, default='/run/umrd/umrd.log',
                    help='The path of log file. Infos will be writen into the log.')
PARSER.add_argument('--output-dir', type=str, default='/run/umrd',
                    help='The directory of outputs.')
PARSER.add_argument('--cycle-sleep', type=float, default=1.0,
                    help='This parameter is used as the top-level control. It limits the whole umrd '
                    'cycle and sleep at least (cycle_sleep_time - reclaim_cost_time).')
PARSER.add_argument('--disable-oversell', action='store_true',
                    help='This parameter is used to disable oversell.')
PARSER.add_argument('--hot-reload', type=str, default='/run/umrd/hot_reload.cfg',
                    help='This parameter is used to set the hot reload file path.')

def main():
    conf = PARSER.parse_args()
    if not check_conf(conf):
        sys.exit(1)

    cmd_conf = ReclaimParams.update_default_values(conf)
    clear_umrd_cgroup()

    ver = get_kernel_version()
    conf.use_emm_zram = b'-0018' in ver
    init_wujing(ver, conf.use_emm_zram)
    if conf.open_zram:
        ensure_zram(conf.comp_alg, conf.use_emm_zram,
                    conf.disk_path, conf.disk_size, conf.zram_reject_size)

    if conf.profile:
        profile = cProfile.Profile()
        profile.enable()
    else:
        profile = None

    umrd = UMRD(conf, cmd_conf, profile)

    atexit.register(umrd.clean)

    if conf.oneshot:
        umrd.run(conf.mode, 1)
    else:
        umrd.loop()
