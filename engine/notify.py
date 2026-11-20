"""Talk to Telegram (send the finished video) and to the Cloudflare Worker (job state)."""
from __future__ import annotations

import os
from pathlib import Path

import requests


def _tg(method: str) -> str:
    return f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{method}"


def send_video(path: Path, caption: str, thumb: Path | None = None, width: int = 1080, height: int = 1920,
               duration: float = 0) -> dict:
    files = {"video": (path.name, path.open("rb"), "video/mp4")}
    if thumb and thumb.exists():
        files["thumbnail"] = (thumb.name, thumb.open("rb"), "image/jpeg")
    r = requests.post(_tg("sendVideo"), data={
        "chat_id": os.environ["TELEGRAM_CHAT_ID"], "caption": caption[:1000], "supports_streaming": "true",
        "width": width, "height": height, "duration": int(duration), "parse_mode": "HTML",
        **({"thumbnail": "attach://thumbnail"} if "thumbnail" in files else {})},
        files=files, timeout=300)
    r.raise_for_status()
    msg = r.json()["result"]
    vid = msg.get("video") or msg.get("document") or {}
    return {"message_id": msg["message_id"], "file_id": vid.get("file_id")}


def send_text(text: str) -> None:
    try:
        requests.post(_tg("sendMessage"), json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text[:4000]},
                      timeout=30)
    except requests.RequestException:
        pass


def get_file(file_id: str, dest: Path) -> Path:
    r = requests.get(_tg("getFile"), params={"file_id": file_id}, timeout=30)
    r.raise_for_status()
    fp = r.json()["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{fp}"
    with requests.get(url, stream=True, timeout=300) as d:
        d.raise_for_status()
        with dest.open("wb") as f:
            for chunk in d.iter_content(1 << 20):
                f.write(chunk)
    return dest


class Worker:
    """The Cloudflare Worker keeps all state (D1). The engine only reports to it."""

    def __init__(self) -> None:
        self.base = os.environ.get("WORKER_URL", "").rstrip("/")
        self.secret = os.environ.get("HOOK_SECRET", "")

    def _h(self) -> dict:
        return {"X-Hook-Secret": self.secret, "Content-Type": "application/json"}

    def job(self, job_id: int) -> dict:
        r = requests.get(f"{self.base}/hook/job/{job_id}", headers=self._h(), timeout=30)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, data: dict) -> dict:
        if not self.base:
            return {}
        for _ in range(3):
            try:
                r = requests.post(f"{self.base}{path}", json=data, headers=self._h(), timeout=30)
                if r.status_code < 500:
                    return r.json() if r.content else {}
            except requests.RequestException:
                pass
        return {}

    def stage(self, job_id: int, text: str) -> None:
        self.post("/hook/update", {"job_id": job_id, "stage": text})
