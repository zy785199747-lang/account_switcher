# Riot API client.
#
# What we actually call:
#
#   1. account-v1 (regional cluster: americas | europe | asia | sea)
#      GET /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}
#      -> { "puuid": "...", "gameName": "...", "tagLine": "..." }
#
#   2. league-v4 (platform: na1, euw1, kr, ...)
#      GET /lol/league/v4/entries/by-puuid/{puuid}
#      -> [ { "queueType": "RANKED_SOLO_5x5", "tier": "DIAMOND", "rank": "II",
#             "leaguePoints": 47, ... }, ... ]
#
# We chain those two calls to turn a Riot ID into solo-queue rank info.
#
# (Older versions of this code had a summoner-v4 step in the middle to look up
# an "encrypted summoner id". Riot is deprecating that field; the response no
# longer reliably contains "id" on every account, which caused KeyError. The
# by-puuid league-v4 endpoint avoids the whole problem.)
#
# Error handling:
#   - 401/403/missing-key/network-error -> ApiUnavailable.
#     The UI banner reads only this exception class; it doesn't care WHY the
#     API can't be reached, just that it can't.
#   - 429                                -> RateLimited (caller can back off)
#   - 404                                -> RiotIdNotFound (Verify dialog
#                                            shows "player not found")
#   - other 4xx/5xx                      -> RiotApiError
#
# Caching:
#   Each Account has cached_tier/division/lp/cached_at. refresh_rank() updates
#   those fields and saves the vault. We do NOT re-fetch if cached_at is newer
#   than RANK_CACHE_TTL_SECONDS (1 hour). Use force=True to bypass that.

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

from src.models import Account

# Tweakable constants — top of file so they're easy to find when debugging.
HTTP_TIMEOUT_SECONDS = 10
RANK_CACHE_TTL_SECONDS = 3600  # 1 hour
RANKED_SOLO_QUEUE = "RANKED_SOLO_5x5"
RANKED_FLEX_QUEUE = "RANKED_FLEX_SR"

# Schema version of the cached_* block on each Account. Bumped any time we
# start writing a new field so that on the first launch after an upgrade we
# force a re-fetch even for accounts whose TTL hasn't expired yet — otherwise
# the new field stays blank until the cache naturally ages out.
# History:
#   1 -> initial: cached_tier/division/lp/cached_at (solo only)
#   2 -> phase 5: add cached_flex_tier/division/lp
#   3 -> phase 5: add cached_profile_icon_id
CURRENT_CACHE_SCHEMA = 3

# Map platform code -> regional cluster used by account-v1.
# https://developer.riotgames.com/docs/lol#routing-values
PLATFORM_TO_REGION = {
    # Americas
    "na1": "americas",
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    # Europe
    "euw1": "europe",
    "eun1": "europe",
    "tr1":  "europe",
    "ru":   "europe",
    # Asia
    "kr":  "asia",
    "jp1": "asia",
    # SEA
    "oc1": "sea",
    "ph2": "sea",
    "sg2": "sea",
    "th2": "sea",
    "tw2": "sea",
    "vn2": "sea",
}

log = logging.getLogger(__name__)


# ---------- exceptions ----------

class RiotApiError(Exception):
    # Base class. Anything from the Riot API layer.
    pass


class ApiUnavailable(RiotApiError):
    # The API can't be reached right now. Reasons: no API key set, key is
    # expired (401/403), network is down, request timed out.
    # The MainWindow banner subscribes to this. Caller should NOT retry in a
    # tight loop.
    pass


class RateLimited(RiotApiError):
    # 429 Too Many Requests. Caller might back off and retry later.
    pass


class RiotIdNotFound(RiotApiError):
    # 404 from account-v1: the gameName#tagLine doesn't exist. Used by the
    # Verify button to show "Player not found" inline.
    pass


# ---------- helpers ----------

def regional_route_for(platform: str) -> str:
    # Returns "americas" | "europe" | "asia" | "sea" given a platform like "na1".
    # Unknown platform falls back to "americas" so we still attempt something
    # rather than crashing — caller will see RiotApiError if it really doesn't
    # work.
    code = (platform or "").lower()
    return PLATFORM_TO_REGION.get(code, "americas")


def account_v1_url(game_name: str, tag_line: str, regional_route: str) -> str:
    # URL builder kept as a free function so tests can verify routing without
    # mocking a whole client.
    return (f"https://{regional_route}.api.riotgames.com"
            f"/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}")


def summoner_v4_url(puuid: str, platform: str) -> str:
    # Kept around in case we ever need profile icon / level. Not used in the
    # rank-fetch path anymore because Riot is phasing out the encrypted "id".
    return (f"https://{platform}.api.riotgames.com"
            f"/lol/summoner/v4/summoners/by-puuid/{puuid}")


def league_v4_url(summoner_id: str, platform: str) -> str:
    # Legacy by-summoner endpoint. Kept for completeness; not used anymore.
    return (f"https://{platform}.api.riotgames.com"
            f"/lol/league/v4/entries/by-summoner/{summoner_id}")


