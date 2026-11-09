#!/usr/bin/env python
"""Record the HTTP status the published report answers, at generation time.

The report says whether GitHub Pages is serving the site or answers 404, and
that claim is measured rather than assumed: this script requests the URL and
stores the status it answers with the measured numbers, so the page, the
README and ``results/audit.json`` quote one artifact.

    python scripts/check_pages.py

Run it when the report is regenerated, so the status stored beside the numbers
is the one the site answered at generation time.

The probe URL is built from the account that owns this repository, read from the
``origin`` remote, so a typo cannot send the request to a host that does not
exist -- any nonexistent ``*.github.io`` host answers with the same 404 page as a
site nobody is serving. ``check_host`` refuses to run when the two disagree.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_NAME = "prompt-injection-rag-audit"
TIMEOUT_SECONDS = 20.0


def _remote_owner() -> str | None:
    """The account that owns this checkout, read from the ``origin`` remote."""

    try:
        done = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    remote = done.stdout.strip()
    if "://" not in remote:
        remote = "https://" + remote.replace(":", "/", 1)
    parsed = urllib.parse.urlparse(remote)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc.endswith("github.com") and parts:
        return parts[0]
    return None


def _pages_url() -> str:
    """The published report's URL for this repository.

    The host comes from the account that owns the checkout: GitHub serves every
    ``*.github.io`` host, and a misspelled one answers with the same 404 page as
    a site nobody is serving, which reads as an outage. ``PAGES_URL`` overrides
    it for a deliberate probe of another host.
    """

    override = os.environ.get("PAGES_URL")
    if override:
        return override
    owner = _remote_owner()
    if owner is None:
        raise SystemExit(
            "cannot read the account from the origin remote; refusing to guess a "
            "host, because a host nobody owns answers 404 and reads as an outage."
        )
    return f"https://{owner.lower()}.github.io/{REPO_NAME}/"


def check_host() -> str | None:
    """Refuse a URL whose host is not this repository's account.

    A one-letter typo in the host probes a URL that cannot exist, and that host
    answers the same 404 page as a site nobody is serving, so the host is checked
    against the account that owns the checkout before the request is made.
    """

    owner = _remote_owner()
    if owner is None:
        return None
    host = urllib.parse.urlparse(PAGES_URL).netloc
    expected = f"{owner.lower()}.github.io"
    if host != expected:
        raise SystemExit(
            f"PAGES_URL host {host!r} does not match the account {owner!r} that "
            f"owns this repository; expected {expected!r}."
        )
    return host


PAGES_URL = _pages_url()
TIMEOUT_SECONDS = 20.0


def probe_pages(url: str = PAGES_URL, timeout: float = TIMEOUT_SECONDS) -> dict:
    """One GET against the published report, with the status it answered.

    A 404 is an answer and is recorded as one; only a transport failure leaves
    the status unset, and that is recorded in ``error``.
    """

    entry = {
        "url": url,
        "http_status": None,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "error": None,
    }
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            entry["http_status"] = response.status
    except urllib.error.HTTPError as exc:
        entry["http_status"] = exc.code
    except OSError as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"
    return entry


def refresh(path: Path, url: str = PAGES_URL) -> dict:
    """Store a fresh probe in the record, leaving every measured number as is.

    The file is rewritten in the shape the audit writer emits, so a refresh
    changes this field and nothing else.
    """

    with open(path, encoding="utf-8", newline="") as handle:
        record = json.load(handle)
    record["pages_status"] = probe_pages(url)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")
    return record["pages_status"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", default=str(ROOT / "results" / "audit.json"))
    args = parser.parse_args()

    check_host()
    status = refresh(Path(args.record))
    url = status["url"]
    if status["error"] is not None:
        print(f"could not request {url}: {status['error']}")
        return 2
    print(
        f"{url} answered HTTP {status['http_status']} at {status['checked_at']}\n"
        f"recorded in {args.record}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
