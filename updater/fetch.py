import hashlib

import requests
import trafilatura


def fetch_and_extract(url: str, timeout: int = 15, user_agent: str = "Mozilla/5.0") -> tuple[str | None, str | None]:
    """Fetch URL and extract main text. Returns (text, error) — one will be None."""
    try:
        r = requests.get(url, headers={"User-Agent": user_agent}, timeout=timeout)
        r.raise_for_status()
        text = trafilatura.extract(r.text) or ""
        if not text:
            return None, "trafilatura extracted no content"
        return text, None
    except requests.HTTPError as e:
        return None, f"HTTP {e.response.status_code}"
    except Exception as e:
        return None, str(e)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
