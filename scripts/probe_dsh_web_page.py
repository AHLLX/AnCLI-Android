"""Replay the user's exact dsh web URL against the live server on 3080.

Answers two questions at once: does the server accept that token (303 + cookie),
and if so does it then serve the SPA index and its assets.
"""
import http.cookiejar
import re
import urllib.error
import urllib.request

TOKEN = "fFAqBwZUXRGOoCz-WQf5g8nYpYQUFOV7aV8uQMJVDY"
BASE = "http://127.0.0.1:3080"
URL = f"{BASE}/?token={TOKEN}"

jar = http.cookiejar.CookieJar()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(url, label, opener, read=260):
    try:
        with opener.open(url, timeout=15) as resp:
            body = resp.read(read).decode(errors="replace")
            print(f"{label}: HTTP {resp.status}")
            print(f"    location={resp.headers.get('location')!r} set-cookie={'yes' if resp.headers.get('set-cookie') else 'no'}")
            print(f"    body[:120]={body[:120]!r}")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read(read).decode(errors="replace")
        print(f"{label}: HTTP {exc.code} location={exc.headers.get('location')!r}")
        print(f"    body[:120]={body[:120]!r}")
        return exc.code, body
    except Exception as exc:
        print(f"{label}: FAILED {type(exc).__name__}: {exc}")
        return None, ""


print("== 1) raw token exchange (redirects NOT followed) ==")
probe(URL, "   GET /?token=…", urllib.request.build_opener(NoRedirect))

print("\n== 2) full browser-like flow (cookie jar, redirects followed) ==")
follow = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
status, body = probe(URL, "   token URL     ", follow)
print("   cookie jar:", [c.name[:22] + "…" for c in jar])
status2, body2 = probe(f"{BASE}/", "   GET /         ", follow)

print("\n== 3) SPA assets referenced by the index ==")
assets = re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', body2 or body)
print("   found:", assets[:4] or "none")
for asset in assets[:2]:
    target = asset if asset.startswith("http") else BASE + ("" if asset.startswith("/") else "/") + asset
    probe(target, f"   {asset[:40]}", follow, read=80)
