"""The fixed set of VCT franchise teams across the four international leagues.

Rankings endpoints list every ranked team in a region, franchise and challengers
alike, so there is no franchise-only feed. This curated seed names exactly the
franchise teams and carries each team's VLR.gg id directly. Ids are baked in
rather than resolved by search at harvest time, because search matching is
brittle against sponsor prefixes (DRX shows as "KIWOOM DRX") and inactive
suffixes (eg "Apeks (inactive since ...)"). The ids here were verified against
the live search endpoint.

Leagues map to rankings region codes: americas->na, emea->eu, pacific->ap,
china->cn.
"""

LEAGUE_REGION = {
    "americas": "na",
    "emea": "eu",
    "pacific": "ap",
    "china": "cn",
}

# league -> list of (display name, VLR.gg team id)
FRANCHISE_TEAMS = {
    "americas": [
        ("Sentinels", 2),
        ("NRG", 1034),
        ("100 Thieves", 120),
        ("Cloud9", 188),
        ("Evil Geniuses", 5248),
        ("G2 Esports", 11058),
        ("LOUD", 6961),
        ("MIBR", 7386),
        ("KRÜ Esports", 2355),
        ("Leviatán", 2359),
        ("FURIA", 2406),
        ("2Game Esports", 15072),
    ],
    "emea": [
        ("FNATIC", 2593),
        ("Team Liquid", 474),
        ("Team Heretics", 1001),
        ("Karmine Corp", 8877),
        ("FUT Esports", 1184),
        ("Natus Vincere", 4915),
        ("BBL Esports", 397),
        ("KOI", 7035),
        ("Gentle Mates", 12694),
        ("Team Vitality", 2059),
        ("GIANTX", 14419),
        ("Apeks", 11479),
    ],
    "pacific": [
        ("Paper Rex", 624),
        ("DRX", 8185),
        ("T1", 14),
        ("Gen.G", 17),
        ("Talon Esports", 8304),
        ("Team Secret", 6199),
        ("Global Esports", 918),
        ("Rex Regum Qeon", 878),
        ("ZETA DIVISION", 5448),
        ("DetonatioN FocusMe", 278),
        ("Nongshim RedForce", 11060),
        ("BOOM Esports", 466),
    ],
    "china": [
        ("EDward Gaming", 1120),
        ("Bilibili Gaming", 12010),
        ("FunPlus Phoenix", 11328),
        ("Trace Esports", 12685),
        ("Dragon Ranger Gaming", 11981),
        ("Wolves Esports", 13790),
        ("JDG Esports", 13576),
        ("TYLOO", 731),
        ("Titan Esports Club", 14137),
        ("Nova Esports", 12064),
        ("All Gamers", 1119),
        ("Xi Lai Gaming", 13581),
    ],
}


def iter_franchise_teams():
    """Yield (league, region, name, team_id) for every franchise team."""
    for league, teams in FRANCHISE_TEAMS.items():
        region = LEAGUE_REGION[league]
        for name, team_id in teams:
            yield league, region, name, team_id
