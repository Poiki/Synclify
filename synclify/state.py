from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Set


def default_pending() -> List[str]:
    return []


def default_added() -> Set[str]:
    return set()


@dataclass
class RuntimeState:
    yt_search_disabled: bool = False
    prompted_after_quota: bool = False
    continue_manual_after_quota: bool = False
    continue_web_auto_after_quota: bool = False
    plan_mode_only: bool = False
    pending_web_adds: List[str] = field(default_factory=default_pending)
    added_video_ids: Set[str] = field(default_factory=default_added)

    def enable_plan_mode(self) -> None:
        self.plan_mode_only = True
        self.continue_web_auto_after_quota = True

    def reset(self) -> None:
        self.yt_search_disabled = False
        self.prompted_after_quota = False
        self.continue_manual_after_quota = False
        self.continue_web_auto_after_quota = False
        self.plan_mode_only = False
        self.pending_web_adds.clear()
        self.added_video_ids.clear()


STATE = RuntimeState()

__all__ = ["RuntimeState", "STATE"]
