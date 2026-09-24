"""Where persistent files live.

By default next to the code (backend/), which is what a local install wants.
In a container the code is disposable and the data is not, so
STOCKSTRAT_DATA_DIR points everything that must survive — the key file,
backups, the market-data cache — at a mounted volume.
"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("STOCKSTRAT_DATA_DIR") or BACKEND_DIR)
