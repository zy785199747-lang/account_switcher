# URLs to third-party community sites for an Account.
#
# Right-click on a card -> "Open op.gg page" passes through here. Kept as a
# free function (no class) so it's trivially unit-testable: pass an Account,
# get a URL string back.
#
# Region mapping is the awkward part — op.gg uses friendlier region codes
# than the platform codes Riot's API returns ("na" vs "na1", "eune" vs
# "eun1"). PLATFORM_TO_OPGG_REGION pins down the translation; unknowns fall
# back to "na" so a typo can't break the click.

import logging
import urllib.parse

from src.models import Account

log = logging.getLogger(__name__)

# Riot platform code -> op.gg region slug.
# Source: https://www.op.gg/ supports these region paths today.
PLATFORM_TO_OPGG_REGION = {
    "na1":  "na",
    "euw1": "euw",
    "eun1": "eune",
    "kr":   "kr",
    "jp1":  "jp",
    "br1":  "br",
    "la1":  "lan",
    "la2":  "las",
    "oc1":  "oce",
    "tr1":  "tr",
    "ru":   "ru",
    "ph2":  "ph",
    "sg2":  "sg",
    "th2":  "th",
    "tw2":  "tw",
    "vn2":  "vn",
}

OPGG_FALLBACK_REGION = "na"
OPGG_BASE = "https://www.op.gg/lol/summoners"


def opgg_summoner_url(account: Account) -> str:
    # Builds the canonical op.gg summoner URL.
    # Shape: https://www.op.gg/lol/summoners/<region>/<GameName>-<TagLine>
    # Both name and tag are URL-encoded so spaces, accents, and the like
    # round-trip cleanly. op.gg is happy with %20 in the path.
    region = PLATFORM_TO_OPGG_REGION.get(
        (account.region or "").lower(),
        OPGG_FALLBACK_REGION,
    )
    name = urllib.parse.quote(account.game_name or "", safe="")
    tag = urllib.parse.quote(account.tag_line or "", safe="")
    url = f"{OPGG_BASE}/{region}/{name}-{tag}"
    log.debug("opgg url for %s#%s -> %s",
              account.game_name, account.tag_line, url)
    return url
