"""End-to-end test of crypto donations against a stand-in for NOWPayments.

Start the local dev server like this, then run: python scripts/test_donate.py
  npx wrangler dev --port 8787 --ip 127.0.0.1 --persist-to <state> --var ADMIN_TOKEN:local-admin \
     --var NOWPAYMENTS_API_KEY:test --var NOWPAYMENTS_IPN_SECRET:ipn-test-secret --var NOWPAYMENTS_API:http://127.0.0.1:8797/v1
"""
import hashlib, hmac, json, sys, threading, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SITE, SECRET = "http://127.0.0.1:8787", "ipn-test-secret"
invoices = []


class Stand(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        ok = self.path == "/v1/invoice" and self.headers.get("x-api-key") == "test"
        invoices.append(body)
        out = json.dumps({"id": "inv123", "invoice_url": "https://nowpayments.test/invoice/inv123", "order_id": body.get("order_id")} if ok else {"error": "no"}).encode()
        self.send_response(200 if ok else 401); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None


def get(path):
    op = urllib.request.build_opener(NoRedirect)
    try:
        r = op.open(SITE + path); return r.status, dict(r.headers)
    except urllib.error.HTTPError as e: return e.code, dict(e.headers)


def ipn(body, sign=True, secret=SECRET, deep=True):
    def sort(v):
        if isinstance(v, dict): return {k: sort(v[k]) for k in sorted(v)}
        if isinstance(v, list): return [sort(x) for x in v]
        return v
    text = json.dumps(sort(body), separators=(",", ":"), ensure_ascii=False)
    sig = hmac.new(secret.encode(), text.encode(), hashlib.sha512).hexdigest()
    req = urllib.request.Request(SITE + "/api/v1/donate/ipn", method="POST", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json", **({"x-nowpayments-sig": sig} if sign else {})})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")


def admin(path):
    req = urllib.request.Request(SITE + "/api/v1/admin" + path, headers={"authorization": "Bearer local-admin"})
    with urllib.request.urlopen(req) as r: return json.loads(r.read())


ok = True
def check(cond, what):
    global ok
    print(("  ok   " if cond else "  FAIL ") + what); ok &= bool(cond)


srv = ThreadingHTTPServer(("127.0.0.1", 8797), Stand); threading.Thread(target=srv.serve_forever, daemon=True).start()

s, h = get("/donate/crypto?amount=10")
check(s == 302 and h.get("Location", h.get("location", "")).startswith("https://nowpayments.test/invoice/"), f"a donation sends the donor to NOWPayments' invoice ({s})")
inv = invoices[-1] if invoices else {}
check(inv.get("price_amount") == 10 and inv.get("price_currency") == "usd" and inv.get("ipn_callback_url", "").endswith("/api/v1/donate/ipn"), "the invoice asks for $10 and names our notice address")
order = inv.get("order_id", "")
s, h = get("/donate/crypto?amount=0.2")
check(s == 302 and "donate=amount" in h.get("Location", h.get("location", "")), "an amount below $1 goes back to the page with a note")

base = {"payment_id": 5551, "invoice_id": "inv123", "order_id": order, "price_amount": 10, "price_currency": "usd", "pay_currency": "btc",
        "pay_amount": 0.00015, "actually_paid": 0, "outcome_amount": 0.000149, "outcome_currency": "btc", "payment_status": "waiting", "fee": {"currency": "btc", "depositFee": 0}}
s, b = ipn(base, sign=False); check(s == 401, f"an unsigned notice is refused ({s})")
s, b = ipn(base, secret="wrong"); check(s == 401, f"a notice signed with the wrong secret is refused ({s})")
s, b = ipn(base); check(s in (200, 201) and b.get("updated"), f"a signed notice is accepted ({s} {b})")
s, b = ipn({**base, "payment_status": "finished", "actually_paid": 0.00015}); check(b.get("updated"), "the finished notice updates it")
d = admin("/donations")
row = [x for x in d["items"] if x["id"] == order][0]
check(row["status"] == "finished" and row["pay_currency"] == "btc" and row["actually_paid"] == 0.00015, f"admin shows it finished, in BTC ({row['status']})")
check(d["finished_usd"] >= 10, f"and counts it (${d['finished_usd']})")
s, b = ipn({**base, "order_id": "someone-else"}); check(b.get("ignored"), "a notice for an order that isn't ours is ignored")

with urllib.request.urlopen(SITE + "/robots.txt") as r: check("Disallow: /donate/" in r.read().decode(), "crawlers are kept off the donation links")
with urllib.request.urlopen(urllib.request.Request(SITE + "/support?thanks=1", headers={"user-agent": "python-requests"})) as r:
    page = r.read().decode(); check("Thank you" in page and "/donate/crypto?amount=25" in page, "the support page has the buttons and the thank-you")

srv.shutdown()
print("\nall good" if ok else "\nSOMETHING FAILED"); sys.exit(0 if ok else 1)
