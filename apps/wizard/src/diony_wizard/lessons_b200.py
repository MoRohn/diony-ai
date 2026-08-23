"""Leased-B200 lessons. Site → Connect → App → Ready → Producer. Pack is on App. Brain is auto."""

from __future__ import annotations

from diony_wizard.lessons_gpu import gpu_lessons

try:
    B200_LESSONS: dict = gpu_lessons("b200")
except Exception:
    B200_LESSONS = {}
