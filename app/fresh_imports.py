"""Reload this project's own modules when their files changed since they were imported.

Why: Streamlit keeps imported modules in memory between runs. When Streamlit Cloud pulls new
code while the app is awake, the dashboard script is re-run but the old src/ modules stay
loaded, so new dashboard code can meet old analysis code (on 2026-10-20 that broke the live
site with an ImportError until the app was rebooted).

The dashboard calls reload_changed_modules() twice: before its imports (reload anything whose
file changed since the last run) and after them (record the version of newly imported modules).
"""
import importlib
import os
import sys

# Our modules, in dependency order: a module is reloaded after the modules it imports
PROJECT_MODULES = [
    "extract.polymarket", "transform.schemas", "transform.snapshots", "transform.normalize",
    "transform.completeness", "transform.quality", "load.postgres", "analysis.calibration",
    "analysis.bootstrap",
]


def reload_changed_modules():
    """If any project module's file changed since it was loaded, reload all of them, in order."""
    loaded = [sys.modules[name] for name in PROJECT_MODULES if name in sys.modules]
    changed = any(os.path.getmtime(module.__file__) > getattr(module, "_loaded_at", float("inf"))
                  for module in loaded)
    for name in PROJECT_MODULES:
        module = sys.modules.get(name)
        if module is None:
            continue
        if changed:
            module = importlib.reload(module)
        module._loaded_at = os.path.getmtime(module.__file__)  # remember which version is in memory
