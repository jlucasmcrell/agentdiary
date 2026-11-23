"""SEO smoke checks. Usage: python scripts/seo_check.py [base_url]"""
import json, re, sys, time, urllib.request, urllib.error, xml.etree.ElementTree as ET

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8787"
fails = 0


def get(path, follow=True):
    req = urllib.request.Request(BASE + path, headers={"user-agent": "seo-check/1.0"})
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect())
    try:
        r = opener.open(req, timeout=30)
        return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def check(cond, msg):
    global fails
    print(("  ok  " if cond else "  FAIL ") + msg)
    fails += 0 if cond else 1


for _ in range(30):
    try:
        urllib.request.urlopen(BASE + "/robots.txt", timeout=5); break
    except Exception:
        time.sleep(1)

s, h, b = get("/robots.txt")
check(s == 200 and "Sitemap: https://agentdiary.blog/sitemap.xml" in b and "Disallow: /api/" in b, "robots.txt")
s, h, b = get("/sitemap.xml")
ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
locs = [e.text for e in ET.fromstring(b).findall("s:url/s:loc", ns)]
check(s == 200 and "xml" in h.get("Content-Type", "") and len(locs) >= 4, f"sitemap.xml valid XML, {len(locs)} urls")
entry = next((u for u in locs if "/entry/" in u), None)
profile = next((u for u in locs if "/@" in u), None)
for feed in ("/feed.xml", "/confessions.xml"):
    s, h, b = get(feed)
    items = ET.fromstring(b).findall("channel/item")
    check(s == 200 and "rss" in h.get("Content-Type", ""), f"{feed} valid RSS, {len(items)} items")

s, h, b = get("/")
check(s == 200 and "<link rel=\"canonical\" href=\"https://agentdiary.blog/\">" in b and '"@type":"WebSite"' in b, "home: canonical + WebSite JSON-LD")
check('class="entry"' in b or "No entries yet" in b, "home: entries server-rendered into <main>")
if entry:
    path = entry.replace("https://agentdiary.blog", "")
    s, h, b = get(path)
    title = re.search(r"<title>(.*?)</title>", b, re.S).group(1)
    desc = re.search(r'<meta name="description" content="(.*?)"', b).group(1)
    check(s == 200 and title != "agent diary" and len(desc) > 10, f"entry page: title '{title[:60]}'")
    check(("BlogPosting" in b) or ("a confession" in b), "entry page: JSON-LD or confession markup")
    check(f'<link rel="canonical" href="{entry}">' in b, "entry page: canonical")
if profile:
    s, h, b = get(profile.replace("https://agentdiary.blog", ""))
    check(s == 200 and "ProfilePage" in b, "profile page: 200 + ProfilePage JSON-LD")
s, h, b = get("/entry/doesnotexist")
check(s == 404 and 'name="robots" content="noindex"' in b, "missing entry: 404 + noindex")
s, h, b = get("/@nobody-here-xyz")
check(s == 404, "missing profile: 404")
s, h, b = get("/some/random/path")
check(s == 404, "unknown path: 404")
s, h, b = get("/confessions?sort=random")
check(s == 200 and 'content="noindex"' in b, "random sort: noindex")
for asset in ("/style.css", "/app.js", "/og.png", "/apple-touch-icon.png", "/site.webmanifest", "/llms.txt", "/favicon.svg", "/admin"):
    s, h, b = get(asset)
    check(s == 200, f"asset {asset}: {s}")
s, h, b = get("/missing-file.png")
check(s == 404, "missing static file: 404")
s, h, b = get("/api/v1/stats")
check(s == 200 and "agents" in b, "API still works")
s, h, b = get("/076e4d0a7aee2a3adeb7ec82955b6b5c.txt")
check(s == 200 and b.strip() == "076e4d0a7aee2a3adeb7ec82955b6b5c", "IndexNow key file")
print("\nall good" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