def league_v4_by_puuid_url(puuid: str, platform: str) -> str:
    # The endpoint we actually use. Skips the deprecated summoner-id step.
    return (f"https://{platform}.api.riotgames.com"
            f"/lol/league/v4/entries/by-puuid/{puuid}")


# ---------- result types ----------

@dataclass
class RankInfo:
    # One queue's rank. Used for both solo and flex.
    tier: Optional[str]       # "DIAMOND", "GOLD", ... or None if unranked
    division: Optional[str]   # "I", "II", "III", "IV" or None for high tiers
    lp: Optional[int]         # league points or None if unranked

    @classmethod
    def unranked(cls) -> "RankInfo":
        return cls(tier=None, division=None, lp=None)


@dataclass
class RankSet:
    # Solo + flex bundled together. league-v4 by-puuid returns both queues
    # in the same response, so fetching the pair costs the same as solo.
    solo: RankInfo
    flex: RankInfo


@dataclass
class AccountSnapshot:
    # Full result of one refresh cycle: rank set + profile icon id, plus the
    # PUUID we resolved along the way (handy for caching / debugging).
    # profile_icon_id is None when summoner-v4 failed — rank data is still
    # usable, the card just falls back to the procedural icon.
    puuid: str
    ranks: RankSet
    profile_icon_id: Optional[int]


# ---------- the client ----------

class RiotApiClient:
    # Holds the API key and pushes the timestamp of the last successful call.
    # Stateless apart from those — re-instantiating is cheap.

    def __init__(self, api_key: Optional[str]):
        self.api_key = api_key or ""
        self.last_success: Optional[float] = None  # epoch seconds

    # ---------- low-level GET ----------

    def _get(self, url: str) -> dict | list:
        # Single place where we map HTTP errors to our exception classes.
        if not self.api_key:
            raise ApiUnavailable("no Riot API key configured")

        headers = {"X-Riot-Token": self.api_key}
        log.debug("GET %s", url)
        try:
            r = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            log.info("network error talking to Riot: %s", exc)
            raise ApiUnavailable(f"network error: {exc}") from exc

        if r.status_code == 200:
            self.last_success = time.time()
            return r.json()
        if r.status_code in (401, 403):
            log.info("Riot API auth failure: %s", r.status_code)
            raise ApiUnavailable(f"API key rejected (HTTP {r.status_code})")
        if r.status_code == 404:
            raise RiotIdNotFound(f"not found: {url}")
        if r.status_code == 429:
            raise RateLimited("rate limited (HTTP 429)")
        raise RiotApiError(f"unexpected HTTP {r.status_code}: {r.text[:200]}")

    # ---------- exposed actions ----------

    def test_key(self) -> bool:
        # Cheap call AdminWindow uses to show the green check / red cross.
        # Uses a known-valid Riot ID (Faker) on KR. We don't care about the
        # data — only whether the auth succeeds.
        try:
            self._get(account_v1_url("Hide on bush", "KR1", "asia"))
            return True
        except ApiUnavailable:
            return False
        except RiotIdNotFound:
            # If the Riot ID changed, that still means the key is valid.
            return True
        except RiotApiError:
            return False

    def get_puuid(self, game_name: str, tag_line: str, regional_route: str) -> str:
        data = self._get(account_v1_url(game_name, tag_line, regional_route))
        if not isinstance(data, dict) or "puuid" not in data:
            # Defensive: log and raise rather than KeyError-crash the GUI.
            log.warning("account-v1 response missing 'puuid': %r", data)
            raise RiotApiError("Riot account-v1 response did not include a PUUID.")
        return data["puuid"]

    def get_ranks_by_puuid(self, puuid: str, platform: str) -> RankSet:
        # league-v4 returns one entry per queue the account has played ranked
        # in. We pick out solo and flex; everything else (clash, TFT, etc.) is
        # ignored. Missing queue -> Unranked for that side.
        entries = self._get(league_v4_by_puuid_url(puuid, platform))
        if not isinstance(entries, list):
            log.warning("league-v4 by-puuid returned non-list: %r", entries)
            raise RiotApiError("Riot league-v4 returned an unexpected shape.")

        solo = RankInfo.unranked()
        flex = RankInfo.unranked()
        for e in entries:
            if not isinstance(e, dict):
                continue
            qt = e.get("queueType")
            info = RankInfo(
                tier=e.get("tier"),
                division=e.get("rank"),
                lp=e.get("leaguePoints"),
            )
            if qt == RANKED_SOLO_QUEUE:
                solo = info
            elif qt == RANKED_FLEX_QUEUE:
                flex = info
        return RankSet(solo=solo, flex=flex)

    def fetch_rank(self, account: Account) -> RankSet:
        # Two-call chain: Riot ID -> PUUID -> both ranked queues. Used by
        # the Verify button in AddAccountDialog where we only care about
        # whether the Riot ID exists and what the rank is — no need for
        # the profile-icon call.
        regional = regional_route_for(account.region)
        puuid = self.get_puuid(account.game_name, account.tag_line, regional)
        return self.get_ranks_by_puuid(puuid, account.region)

    def get_profile_icon_id(self, puuid: str, platform: str) -> Optional[int]:
        # summoner-v4 by-puuid returns { "profileIconId": int, ... }. Riot is
        # deprecating the encrypted "id" field on this response but the icon
        # id keeps working. Returns None on a malformed payload (defensive).
        data = self._get(summoner_v4_url(puuid, platform))
        if isinstance(data, dict):
            icon = data.get("profileIconId")
            if isinstance(icon, int):
                return icon
        log.warning("summoner-v4 response missing profileIconId: %r", data)
        return None

    def fetch_snapshot(self, account: Account) -> AccountSnapshot:
        # Three-call chain used by refresh_rank: account-v1 -> league-v4 ->
        # summoner-v4. Profile icon is best-effort — if summoner-v4 fails we
        # still return ranks (icon falls back to procedural). Any failure on
        # account-v1 or league-v4 still raises, since rank data is the
        # primary thing we care about.
        regional = regional_route_for(account.region)
        puuid = self.get_puuid(account.game_name, account.tag_line, regional)
        ranks = self.get_ranks_by_puuid(puuid, account.region)
        icon_id: Optional[int] = None
        try:
            icon_id = self.get_profile_icon_id(puuid, account.region)
        except ApiUnavailable as exc:
            # If the key got rate-limited or expired between the two calls,
            # we still want ranks. Re-raise unavailability so the banner
            # logic upstream can react properly.
            log.info("profile icon fetch hit ApiUnavailable: %s", exc)
            raise
        except RiotApiError as exc:
            # Any other API error is treated as "icon not available right
            # now" — log and continue with icon_id=None.
            log.warning("profile icon fetch failed (continuing): %s", exc)
        return AccountSnapshot(puuid=puuid, ranks=ranks, profile_icon_id=icon_id)


