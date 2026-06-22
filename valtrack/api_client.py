"""Thin client over the self-hosted vlrggapi instance.

All viewing happens off the local SQLite store, so this client is only used
during ingestion. It centralizes the base url, a polite delay between requests,
retries on transient failures, and unwrapping the v2 response envelope.
"""
import time

import requests

BASE_URL = "http://127.0.0.1:3001"


class ApiError(Exception):
    """Raised when the API cannot satisfy a request after retries."""


class VlrClient:
    def __init__(self, base_url=BASE_URL, request_delay=1.5, timeout=30, max_retries=3):
        # request_delay is the polite pause before each call, to be gentle on
        # VLR.gg during large pulls rather than to respect a local rate limit.
        self.base_url = base_url.rstrip("/")
        self.request_delay = request_delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    def _get(self, path, params=None):
        url = f"{self.base_url}{path}"
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            time.sleep(self.request_delay)
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                # Back off a little more on each failed attempt.
                time.sleep(self.request_delay * attempt)
        raise ApiError(f"GET {path} params={params} failed after "
                       f"{self.max_retries} attempts: {last_error}")

    def _get_v2_data(self, path, params=None):
        """Call a v2 endpoint and return the inner data payload.

        v2 wraps everything as {"status": "success", "data": {...}}. We unwrap
        to the data object and let callers reach into segments.
        """
        payload = self._get(path, params)
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise ApiError(f"GET {path} returned an unexpected envelope: {payload}")
        return payload.get("data", {})

    def search(self, query):
        """Search for teams, players, and events by keyword."""
        return self._get_v2_data("/v2/search", {"q": query})

    def rankings(self, region):
        """Team rankings for a region code (na, eu, ap, cn, ...)."""
        return self._get_v2_data("/v2/rankings", {"region": region})

    def team_profile(self, team_id):
        """Identity, roster, rating, placements, and winnings for a team."""
        return self._get_v2_data("/v2/team", {"id": str(team_id), "q": "profile"})

    def team_matches(self, team_id, page=1):
        """One page of a team's series-level match history."""
        return self._get_v2_data(
            "/v2/team", {"id": str(team_id), "q": "matches", "page": page}
        )

    def match_detail(self, match_id):
        """Full per-match detail: per-map scores, player stats, rounds, vetos.

        Returns the single detail segment, the object the parser consumes. The
        v2 envelope wraps it as data.segments[0]; an empty segments list raises
        so a caller never parses an empty match as a real one.
        """
        data = self._get_v2_data(
            "/v2/match/details", {"match_id": str(match_id)}
        )
        segments = data.get("segments", [])
        if not segments:
            raise ApiError(f"match {match_id} returned no detail segment")
        return segments[0]
