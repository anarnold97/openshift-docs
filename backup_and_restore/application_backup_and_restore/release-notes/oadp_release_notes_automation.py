#!/usr/bin/env python3
"""
OADP release notes automation: fetch JIRA issues from filters and send to RENOA.

Usage:
  Set JIRA_USER and JIRA_API_TOKEN in the environment.
  You can set JIRA filter IDs in config/env, or the script will prompt you for them.
  Run: python oadp_release_notes_automation.py
"""

import os
import sys
import json
import argparse

try:
    import requests
except ImportError:
    print("Install requests: pip install requests", file=sys.stderr)
    sys.exit(1)

# -----------------------------------------------------------------------------
# Config (edit these)
# -----------------------------------------------------------------------------

# Add your RENOA endpoint when ready (e.g. https://renoa.example.com/api/generate)
RENOA_URL = os.environ.get("RENOA_URL", "")

# JIRA base URL (Red Hat uses issues.redhat.com)
JIRA_BASE_URL = os.environ.get("JIRA_BASE_URL", "https://issues.redhat.com").rstrip("/")

# Filter IDs from JIRA (optional): set here or in env, or you will be prompted at runtime.
# Find filter ID in the filter's URL: .../issues/?filter=12345 -> 12345
JIRA_FILTER_RESOLVED_ID = os.environ.get("OADP_JIRA_FILTER_RESOLVED", "")
JIRA_FILTER_KNOWN_ID = os.environ.get("OADP_JIRA_FILTER_KNOWN", "")

# JIRA auth: set JIRA_USER and JIRA_API_TOKEN (or JIRA_API_KEY) in env
JIRA_USER = os.environ.get("JIRA_USER", "")
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN") or os.environ.get("JIRA_API_KEY", "")

# -----------------------------------------------------------------------------
# JIRA API
# -----------------------------------------------------------------------------


def jira_request(method: str, path: str, **kwargs) -> requests.Response:
    """Call JIRA REST API with auth."""
    url = f"{JIRA_BASE_URL}{path}"
    auth = None
    if JIRA_USER and JIRA_API_TOKEN:
        auth = (JIRA_USER, JIRA_API_TOKEN)
    return requests.request(method, url, auth=auth, timeout=30, **kwargs)


def get_filter_jql(filter_id: str) -> str:
    """Return JQL for a saved filter."""
    r = jira_request("GET", f"/rest/api/2/filter/{filter_id}")
    r.raise_for_status()
    return r.json().get("jql", "")


def fetch_issues_from_filter(filter_id: str) -> list[dict]:
    """Fetch issues matching the given JIRA filter. Returns list of {key, summary, link}."""
    if not filter_id:
        return []
    jql = get_filter_jql(filter_id)
    if not jql:
        return []
    issues = []
    start = 0
    page_size = 50
    while True:
        r = jira_request(
            "GET",
            "/rest/api/2/search",
            params={"jql": jql, "startAt": start, "maxResults": page_size, "fields": "summary"},
        )
        r.raise_for_status()
        data = r.json()
        for item in data.get("issues", []):
            key = item.get("key", "")
            summary = (item.get("fields") or {}).get("summary", "")
            link = f"{JIRA_BASE_URL}/browse/{key}" if key else ""
            issues.append({"key": key, "summary": summary, "link": link})
        start += page_size
        if start >= data.get("total", 0):
            break
    return issues


# -----------------------------------------------------------------------------
# RENOA
# -----------------------------------------------------------------------------


def filter_url(filter_id: str) -> str:
    """Return the shareable JIRA filter URL for the given filter ID."""
    if not filter_id:
        return ""
    return f"{JIRA_BASE_URL}/issues/?filter={filter_id}"


def send_to_renoa(
    release: str,
    resolved: list[dict],
    known: list[dict],
    resolved_filter_id: str = "",
    known_filter_id: str = "",
) -> bool:
    """POST release number, ticket numbers, and filter URLs to RENOA. Returns True on success."""
    if not RENOA_URL:
        print("RENOA_URL is not set. Skipping RENOA request.")
        print("Payload that would be sent:")
        payload = build_payload(
            release, resolved, known, resolved_filter_id, known_filter_id
        )
        print(json.dumps(payload, indent=2))
        return False

    payload = build_payload(
        release, resolved, known, resolved_filter_id, known_filter_id
    )
    r = requests.post(RENOA_URL, json=payload, timeout=60)
    r.raise_for_status()
    return True


def build_payload(
    release: str,
    resolved: list[dict],
    known: list[dict],
    resolved_filter_id: str = "",
    known_filter_id: str = "",
) -> dict:
    """Build the payload for RENOA: release, JIRA ticket numbers, and optional filter URLs."""
    payload = {
        "release": release,
        "resolved_issues": [i["key"] for i in resolved if i.get("key")],
        "known_issues": [i["key"] for i in known if i.get("key")],
    }
    resolved_url = filter_url(resolved_filter_id)
    known_url = filter_url(known_filter_id)
    if resolved_url:
        payload["resolved_filter_url"] = resolved_url
    if known_url:
        payload["known_filter_url"] = known_url
    return payload


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch OADP JIRA issues and send to RENOA")
    parser.add_argument(
        "--release",
        "-r",
        type=str,
        default=None,
        help="OADP release number (e.g. 1.5.5). If not set, will prompt.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only fetch and print issues; do not call RENOA",
    )
    parser.add_argument(
        "--resolved-filter",
        type=str,
        default=JIRA_FILTER_RESOLVED_ID,
        help="JIRA filter ID for resolved issues (overrides config)",
    )
    parser.add_argument(
        "--known-filter",
        type=str,
        default=JIRA_FILTER_KNOWN_ID,
        help="JIRA filter ID for known issues (overrides config)",
    )
    args = parser.parse_args()

    release = args.release
    if not release:
        release = input("Enter OADP release number (e.g. 1.5.5): ").strip()
    if not release:
        print("Release number is required.", file=sys.stderr)
        sys.exit(1)

    if not JIRA_USER or not JIRA_API_TOKEN:
        print("Set JIRA_USER and JIRA_API_TOKEN (or JIRA_API_KEY) in the environment.", file=sys.stderr)
        sys.exit(1)

    resolved_id = (args.resolved_filter or JIRA_FILTER_RESOLVED_ID or "").strip()
    known_id = (args.known_filter or JIRA_FILTER_KNOWN_ID or "").strip()

    if not resolved_id:
        resolved_id = input("JIRA filter ID for Resolved issues (or leave blank to skip): ").strip()
    if not known_id:
        known_id = input("JIRA filter ID for Known issues (or leave blank to skip): ").strip()

    if not resolved_id and not known_id:
        print("At least one JIRA filter ID is required (Resolved or Known issues).", file=sys.stderr)
        sys.exit(1)

    print(f"Release: {release}")
    print("Fetching Resolved issues...")
    resolved = fetch_issues_from_filter(resolved_id)
    print(f"  -> {len(resolved)} issues")
    print("Fetching Known issues...")
    known = fetch_issues_from_filter(known_id)
    print(f"  -> {len(known)} issues")

    if not resolved and not known:
        print("No issues found. Check filter IDs and JIRA credentials.", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("\n[DRY RUN] Payload that would be sent to RENOA:")
        print(
            json.dumps(
                build_payload(release, resolved, known, resolved_id, known_id),
                indent=2,
            )
        )
        return

    if send_to_renoa(release, resolved, known, resolved_id, known_id):
        print("Successfully sent data to RENOA.")
    else:
        print("RENOA URL not set; payload printed above.")


if __name__ == "__main__":
    main()
