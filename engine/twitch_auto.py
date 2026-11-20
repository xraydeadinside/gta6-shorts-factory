"""Automatic clip discovery through the Twitch Helix API — PREPARED BUT OFF.

Turn on later (needs a Twitch developer app, which needs a verified phone):
  1. dev.twitch.tv/console -> Register application -> get Client ID + Secret.
  2. Add GitHub secrets TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET.
  3. config.yaml -> twitch_auto.enabled: true and fill twitch_auto.streamers.
  4. GitHub -> Settings -> Variables -> TWITCH_AUTO = true (enables the schedule in twitch-auto.yml).
New popular GTA 6 clips are then sent to the Worker, which creates jobs exactly like /clip.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import requests

from .notify import Worker
from .util import config

HELIX = "https://api.twitch.tv/helix"


def _token() -> str:
    r = requests.post("https://id.twitch.tv/oauth2/token", params={
        "client_id": os.environ["TWITCH_CLIENT_ID"], "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
        "grant_type": "client_credentials"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def find_clips() -> list[dict]:
    cfg = config()["twitch_auto"]
    h = {"Client-Id": os.environ["TWITCH_CLIENT_ID"], "Authorization": f"Bearer {_token()}"}
    game = requests.get(f"{HELIX}/games", params={"name": cfg["game_name"]}, headers=h, timeout=30).json()
    game_id = (game.get("data") or [{}])[0].get("id")
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=cfg["lookback_hours"])).isoformat()
    out = []
    for login in cfg["streamers"]:
        u = requests.get(f"{HELIX}/users", params={"login": login}, headers=h, timeout=30).json().get("data") or []
        if not u:
            continue
        clips = requests.get(f"{HELIX}/clips", params={"broadcaster_id": u[0]["id"], "started_at": since,
                                                       "first": 20}, headers=h, timeout=30).json().get("data") or []
        for c in clips:
            if game_id and c.get("game_id") != game_id:
                continue
            if c.get("view_count", 0) >= cfg["min_views"] and c.get("language", "en").startswith("en"):
                out.append({"url": c["url"], "note": f"автопоиск: {c['view_count']} просмотров"})
    return out


def main() -> int:
    if not config()["twitch_auto"].get("enabled"):
        print("twitch_auto is disabled in config.yaml")
        return 0
    clips = find_clips()
    if clips:
        Worker().post("/hook/spawn", {"job_id": 0, "moments": clips, "dedupe": True})
    print(f"found {len(clips)} clips")
    return 0


if __name__ == "__main__":
    sys.exit(main())
