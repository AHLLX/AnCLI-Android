"""Replay dsh's browser auth flow with a cookie jar, from inside the container.

Mirrors what Chrome does: GET /?token=… (expect 303 + Set-Cookie), then GET / with
the cookie (expect 303, NOT 401).
"""
import http.cookiejar
import sys
import urllib.error
import urllib.request

url = sys.argv[1] if len(sys.argv) > 1 else ""
if not url:
    print("usage: probe_dsh_auth.py <authenticated-url>")
    raise SystemExit(2)

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(target, label):
    try:
        with opener.open(target, timeout=15) as resp:
            body = resp.read(200).decode(errors="replace").strip()
            print(f"{label}: HTTP {resp.status} location={resp.headers.get('location')!r} "
                  f"set-cookie={'yes' if resp.headers.get('set-cookie') else 'no'}")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read(200).decode(errors="replace").strip()
        print(f"{label}: HTTP {exc.code} body={body[:80]!r}")
        return exc.code, body


base = url.split("?")[0]
status, _ = get(url, "1) token URL      ")
print("   cookies after exchange:", [(c.name[:18] + "…", c.domain, c.path) for c in jar])
status2, body2 = get(base + "/", "2) authenticated /")
print("   verdict:", "AUTH OK" if status2 == 303 else "AUTH FAILED")
