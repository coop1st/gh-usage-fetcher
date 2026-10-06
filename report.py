"""Build the weekly usage email from a snapshot written by fetch_usage.py.

Usage: python3 report.py <gh-usage-data checkout> [--now ISO]
Prints one JSON object: subject, body, status, age_hours, notify (list of
reasons the user should get a push notification; empty on a normal week).
Doing the arithmetic here keeps the numbers identical from week to week.
"""
import calendar, datetime as dt, glob, json, os, sys
from zoneinfo import ZoneInfo

ALLOWANCE = 3000
STORAGE_GB = 2
WARN_AT = 2700
PRICE = 0.006
# Standard runners count against the allowance at these rates. Any other
# minute SKU (larger runners) is never covered by included minutes.
MULT = {"Actions Linux": 1, "Actions Windows": 2, "Actions macOS": 10}
DUB = ZoneInfo("Europe/Dublin")


def parse(ts):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def tally(month, public, since=None):
    """Return per-repo private/public minutes and other totals for a month."""
    repos, days, other, storage_gbh = {}, {}, 0.0, 0.0
    for i in month["usage"]["usageItems"]:
        sku, q = i["sku"], float(i["quantity"])
        day = i["date"][:10]
        if sku == "Actions storage":
            storage_gbh += q
            continue
        if i.get("unitType", "").lower() != "minutes":
            continue
        if sku not in MULT:
            other += q
            continue
        name = i.get("repositoryName") or "(unknown)"
        is_pub = name in public
        r = repos.setdefault(name, {"public": is_pub, "month": 0.0, "week": 0.0})
        counted = q if is_pub else q * MULT[sku]
        r["month"] += counted
        if since and day >= since:
            r["week"] += counted
        d = days.setdefault(day, [0.0, 0.0])
        d[0] += counted
        if not is_pub:
            d[1] += counted
    private = sum(r["month"] for r in repos.values() if not r["public"])
    public_m = sum(r["month"] for r in repos.values() if r["public"])
    return {"repos": repos, "days": days, "private": private, "public": public_m,
            "other": other, "storage_gbh": storage_gbh}


def summary_minutes(month):
    return sum(float(i["grossQuantity"]) for i in month["summary"]["usageItems"]
               if i.get("unitType", "").lower() == "minutes")


def net_amount(month):
    return sum(float(i.get("netAmount", 0)) for i in month["summary"]["usageItems"])


