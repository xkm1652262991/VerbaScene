"""Compatibility exports for the script business module.

Application code must import :mod:`app.scripts` directly. This module remains
for external maintenance scripts that still use the pre-module path.
"""

from app.scripts.contracts import SCRIPT_GENERATION_TASK_TYPE
from app.scripts.service import (
    create_script_generation_task,
    execute_script_generation_task,
    generate_script_now,
    get_latest_script,
    get_primary_chapter_or_404,
    get_project_or_404,
    get_script_or_404,
    update_script,
)

__all__ = [
    "SCRIPT_GENERATION_TASK_TYPE",
    "create_script_generation_task",
    "execute_script_generation_task",
    "generate_script_now",
    "get_latest_script",
    "get_primary_chapter_or_404",
    "get_project_or_404",
    "get_script_or_404",
    "update_script",
]
