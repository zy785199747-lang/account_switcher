# Unit tests for src/riot/api.py.
#
# We only test the pure-logic bits: URL builders, regional routing, cache
# freshness check. We do NOT hit the real Riot API in tests — that would
# require a key and would be flaky.

import time

import pytest

from src.models import Account
from src.riot.api import (
    account_v1_url,
    is_cache_fresh,
    league_v4_by_puuid_url,
    league_v4_url,
    regional_route_for,
    summoner_v4_url,
)


# ---------- regional routing ----------

@pytest.mark.parametrize("platform,expected", [
    ("na1",  "americas"),
    ("br1",  "americas"),
    ("la1",  "americas"),
    ("la2",  "americas"),
    ("euw1", "europe"),
    ("eun1", "europe"),
    ("tr1",  "europe"),
    ("ru",   "europe"),
    ("kr",   "asia"),
    ("jp1",  "asia"),
    ("oc1",  "sea"),
    ("ph2",  "sea"),
    ("sg2",  "sea"),
    ("th2",  "sea"),
    ("tw2",  "sea"),
    ("vn2",  "sea"),
])
def test_regional_route_for_known_platforms(platform, expected):
    assert regional_route_for(platform) == expected


def test_regional_route_for_unknown_platform_falls_back():
    # Don't crash on garbage; default to americas so we still try something.
    assert regional_route_for("zzz9") == "americas"
    assert regional_route_for("") == "americas"
    assert regional_route_for(None) == "americas"  # type: ignore[arg-type]


def test_regional_route_for_is_case_insensitive():
    assert regional_route_for("KR") == "asia"
    assert regional_route_for("EUW1") == "europe"


# ---------- URL builders ----------

def test_account_v1_url():
    assert account_v1_url("Faker", "KR1", "asia") == (
        "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
    )


def test_summoner_v4_url():
    assert summoner_v4_url("PUUID-XYZ", "kr") == (
        "https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/PUUID-XYZ"
    )


def test_league_v4_url():
    assert league_v4_url("SID-123", "na1") == (
        "https://na1.api.riotgames.com/lol/league/v4/entries/by-summoner/SID-123"
    )


def test_league_v4_by_puuid_url():
    # The endpoint we actually use (avoids the deprecated summoner-id step).
    assert league_v4_by_puuid_url("PUUID-XYZ", "kr") == (
        "https://kr.api.riotgames.com/lol/league/v4/entries/by-puuid/PUUID-XYZ"
    )


def test_url_builders_handle_special_chars():
    # Riot game names can contain spaces (e.g. "Hide on bush"). The URL just
    # passes them through; requests will percent-encode at send time.
    url = account_v1_url("Hide on bush", "KR1", "asia")
    assert "Hide on bush" in url


# ---------- cache freshness ----------

def test_is_cache_fresh_no_timestamp():
    a = Account()
    assert a.cached_at is None
    assert is_cache_fresh(a) is False


def test_is_cache_fresh_recent():
    a = Account(cached_at=time.time() - 60)  # 1 minute ago
    assert is_cache_fresh(a, ttl_seconds=3600) is True


def test_is_cache_fresh_expired():
    a = Account(cached_at=time.time() - 7200)  # 2 hours ago
    assert is_cache_fresh(a, ttl_seconds=3600) is False


def test_is_cache_fresh_custom_ttl():
    # Same age, different TTL.
    a = Account(cached_at=time.time() - 30)
    assert is_cache_fresh(a, ttl_seconds=60) is True
    assert is_cache_fresh(a, ttl_seconds=10) is False