def main():
    data_dir = sys.argv[1]
    now = dt.datetime.now(dt.timezone.utc)
    if "--now" in sys.argv:
        now = parse(sys.argv[sys.argv.index("--now") + 1])
    with open(os.path.join(data_dir, "latest.json"), encoding="utf-8") as f:
        snap = json.load(f)
    fetched = parse(snap["fetched_at"])
    age_h = (now - fetched).total_seconds() / 3600
    public = set(snap["public_repos"])
    notify, checks = [], []

    days_in = calendar.monthrange(fetched.year, fetched.month)[1]
    month_start = fetched.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elapsed_days = max((fetched - month_start).total_seconds() / 86400, 0.25)
    week_start = (fetched - dt.timedelta(days=6)).strftime("%Y-%m-%d")
    t = tally(snap["current"], public, since=week_start)

    remaining = ALLOWANCE - t["private"]
    projected = t["private"] / elapsed_days * days_in
    status = "OVER" if projected > ALLOWANCE else "WARNING" if projected > WARN_AT else "OK"
    if remaining < 0:
        status = "OVER"
    storage_avg = t["storage_gbh"] / (elapsed_days * 24)
    billed = net_amount(snap["current"])

    # Sanity checks: say so loudly rather than hide a bad number.
    tp = snap["current"]["summary"].get("timePeriod", {})
    if (tp.get("year"), tp.get("month")) != (fetched.year, fetched.month):
        checks.append(f"snapshot month {tp} does not match fetch date {fetched:%Y-%m}")
    raw_total = sum(float(i["quantity"]) for i in snap["current"]["usage"]["usageItems"]
                    if i.get("unitType", "").lower() == "minutes")
    if abs(raw_total - summary_minutes(snap["current"])) > max(3, raw_total * 0.01):
        checks.append(f"itemised minutes {raw_total:.0f} != summary "
                      f"{summary_minutes(snap['current']):.0f}")
    if any(float(i["quantity"]) < 0 for i in snap["current"]["usage"]["usageItems"]):
        checks.append("negative quantities in the usage data")
    if checks:
        notify.append("data check failed")

    stale = age_h > 24
    if stale:
        notify.append(f"snapshot is {age_h / 24:.1f} days old")
    if status != "OK":
        notify.append(f"status {status}")
    if t["other"]:
        notify.append("larger-runner minutes present (always billed)")
    if billed > 0:
        notify.append(f"${billed:.2f} billed this month")

    exp_line = None
    if snap.get("token_expires"):
        try:
            exp = dt.datetime.strptime(snap["token_expires"][:19], "%Y-%m-%d %H:%M:%S")
            days_left = (exp.replace(tzinfo=dt.timezone.utc) - now).days
            exp_line = f"USAGE_TOKEN expires {exp:%d %b %Y} ({days_left} days)."
            if days_left <= 21:
                notify.append(f"token expires in {days_left} days")
        except ValueError:
            exp_line = f"USAGE_TOKEN expires {snap['token_expires']}."

    today = now.astimezone(DUB).strftime("%Y-%m-%d")
    subject = (f"GitHub Actions usage -- {today} -- {remaining:,.0f} min left ({status})"
               if remaining >= 0 else
               f"GitHub Actions usage -- {today} -- OVER by {-remaining:,.0f} min")
    if stale:
        subject += " -- STALE DATA"

    L = []
    if stale:
        L += [f"STALE DATA: these figures are from {fetched.astimezone(DUB):%a %d %b %H:%M} "
              f"Dublin ({age_h / 24:.1f} days old). The fetch workflow has not refreshed "
              "them - check https://github.com/coop1st/gh-usage-fetcher/actions", ""]
    for c in checks:
        L.append(f"DATA CHECK FAILED: {c}")
    if checks:
        L.append("")
    L += [(f"{remaining:,.0f} of {ALLOWANCE:,} private-repo minutes left for "
           if remaining >= 0 else
           f"OVER the {ALLOWANCE:,}-minute allowance by {-remaining:,.0f} minutes for ")
          + f"{fetched:%B %Y}. Status: {status}.",
          f"Used {t['private'] / ALLOWANCE:.1%} of the allowance with "
          f"{elapsed_days / days_in:.1%} of the month gone. Projected month-end: "
          f"about {projected:,.0f} minutes.",
          f"Figures as of {fetched.astimezone(DUB):%a %d %b %H:%M} Dublin time.", ""]
    if projected > ALLOWANCE:
        over = projected - ALLOWANCE
        L += [f"At this pace you'd go about {over:,.0f} minutes over: roughly "
              f"${over * PRICE:.2f} if your spending limit allows it, otherwise "
              "private-repo workflows stop until the 1st.", ""]
    L += ["SUMMARY",
          f"  Private-repo minutes used:     {t['private']:>7,.0f}  (counts against 3,000)",
          f"  Public-repo minutes (free):    {t['public']:>7,.0f}",
          f"  Remaining:                     {remaining:>7,.0f}",
          f"  Storage, monthly average:      {storage_avg:>7.2f} GB of {STORAGE_GB} GB",
          f"  Billed this month:           {'$' + format(billed, ',.2f'):>9}", ""]
    if t["other"]:
        L += [f"  Larger-runner minutes:         {t['other']:>7,.0f}  (always billed, "
              "never covered by the allowance)", ""]

    L.append("BY REPO (minutes)           month   last 7 days")
    order = sorted(t["repos"].items(), key=lambda kv: (kv[1]["public"], -kv[1]["month"]))
    for name, r in order:
        tag = "public, free" if r["public"] else "private"
        L.append(f"  {name[:26]:<26}{r['month']:>6,.0f}  {r['week']:>8,.0f}   {tag}")
    L.append("")

    L.append("LAST 7 DAYS              all repos   private only")
    for k in range(6, -1, -1):
        d = (fetched - dt.timedelta(days=k)).strftime("%Y-%m-%d")
        if d < month_start.strftime("%Y-%m-%d"):
            continue
        a, p = t["days"].get(d, [0, 0])
        L.append(f"  {dt.date.fromisoformat(d):%a %d %b}            {a:>6,.0f}   {p:>8,.0f}")
    L.append("")

    # Week-on-week pace, from the snapshot closest to 7 days before this one.
    obs = []
    priv = [(n, r["month"]) for n, r in t["repos"].items() if not r["public"]]
    if priv and t["private"]:
        top, top_m = max(priv, key=lambda x: x[1])
        obs.append(f"{top} is the biggest user: {top_m:,.0f} minutes, "
                   f"{top_m / t['private']:.0%} of private usage.")
    target = (fetched - dt.timedelta(days=7)).strftime("%Y-%m-%d")
    older = sorted(p for p in glob.glob(os.path.join(data_dir, "snapshots", "*.json"))
                   if os.path.basename(p)[:10] <= target)
    if older:
        with open(older[-1], encoding="utf-8") as f:
            prev = json.load(f)
        pf = parse(prev["fetched_at"])
        if (pf.year, pf.month) == (fetched.year, fetched.month):
            pt = tally(prev["current"], set(prev["public_repos"]))
            span = (fetched - pf).total_seconds() / 86400
            if span > 0:
                rate_now = (t["private"] - pt["private"]) / span
                rate_before = pt["private"] / max((pf - month_start).total_seconds() / 86400, 0.25)
                obs.append(f"Private usage ran at {rate_now:,.0f} min/day since the last "
                           f"report, against {rate_before:,.0f} min/day before it.")
    if "previous" in snap:
        pm = snap["previous"]
        pt = tally(pm, public)
        y, m = pm["summary"]["timePeriod"]["year"], pm["summary"]["timePeriod"]["month"]
        obs.append(f"{calendar.month_name[m]} {y} final: {pt['private']:,.0f} of {ALLOWANCE:,} "
                   f"private minutes, ${net_amount(pm):.2f} billed.")
    if obs:
        L.append("NOTES")
        L += [f"  - {o}" for o in obs]
        L.append("")
    if exp_line:
        L += [exp_line, ""]
    L.append("Generated by the Weekly GitHub Actions Usage Report cloud routine. "
             "Uses no billed GitHub Actions minutes.")

    print(json.dumps({"subject": subject, "body": "\n".join(L), "status": status,
                      "age_hours": round(age_h, 1), "fetched_at": snap["fetched_at"],
                      "notify": notify}, indent=1))


if __name__ == "__main__":
    main()
