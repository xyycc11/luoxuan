"""游戏状态模型。

dataclass本身仍是内存对象。Day19网页API显式保存到SQL；旧CLI仍仅用内存。
"""

from dataclasses import dataclass, field


VALID_QUEST_STAGES = {"not_started", "active", "completed"}


@dataclass
class GameState:
    player_id: str
    world_id: str
    inventory: dict[str, int] = field(default_factory=dict)
    quest_stage: str = "not_started"
    has_permit: bool = False
    training_herb_claimed: bool = False

    def __post_init__(self) -> None:
        if self.quest_stage not in VALID_QUEST_STAGES:
            allowed = ", ".join(sorted(VALID_QUEST_STAGES))
            raise ValueError(
                f"invalid quest_stage={self.quest_stage!r}; expected one of: {allowed}"
            )
