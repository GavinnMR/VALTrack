"""Pure derivations for VALTrack.

These take plain rows or lists and return computed figures with no database
access, so they are cheap to unit test against known inputs. The later
must-aggregate steps (side splits, pistol, opening duels) can add their pure
logic here too.
"""

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
