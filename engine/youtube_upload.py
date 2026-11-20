"""YouTube Data API v3 upload — PREPARED BUT OFF.

Turn on: config.yaml -> youtube.auto_upload: true, add secrets YT_CLIENT_ID, YT_CLIENT_SECRET,
YT_REFRESH_TOKEN (get the refresh token once with scripts/get_youtube_token.py), and switch
"Автозагрузка на YouTube" on in the web panel settings.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from .fetch import UserError
from .util import config


def _access_token() -> str:
    need = ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")
    if not all(os.environ.get(k) for k in need):
        raise UserError("Автозагрузка на YouTube не настроена (нет ключей YT_*). Выложи файл вручную.")
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def upload(path: Path, title: str, description: str, tags: list[str]) -> str:
    cfg = config()["youtube"]
    if not cfg.get("auto_upload"):
        raise UserError("Автозагрузка на YouTube выключена в config.yaml.")
    tok = _access_token()
    meta = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:15],
                    "categoryId": str(cfg.get("category_id", "20")), "defaultLanguage": "en"},
        "status": {"privacyStatus": cfg.get("privacy", "public"), "selfDeclaredMadeForKids": False},
    }
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(path.stat().st_size)},
        data=json.dumps(meta), timeout=60)
    init.raise_for_status()
    up = requests.put(init.headers["Location"], data=path.read_bytes(),
                      headers={"Authorization": f"Bearer {tok}", "Content-Type": "video/mp4"}, timeout=900)
    up.raise_for_status()
    return up.json()["id"]
