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
    # 纯外观选择，旧存档缺字段默认未选；不影响能力、奖励或许可。
    traveler: str | None = None

    def __post_init__(self) -> None:
        if self.traveler is not None and (type(self.traveler) is not str or self.traveler not in {'aether', 'lumine'}):
            raise ValueError('traveler must be aether, lumine or None')
        if self.quest_stage not in VALID_QUEST_STAGES:
            allowed = ", ".join(sorted(VALID_QUEST_STAGES))
            raise ValueError(
                f"invalid quest_stage={self.quest_stage!r}; expected one of: {allowed}"
            )