# ---------- module-level convenience ----------

def is_cache_fresh(account: Account, ttl_seconds: int = RANK_CACHE_TTL_SECONDS) -> bool:
    # Pure TTL check — returns True if the cached rank is younger than the
    # TTL. Does NOT consider schema version. Callers that want the full
    # "should we hit the API?" answer should use cache_needs_refresh().
    if account.cached_at is None:
        return False
    return (time.time() - account.cached_at) < ttl_seconds


def cache_needs_refresh(account: Account,
                        ttl_seconds: int = RANK_CACHE_TTL_SECONDS) -> bool:
    # Returns True when we should fetch from Riot. Three reasons:
    #   1. Never been fetched (cached_at is None).
    #   2. TTL expired.
    #   3. Schema is older than CURRENT_CACHE_SCHEMA — the cache is missing
    #      a field that newer code reads, so even a "young" cache is stale
    #      from a feature-completeness point of view.
    if account.cached_at is None:
        return True
    if (time.time() - account.cached_at) >= ttl_seconds:
        return True
    if account.cached_schema < CURRENT_CACHE_SCHEMA:
        return True
    return False


def refresh_rank(client: RiotApiClient, account: Account, force: bool = False) -> bool:
    # Updates account.cached_* (solo + flex + profile_icon_id) in place.
    # Returns True if a fresh fetch happened, False if the cache was still
    # good (and we skipped the call). Caller is responsible for
    # vault.update(account) afterwards.
    if not force and not cache_needs_refresh(account):
        log.debug("cache still fresh for %s#%s, skipping fetch",
                  account.game_name, account.tag_line)
        return False

    snap = client.fetch_snapshot(account)
    ranks = snap.ranks
    account.cached_tier = ranks.solo.tier
    account.cached_division = ranks.solo.division
    account.cached_lp = ranks.solo.lp
    account.cached_flex_tier = ranks.flex.tier
    account.cached_flex_division = ranks.flex.division
    account.cached_flex_lp = ranks.flex.lp
    # snap.profile_icon_id is None only when summoner-v4 failed mid-fetch.
    # Preserve the previously-cached id in that case so we don't blank a
    # working card just because one of the three calls flaked.
    if snap.profile_icon_id is not None:
        account.cached_profile_icon_id = snap.profile_icon_id
    account.cached_at = time.time()
    # Stamp the row with the current schema so next launch knows the cache
    # is feature-complete and can skip the fetch within the TTL window.
    account.cached_schema = CURRENT_CACHE_SCHEMA
    log.info("rank refreshed for %s#%s: solo=%s/%s/%s flex=%s/%s/%s icon=%s schema=%d",
             account.game_name, account.tag_line,
             ranks.solo.tier, ranks.solo.division, ranks.solo.lp,
             ranks.flex.tier, ranks.flex.division, ranks.flex.lp,
             account.cached_profile_icon_id, CURRENT_CACHE_SCHEMA)
    return True
