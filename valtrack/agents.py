"""Static Valorant agent to role reference.

VLR does not expose a player's in-game role, only which agents they played. So
the player-versus-player view infers a player's role from their
agent usage, which needs this fixed agent to role table. This is plain reference
data, like the franchise team list, kept in its own module.

Keep this updated when Riot adds an agent: a missing agent maps to no role, so
that player falls into the "unknown" bucket rather than being misplaced.
"""

# Roles as VLR and the community use them. Keyed by the lowercased agent name so
# the lookup is case insensitive and tolerant of how the source spells them.
_ROLE_BY_AGENT = {
    # Duelists
    "jett": "duelist",
    "raze": "duelist",
    "reyna": "duelist",
    "phoenix": "duelist",
    "yoru": "duelist",
    "neon": "duelist",
    "iso": "duelist",
    "waylay": "duelist",
    # Initiators
    "sova": "initiator",
    "breach": "initiator",
    "skye": "initiator",
    "kay/o": "initiator",
    "kayo": "initiator",
    "fade": "initiator",
    "gekko": "initiator",
    "tejo": "initiator",
    # Controllers
    "brimstone": "controller",
    "omen": "controller",
    "viper": "controller",
    "astra": "controller",
    "harbor": "controller",
    "clove": "controller",
    # Sentinels
    "killjoy": "sentinel",
    "cypher": "sentinel",
    "sage": "sentinel",
    "chamber": "sentinel",
    "deadlock": "sentinel",
    "vyse": "sentinel",
}

# The order roles are presented in the comparison, with the catch-all last.
ROLE_ORDER = ["duelist", "initiator", "controller", "sentinel", "unknown"]


def agent_role(agent):
    """Return the role for an agent name, or None when it is not in the table.

    The lookup is case insensitive. A None result is honest: it says we do not
    know this agent's role (a new agent, or a blank value), so the caller can
    treat that player as unknown rather than guess.
    """
    if not agent:
        return None
    return _ROLE_BY_AGENT.get(agent.strip().casefold())
