# Tests for op.gg URL builder.

import pytest

from src.external_links import OPGG_FALLBACK_REGION, opgg_summoner_url
from src.models import Account


def make_account(region="na1", game_name="Faker", tag_line="KR1"):
    return Account(
        username="u", password="p",
        game_name=game_name, tag_line=tag_line, region=region,
    )


def test_basic_na_url():
    url = opgg_summoner_url(make_account(region="na1",
                                         game_name="Doublelift",
                                         tag_line="NA1"))
    assert url == "https://www.op.gg/lol/summoners/na/Doublelift-NA1"


def test_kr_region_mapping():
    url = opgg_summoner_url(make_account(region="kr",
                                         game_name="Faker",
                                         tag_line="KR1"))
    assert url == "https://www.op.gg/lol/summoners/kr/Faker-KR1"


def test_eune_friendly_slug_not_platform_code():
    # Riot platform code is "eun1" but op.gg uses "eune".
    url = opgg_summoner_url(make_account(region="eun1",
                                         game_name="Player",
                                         tag_line="EUNE"))
    assert "/eune/" in url
    assert "/eun1/" not in url


@pytest.mark.parametrize("riot_code,opgg_slug", [
    ("na1", "na"),
    ("euw1", "euw"),
    ("eun1", "eune"),
    ("kr", "kr"),
    ("jp1", "jp"),
    ("br1", "br"),
    ("la1", "lan"),
    ("la2", "las"),
    ("oc1", "oce"),
    ("tr1", "tr"),
    ("ru", "ru"),
    ("ph2", "ph"),
    ("sg2", "sg"),
    ("th2", "th"),
    ("tw2", "tw"),
    ("vn2", "vn"),
])
def test_every_supported_region_maps(riot_code, opgg_slug):
    url = opgg_summoner_url(make_account(region=riot_code))
    assert f"/{opgg_slug}/" in url


def test_unknown_region_falls_back_to_na():
    url = opgg_summoner_url(make_account(region="nonsense"))
    assert f"/{OPGG_FALLBACK_REGION}/" in url


def test_region_is_case_insensitive():
    url_lower = opgg_summoner_url(make_account(region="kr"))
    url_upper = opgg_summoner_url(make_account(region="KR"))
    assert url_lower == url_upper


def test_special_chars_in_game_name_get_encoded():
    # Riot IDs allow spaces and unicode. URL must encode them so the browser
    # gets a clean path.
    url = opgg_summoner_url(make_account(region="na1",
                                         game_name="my name",
                                         tag_line="NA1"))
    assert "my%20name" in url
    assert " " not in url


def test_special_chars_in_tag_line_get_encoded():
    url = opgg_summoner_url(make_account(region="na1",
                                         game_name="Player",
                                         tag_line="tag with space"))
    assert "tag%20with%20space" in url


def test_unicode_round_trips():
    url = opgg_summoner_url(make_account(region="kr",
                                         game_name="페이커",
                                         tag_line="KR1"))
    # Should be percent-encoded UTF-8, not raw unicode in the URL.
    assert "%" in url
    # Decoded form should round-trip.
    import urllib.parse
    decoded = urllib.parse.unquote(url)
    assert "페이커" in decoded


def test_empty_game_name_does_not_crash():
    # Edge case: half-built account from somewhere. Should still produce a
    # URL — op.gg will 404 but we don't crash the click handler.
    url = opgg_summoner_url(make_account(game_name="", tag_line=""))
    assert url.startswith("https://www.op.gg/lol/summoners/")
