"""Snapshot GitHub billing usage for one user into a private repo.

Runs in a public repo, so its logs are public: it never prints figures,
repo names or response bodies - only HTTP status codes and "ok".
"""
import base64, datetime as dt, json, os, sys, urllib.error, urllib.request

USER = "coop1st"
DATA_REPO = "coop1st/gh-usage-data"
TOKEN = os.environ["USAGE_TOKEN"]


def call(url, method="GET", body=None, auth=True):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body else None)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if auth:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def month_data(year, month):
    base = f"https://api.github.com/users/{USER}/settings/billing/usage"
    s1, summary = call(f"{base}/summary?year={year}&month={month}")
    s2, usage = call(f"{base}?year={year}&month={month}")
    if s1 != 200 or s2 != 200:
        sys.exit(f"billing API failed: summary={s1} usage={s2}")
    return {"summary": summary, "usage": usage}


now = dt.datetime.now(dt.timezone.utc)
snap = {"fetched_at": now.isoformat(timespec="seconds"),
        "current": month_data(now.year, now.month)}
if now.day <= 7:
    prev = (now.replace(day=1) - dt.timedelta(days=1))
    snap["previous"] = month_data(prev.year, prev.month)

# Public repos are free; everything not in this list counts as private.
s, repos = call(f"https://api.github.com/users/{USER}/repos?per_page=100&type=owner", auth=False)
if s != 200:
    sys.exit(f"public repo list failed: {s}")
snap["public_repos"] = sorted(r["name"] for r in repos)

content = base64.b64encode(json.dumps(snap, indent=1).encode()).decode()
for path in ("latest.json", f"snapshots/{now:%Y-%m-%d}.json"):
    url = f"https://api.github.com/repos/{DATA_REPO}/contents/{path}"
    s, existing = call(url)
    body = {"message": f"usage snapshot {now:%Y-%m-%d %H:%M}Z", "content": content,
            "committer": {"name": "gh-usage-fetcher",
                          "email": "124205316+coop1st@users.noreply.github.com"}}
    if s == 200:
        body["sha"] = existing["sha"]
    s, _ = call(url, "PUT", body)
    if s not in (200, 201):
        sys.exit(f"write {path} failed: {s}")
print("ok")
