#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Launch the minidlna configuration GUI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from minidlnaconfig.app import main

if __name__ == "__main__":
    raise SystemExit(main())
