# vlrggapi clone patches

VALTrack runs a self-hosted clone of [vlrggapi](https://github.com/axsddlr/vlrggapi)
as its data source. The clone needs a few local changes that VALTrack depends on,
and because the clone lives outside this repo (it is gitignored), those changes are
lost whenever it is re-cloned or updated. This file is the record so they can be
re-applied in one step.

`clone-patches.diff` contains three changes:

- `api/scrapers/match_detail.py`: the per-map economy block selection (read the
  `game=all` page and pick each map's own block by game id), the round win type
  from the round icon, the picked-by-team class, and stripping the team tag from
  the performance-tab player name.
- `api/utils/rate_limiter.py`: raise all tiers to 600 requests per minute, to match
  the self-hosted limit. VALTrack's own client delay is the politeness control.
- `tests/test_match_detail_scraper.py`: the matching fixture and assertion update.

## Base commit

The diff was generated against upstream vlrggapi commit
`a6075fec9757ae5394c9a50002ba40270f4a5d00` (origin https://github.com/axsddlr/vlrggapi.git).
Apply it on that commit for a clean result. A later upstream may need a manual merge.

## Applying after a fresh clone

From inside the cloned `vlrggapi/` directory:

    git apply ../vlrggapi-patches/clone-patches.diff

Then verify the scraper test still passes:

    .venv/Scripts/python.exe -m pytest tests/test_match_detail_scraper.py

If `git apply` reports the patch does not apply (upstream moved on), re-apply the
four changes by hand using the diff as the guide, then regenerate this file with
`cd vlrggapi && git diff > ../vlrggapi-patches/clone-patches.diff`.
