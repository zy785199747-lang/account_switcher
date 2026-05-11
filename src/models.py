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

    # Free-text reminder shown on the card under the region. Helpful when
    # several accounts share similar Riot IDs ("main", "smurf", "ARAM only").
    note: str = ""

    # Cached rank fields — populated by Riot API in Phase 3.
    # The unprefixed fields are solo-queue (RANKED_SOLO_5x5); the flex_*
    # variants are flex-queue (RANKED_FLEX_SR). Both come from the same
    # league-v4 by-puuid response so fetching both is free.
    cached_tier: Optional[str] = None
    cached_division: Optional[str] = None
    cached_lp: Optional[int] = None
    cached_flex_tier: Optional[str] = None
    cached_flex_division: Optional[str] = None
    cached_flex_lp: Optional[int] = None
    cached_at: Optional[float] = None  # epoch seconds

    # Profile icon ID from summoner-v4. The Data Dragon CDN converts this
    # number into an actual PNG (see src/riot/ddragon.py). None = not
    # fetched yet OR the summoner-v4 call failed.
    cached_profile_icon_id: Optional[int] = None

    # Schema version of the cached_* block. Old vaults default to 1 (solo
    # only). Schema 2 adds flex; schema 3 adds the profile icon ID. Bump on
    # future schema changes and the refresh logic will force a re-fetch even
    # when the TTL has not yet expired, so users see fresh data immediately
    # after upgrading.
    cached_schema: int = 1

    def to_dict(self) -> dict:
        # Used when serialising the vault to JSON before encryption.
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Account":
        # Forgiving load: any unknown keys are ignored, missing keys get defaults.
        # This means we can add fields in later phases without breaking old vaults.
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
