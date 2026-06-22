"""Pure derivations for VALTrack.

These take plain rows or lists and return computed figures with no database
access, so they are cheap to unit test against known inputs. The later
must-aggregate steps (side splits, pistol, opening duels) can add their pure
logic here too.
"""


def _rate(won, total):
    """A win rate as a 0..1 float, or None when there is nothing to divide.

    A None rate is honest: it says we have no rounds (or no decided maps) to
    judge a side on, rather than printing a misleading 0%.
    """
    return won / total if total else None

# Role text that marks a non-playing staff member. The stored is_staff flag is
# unusable (it comes back 0 for everyone) and the role text is sometimes mangled
# (a stand-in can show up as "Wongstand-in"), so we match on substrings and
# accept that the split is best effort.
_STAFF_MARKERS = ("coach", "manager", "analyst", "staff", "owner", "director")
_SUB_MARKERS = ("stand-in", "standin", "substitute")


def form_and_streak(results, n=5):
    """Summarize recent results into a short form list and the current streak.

    `results` is decided outcomes ordered newest first, each "W" or "L". The
    caller filters out ties and undecided matches, so it controls what counts.
    Returns a dict:
      - form: the most recent n results, still newest first
      - streak_kind: "W", "L", or None when there are no results
      - streak_len: how many of the most recent results share that kind
      - decided: how many results were supplied (the sample size)
    """
    streak_kind = results[0] if results else None
    streak_len = 0
    for r in results:
        if r == streak_kind:
            streak_len += 1
        else:
            break
    return {
        "form": results[:n],
        "streak_kind": streak_kind,
        "streak_len": streak_len,
        "decided": len(results),
    }


def classify_roster(rows):
    """Split a stored roster into the current five, stand-ins, and staff.

    Each row needs alias, real_name, role, and is_captain. Classification leans
    on the role text because the stored is_staff flag is unusable. A blank role
    is a main player. This is heuristic and can misplace someone when VLR's role
    text is odd, which the UI surfaces rather than hides.
    """
    mains, subs, staff = [], [], []
    for row in rows:
        role = (row["role"] or "").strip()
        folded = role.casefold()
        member = {
            "alias": row["alias"],
            "real_name": row["real_name"],
            "role": role,
            "is_captain": bool(row["is_captain"]),
        }
        if any(m in folded for m in _STAFF_MARKERS):
            staff.append(member)
        elif any(m in folded for m in _SUB_MARKERS):
            subs.append(member)
        else:
            mains.append(member)
    return {"mains": mains, "subs": subs, "staff": staff}


def map_winrates(map_rows, team_name):
    """Per-map win record for one team from stored map results.

    Each row needs map_name, winner_name, team1_name, and team2_name. The caller
    scopes the rows to maps this team played, so every row counts toward one map.
    A map with no winner_name (a forfeit or unplayed map) is not decided, so it
    adds to neither wins nor losses and drops out of the win rate denominator.

    Returns a dict keyed by map name, each value {won, lost, winrate}, where
    winrate is over decided maps (None when the team has no decided map there).
    """
    out = {}
    for row in map_rows:
        name = row["map_name"]
        if name is None:
            continue
        agg = out.setdefault(name, {"won": 0, "lost": 0})
        winner = row["winner_name"]
        if winner is None:
            continue
        if winner == team_name:
            agg["won"] += 1
        else:
            agg["lost"] += 1
    for agg in out.values():
        agg["winrate"] = _rate(agg["won"], agg["won"] + agg["lost"])
    return out


def side_winrates(round_rows, team_name):
    """Per-map attack and defense round win rates for one team.

    Each row needs map_name, winner_side ("atk" or "def"), and winner_team. The
    rounds table stores only the winner of each round, but the two teams are
    always on opposite sides, so a team's full side record follows from the
    winners alone:

      - a round this team won on attack is an attack round won,
      - a round the opponent won on defense is an attack round this team lost
        (the opponent defending means this team was attacking),

    and the mirror for defense. The caller scopes the rows to maps this team
    played, so "winner_team is not this team" is unambiguously the opponent.

    Returns a dict keyed by map name, each value with attack and defense wins,
    totals, and rates ({atk_won, atk_total, atk_winrate, def_won, def_total,
    def_winrate}); a rate is None when that side has no rounds.
    """
    out = {}
    for row in round_rows:
        name = row["map_name"]
        if name is None:
            continue
        side = row["winner_side"]
        if side not in ("atk", "def"):
            continue
        agg = out.setdefault(
            name, {"atk_won": 0, "atk_lost": 0, "def_won": 0, "def_lost": 0}
        )
        won_by_team = row["winner_team"] == team_name
        if side == "atk" and won_by_team:
            agg["atk_won"] += 1
        elif side == "def" and not won_by_team:
            agg["atk_lost"] += 1
        elif side == "def" and won_by_team:
            agg["def_won"] += 1
        else:  # side == "atk" and not won_by_team
            agg["def_lost"] += 1
    result = {}
    for name, agg in out.items():
        atk_total = agg["atk_won"] + agg["atk_lost"]
        def_total = agg["def_won"] + agg["def_lost"]
        result[name] = {
            "atk_won": agg["atk_won"],
            "atk_total": atk_total,
            "atk_winrate": _rate(agg["atk_won"], atk_total),
            "def_won": agg["def_won"],
            "def_total": def_total,
            "def_winrate": _rate(agg["def_won"], def_total),
        }
    return result


def per_map_splits(map_rows, round_rows, team_name):
    """Combine the map win record and side splits into one per-map table.

    Returns a list of per-map dicts ready for display, each carrying the map
    name, the win-loss record and map win rate, the attack and defense win rates
    with their round counts, and the total decided rounds. Maps are ordered by
    how many decided maps the team has on them (most first), then by name, so the
    maps the team plays most sit at the top.
    """
    maps = map_winrates(map_rows, team_name)
    sides = side_winrates(round_rows, team_name)
    rows = []
    for name in maps.keys() | sides.keys():
        m = maps.get(name, {"won": 0, "lost": 0, "winrate": None})
        s = sides.get(
            name,
            {"atk_won": 0, "atk_total": 0, "atk_winrate": None,
             "def_won": 0, "def_total": 0, "def_winrate": None},
        )
        rows.append({
            "map_name": name,
            "won": m["won"],
            "lost": m["lost"],
            "map_winrate": m["winrate"],
            "atk_won": s["atk_won"],
            "atk_total": s["atk_total"],
            "atk_winrate": s["atk_winrate"],
            "def_won": s["def_won"],
            "def_total": s["def_total"],
            "def_winrate": s["def_winrate"],
            "rounds_total": s["atk_total"] + s["def_total"],
        })
    rows.sort(key=lambda r: (-(r["won"] + r["lost"]), r["map_name"]))
    return rows
