"""Snapshot GitHub billing usage for one user into a private repo.

Runs in a PUBLIC repo (so its Actions minutes are free), which means its logs
are public too: it never prints figures, repo names or response bodies -
only HTTP status codes, exception type names and "ok".
"""
import base64, datetime as dt, json, os, sys, time, urllib.error, urllib.request

USER = "coop1st"
DATA_REPO = "coop1st/gh-usage-data"
TOKEN = os.environ.get("USAGE_TOKEN", "")
EXPIRY_HEADER = "github-authentication-token-expiration"


def call(url, method="GET", body=None, auth=True):
    """Return (status, json, headers). Retries network errors and 5xx/429."""
    for attempt in range(4):
        req = urllib.request.Request(url, method=method,
                                     data=json.dumps(body).encode() if body else None)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if auth:
            req.add_header("Authorization", f"Bearer {TOKEN}")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"null"), r.headers
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:
                return e.code, None, e.headers
            status = e.code
        except (urllib.error.URLError, TimeoutError) as e:
            status = type(e).__name__
        if attempt < 3:
            time.sleep(10 * (attempt + 1))
    return status, None, {}


def month_data(year, month):
    base = f"https://api.github.com/users/{USER}/settings/billing/usage"
    s1, summary, h = call(f"{base}/summary?year={year}&month={month}")
    s2, usage, _ = call(f"{base}?year={year}&month={month}")
    if s1 != 200 or s2 != 200:
        sys.exit(f"billing API failed: summary={s1} usage={s2}")
    return {"summary": summary, "usage": usage}, h.get(EXPIRY_HEADER)


def main():
    if not TOKEN:
        sys.exit("USAGE_TOKEN secret is not set")
    now = dt.datetime.now(dt.timezone.utc)
    current, expiry = month_data(now.year, now.month)
    snap = {"fetched_at": now.isoformat(timespec="seconds"),
            "token_expires": expiry, "current": current}
    if now.day <= 7:
        prev = now.replace(day=1) - dt.timedelta(days=1)
        snap["previous"], _ = month_data(prev.year, prev.month)

    # Public repos are free; anything not listed here is treated as private.
    s, repos, _ = call(f"https://api.github.com/users/{USER}/repos?per_page=100&type=owner",
                       auth=False)
    if s != 200:
        sys.exit(f"public repo list failed: {s}")
    snap["public_repos"] = sorted(r["name"] for r in repos)

    content = base64.b64encode(json.dumps(snap, indent=1).encode()).decode()
    for path in ("latest.json", f"snapshots/{now:%Y-%m-%d}.json"):
        url = f"https://api.github.com/repos/{DATA_REPO}/contents/{path}"
        s, existing, _ = call(url)
        if s not in (200, 404):
            sys.exit(f"read {path} failed: {s}")
        body = {"message": f"usage snapshot {now:%Y-%m-%d %H:%M}Z", "content": content,
                "committer": {"name": "gh-usage-fetcher",
                              "email": "124205316+coop1st@users.noreply.github.com"}}
        if s == 200:
            body["sha"] = existing["sha"]
        s, _, _ = call(url, "PUT", body)
        if s not in (200, 201):
            sys.exit(f"write {path} failed: {s}")
    print("ok")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # never let a traceback put data in the public log
        sys.exit(f"failed: {type(e).__name__}")
