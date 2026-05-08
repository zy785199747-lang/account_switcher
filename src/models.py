# Account dataclass.
# This is the only thing the vault stores per-account.
# In Phase 1 only `id`, `username`, `password`, `game_name`, `tag_line`, `region`
# are used. The cached_* fields exist already so adding rank in Phase 3 is
# a one-line change with no migration headache.

from dataclasses import dataclass, field, asdict
from typing import Optional
import uuid


@dataclass
class Account:
    # Stable identifier so cards can be re-rendered without ambiguity.
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # The login credentials Riot Client expects.
    username: str = ""
    password: str = ""

    # Riot ID = "<game_name>#<tag_line>". This is what the API uses.
    game_name: str = ""
    tag_line: str = ""

    # Platform routing value (e.g. "na1", "euw1", "kr"). Used by summoner-v4
    # and league-v4 endpoints in Phase 3.
    region: str = "na1"

    # Cached rank fields — populated by Riot API in Phase 3.
    cached_tier: Optional[str] = None
    cached_division: Optional[str] = None
    cached_lp: Optional[int] = None
    cached_at: Optional[float] = None  # epoch seconds

    def to_dict(self) -> dict:
        # Used when serialising the vault to JSON before encryption.
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Account":
        # Forgiving load: any unknown keys are ignored, missing keys get defaults.
        # This means we can add fields in later phases without breaking old vaults.
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
