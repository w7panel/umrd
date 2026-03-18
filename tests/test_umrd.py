# Copyright (c) 2024 Tencent Cloud.
# SPDX-License-Identifier: GPL-2.0

"""
Test module for umrd package.
"""

import pytest


class TestReclaimParams:
    def test_version_exists(self):
        from umrd import UMRD_VERSION
        assert UMRD_VERSION is not None

    def test_reclaim_params_exist(self):
        from umrd.util import RECLAIM_PARAMS
        assert isinstance(RECLAIM_PARAMS, dict)
        assert len(RECLAIM_PARAMS) > 0


class TestCLI:
    def test_parser_exists(self):
        from umrd.cli import PARSER
        assert PARSER is not None

    def test_main_function_exists(self):
        from umrd.cli import main
        assert callable(main)
