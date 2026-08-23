"""Leased-B300 lessons. Site → Connect → App → Ready → Producer. Pack is on App. Brain is auto."""

from __future__ import annotations

from diony_wizard.lessons_gpu import gpu_lessons

try:
    B300_LESSONS: dict = gpu_lessons("b300")
except Exception:
    B300_LESSONS = {}
