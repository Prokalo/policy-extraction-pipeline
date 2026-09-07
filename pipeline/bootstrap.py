from __future__ import annotations

import sys
from pathlib import Path


def activate_local_venv() -> None:
    root = Path(__file__).resolve().parent.parent
    venv_site = root / ".venv" / "lib"
    if not venv_site.exists():
        return

    candidates = sorted(venv_site.glob("python*/site-packages"))
    for site_packages in candidates:
        site_str = str(site_packages)
        if site_str not in sys.path:
            sys.path.insert(0, site_str)
