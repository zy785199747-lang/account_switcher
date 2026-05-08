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


# ---------- result type ----------

@dataclass
class RankInfo:
    # Only solo-queue rank. The plan said we don't show flex.
    tier: Optional[str]       # "DIAMOND", "GOLD", ... or None if unranked
    division: Optional[str]   # "I", "II", "III", "IV" or None for high tiers
    lp: Optional[int]         # league points or None if unranked


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

    def get_solo_rank_by_puuid(self, puuid: str, platform: str) -> RankInfo:
        entries = self._get(league_v4_by_puuid_url(puuid, platform))
        if not isinstance(entries, list):
            log.warning("league-v4 by-puuid returned non-list: %r", entries)
            raise RiotApiError("Riot league-v4 returned an unexpected shape.")
        for e in entries:
            if isinstance(e, dict) and e.get("queueType") == RANKED_SOLO_QUEUE:
                return RankInfo(
                    tier=e.get("tier"),
                    division=e.get("rank"),
                    lp=e.get("leaguePoints"),
                )
        # No solo-queue entry: account is unranked.
        return RankInfo(tier=None, division=None, lp=None)

    def fetch_rank(self, account: Account) -> RankInfo:
        # Two-call chain: Riot ID -> PUUID -> solo rank.
        regional = regional_route_for(account.region)
        puuid = self.get_puuid(account.game_name, account.tag_line, regional)
        return self.get_solo_rank_by_puuid(puuid, account.region)


# ---------- module-level convenience ----------

def is_cache_fresh(account: Account, ttl_seconds: int = RANK_CACHE_TTL_SECONDS) -> bool:
    # Returns True if the cached rank is younger than the TTL.
    if account.cached_at is None:
        return False
    return (time.time() - account.cached_at) < ttl_seconds


def refresh_rank(client: RiotApiClient, account: Account, force: bool = False) -> bool:
    # Updates account.cached_* in place. Returns True if a fresh fetch happened,
    # False if the cache was still good (and we skipped the call).
    # Caller is responsible for vault.update(account) afterwards.
    if not force and is_cache_fresh(account):
        log.debug("cache still fresh for %s#%s, skipping fetch",
                  account.game_name, account.tag_line)
        return False

    info = client.fetch_rank(account)
    account.cached_tier = info.tier
    account.cached_division = info.division
    account.cached_lp = info.lp
    account.cached_at = time.time()
    log.info("rank refreshed for %s#%s: tier=%s div=%s lp=%s",
             account.game_name, account.tag_line,
             info.tier, info.division, info.lp)
    return True
