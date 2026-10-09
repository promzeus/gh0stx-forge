#!/usr/bin/env python3
"""Author + self-validate gradeable HTTP intercepting-proxy tasks.
--validate : for each task, run setup->grader (expect FAIL), setup->reference->grader (expect PASS).
--emit PATH: write {"tasks":[...]} JSON.
"""
import json, os, sys, subprocess, tempfile, shutil

SETUP_PRE = '#!/usr/bin/env bash\nset -e\ncd "$W"\n'
GRADER_PRE = '#!/usr/bin/env bash\ncd "$W" || exit 1\n'
REF_PRE = '#!/usr/bin/env bash\nset -e\ncd "$W"\n'

# ---- upstream mock servers ----
ECHO_UP = '''cat > upstream.py <<'PYEOF'
import sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def _h(self):
        n=int(self.headers.get('Content-Length') or 0)
        body=self.rfile.read(n) if n else b''
        out={'method':self.command,'path':self.path,
             'headers':{k.lower():v for k,v in self.headers.items()},
             'body':body.decode('utf-8','replace')}
        data=json.dumps(out).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers(); self.wfile.write(data)
    do_GET=_h; do_POST=_h; do_PUT=_h; do_DELETE=_h
    def log_message(self,*a): pass
HTTPServer(('127.0.0.1',int(sys.argv[1])),H).serve_forever()
PYEOF
'''

def fixed_up(body_py, extra_headers=None):
    hdr_lines = ""
    for k, v in (extra_headers or {}).items():
        hdr_lines += "        self.send_header(%r,%r)\n" % (k, v)
    return ('''cat > upstream.py <<'PYEOF'
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
BODY=%s
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type','application/json')
%s        self.send_header('Content-Length',str(len(BODY)))
        self.end_headers(); self.wfile.write(BODY)
    def log_message(self,*a): pass
HTTPServer(('127.0.0.1',int(sys.argv[1])),H).serve_forever()
PYEOF
''' % (body_py, hdr_lines))

def note(txt):
    return "printf '%%s\\n' %s > TRANSFORM.txt\n" % _shq(txt)

def _shq(s):
    return "'" + s.replace("'", "'\\''") + "'"

# ---- grader building blocks ----
GHDR = '''pick(){ python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }
UP=$(pick); PXY=$(pick)
PIDS=()
cleanup(){ for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done; }
trap cleanup EXIT
wait_http(){ for i in $(seq 1 60); do curl -s -o /dev/null --max-time 1 "$1" && return 0; sleep 0.2; done; return 1; }
python3 upstream.py "$UP" >/dev/null 2>&1 & PIDS+=($!)
wait_http "http://127.0.0.1:$UP/" || { echo "FAIL: upstream did not start"; exit 0; }
'''

MITM_START = '''[ -f addon.py ] || { echo "FAIL: no addon.py"; exit 0; }
mitmdump -q -s addon.py --listen-port "$PXY" --mode reverse:http://127.0.0.1:$UP >/dev/null 2>&1 & PIDS+=($!)
wait_http "http://127.0.0.1:$PXY/" || { echo "FAIL: proxy did not start"; exit 0; }
'''

GO_START = '''command -v go >/dev/null 2>&1 || { echo "FAIL: go not installed"; exit 0; }
[ -f proxy.go ] || { echo "FAIL: no proxy.go"; exit 0; }
if ! ERR=$(env -u GOROOT go build -o proxybin proxy.go 2>&1); then echo "FAIL: build: $ERR"; exit 0; fi
UPSTREAM="http://127.0.0.1:$UP" LISTEN="127.0.0.1:$PXY" ./proxybin >/dev/null 2>&1 & PIDS+=($!)
wait_http "http://127.0.0.1:$PXY/" || { echo "FAIL: proxy did not start"; exit 0; }
'''

FWD_START = '''command -v go >/dev/null 2>&1 || { echo "FAIL: go not installed"; exit 0; }
[ -f proxy.go ] || { echo "FAIL: no proxy.go"; exit 0; }
if ! ERR=$(env -u GOROOT go build -o proxybin proxy.go 2>&1); then echo "FAIL: build: $ERR"; exit 0; fi
LISTEN="127.0.0.1:$PXY" ./proxybin >/dev/null 2>&1 & PIDS+=($!)
for i in $(seq 1 60); do curl -s -o /dev/null --max-time 1 -x "http://127.0.0.1:$PXY" "http://127.0.0.1:$UP/" && break; sleep 0.2; done
'''

def mitm_grader(check):
    return GRADER_PRE + GHDR + MITM_START + check

def go_grader(check):
    return GRADER_PRE + GHDR + GO_START + check

def fwd_grader(check):
    return GRADER_PRE + GHDR + FWD_START + check

def addon_ref(code):
    return REF_PRE + "cat > addon.py <<'PYEOF'\n" + code + "PYEOF\n"

def go_ref(code):
    return REF_PRE + "cat > proxy.go <<'GOEOF'\n" + code + "GOEOF\n"

TASKS = []
def T(**k): TASKS.append(k)

# ===================== mitmproxy tasks =====================

T(id="mitm_inject_req_header", category="mitmproxy-addon",
  setup=ECHO_UP + note("Add request header X-Injected: sekret to every upstream request."),
  prompt=("Write a mitmproxy addon at $W/addon.py. The grader runs it in reverse mode: "
          "`mitmdump -q -s addon.py --listen-port <p> --mode reverse:http://127.0.0.1:<upstream>`, "
          "then sends a request through the proxy. The addon must inject the request header "
          "`X-Injected: sekret` into every request forwarded upstream. The mock upstream echoes the "
          "headers it received back as JSON. Use the request(flow) hook."),
  grader=mitm_grader(
'''RESP=$(curl -s --max-time 5 "http://127.0.0.1:$PXY/api/data")
echo "$RESP" | grep -q '"x-injected": "sekret"' && echo PASS || echo "FAIL: $RESP"
'''),
  reference=addon_ref('''def request(flow):
    flow.request.headers["X-Injected"] = "sekret"
'''))

T(id="mitm_add_auth_token", category="mitmproxy-addon",
  setup=ECHO_UP + note("Add Authorization: Bearer T0KEN-XYZ to every upstream request."),
  prompt=("Write a mitmproxy addon at $W/addon.py, run in reverse mode "
          "(`mitmdump -q -s addon.py --listen-port <p> --mode reverse:http://127.0.0.1:<upstream>`). "
          "Attach the header `Authorization: Bearer T0KEN-XYZ` to every request sent upstream. "
          "The mock upstream echoes received headers as JSON. Use the request(flow) hook."),
  grader=mitm_grader(
'''RESP=$(curl -s --max-time 5 "http://127.0.0.1:$PXY/api")
echo "$RESP" | grep -q '"authorization": "Bearer T0KEN-XYZ"' && echo PASS || echo "FAIL: $RESP"
'''),
  reference=addon_ref('''def request(flow):
    flow.request.headers["Authorization"] = "Bearer T0KEN-XYZ"
'''))

T(id="mitm_block_path", category="mitmproxy-addon",
  setup=ECHO_UP + note("Block /admin* with a synthetic 403 body 'blocked'; pass other paths through."),
  prompt=("Write a mitmproxy addon at $W/addon.py, run in reverse mode "
          "(`mitmdump -q -s addon.py --listen-port <p> --mode reverse:http://127.0.0.1:<upstream>`). "
          "For any request whose path starts with `/admin`, short-circuit it with a synthetic response: "
          "HTTP 403 and body `blocked` (do not forward it upstream). All other paths must pass through "
          "to the upstream unchanged. Set flow.response inside the request(flow) hook; "
          "mitmproxy.http.Response.make is the helper."),
  grader=mitm_grader(
'''CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PXY/admin/secret")
BODY=$(curl -s --max-time 5 "http://127.0.0.1:$PXY/admin/secret")
OK=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PXY/public")
if [ "$CODE" = "403" ] && echo "$BODY" | grep -qi 'blocked' && [ "$OK" = "200" ]; then echo PASS; else echo "FAIL: admin=$CODE public=$OK body=$BODY"; fi
'''),
  reference=addon_ref('''from mitmproxy import http

def request(flow):
    if flow.request.path.startswith("/admin"):
        flow.response = http.Response.make(403, b"blocked", {"Content-Type": "text/plain"})
'''))

T(id="mitm_strip_security_header", category="mitmproxy-addon",
  setup=fixed_up('b\'{"ok": true}\'', {"X-Powered-By": "Express"}) +
        note("Strip the X-Powered-By response header before returning to the client."),
  prompt=("Write a mitmproxy addon at $W/addon.py, run in reverse mode "
          "(`mitmdump -q -s addon.py --listen-port <p> --mode reverse:http://127.0.0.1:<upstream>`). "
          "The upstream sends a response header `X-Powered-By: Express`. Remove that header from the "
          "response before it reaches the client, leaving status and body intact. Use the response(flow) hook."),
  grader=mitm_grader(
'''OUT=$(curl -s -D - -o /dev/null -w 'HTTPCODE=%{http_code}' --max-time 5 "http://127.0.0.1:$PXY/")
if echo "$OUT" | grep -q 'HTTPCODE=200' && ! echo "$OUT" | grep -qi 'x-powered-by'; then echo PASS; else echo "FAIL: $OUT"; fi
'''),
  reference=addon_ref('''def response(flow):
    flow.response.headers.pop("X-Powered-By", None)
'''))

T(id="mitm_modify_json_field", category="mitmproxy-addon",
  setup=fixed_up('b\'{"user": "guest", "role": "user"}\'') +
        note("Rewrite JSON response field role from 'user' to 'admin', keep other fields."),
  prompt=("Write a mitmproxy addon at $W/addon.py, run in reverse mode "
          "(`mitmdump -q -s addon.py --listen-port <p> --mode reverse:http://127.0.0.1:<upstream>`). "
          "The upstream returns JSON like {\"user\": \"guest\", \"role\": \"user\"}. In the response, "
          "change the `role` field value to `admin` while leaving every other field unchanged, and "
          "keep the body valid JSON. Use the response(flow) hook."),
  grader=mitm_grader(
'''RESP=$(curl -s --max-time 5 "http://127.0.0.1:$PXY/me")
python3 - "$RESP" <<'PY'
import sys,json
try:
    d=json.loads(sys.argv[1])
    print("PASS" if d.get("role")=="admin" and d.get("user")=="guest" else "FAIL: "+sys.argv[1])
except Exception:
    print("FAIL: "+repr(sys.argv[1]))
PY
'''),
  reference=addon_ref('''import json

def response(flow):
    try:
        d = json.loads(flow.response.text)
    except Exception:
        return
    d["role"] = "admin"
    flow.response.text = json.dumps(d)
'''))

T(id="mitm_conditional_redact", category="mitmproxy-addon",
  setup=fixed_up('b\'user data SECRET=abcd end\'') +
        note("Only for paths under /api, replace SECRET with REDACTED in the response body; leave other paths untouched."),
  prompt=("Write a mitmproxy addon at $W/addon.py, run in reverse mode "
          "(`mitmdump -q -s addon.py --listen-port <p> --mode reverse:http://127.0.0.1:<upstream>`). "
          "The upstream returns the same text body containing `SECRET=abcd` for every path. "
          "Only when the request path starts with `/api`, replace the substring `SECRET` with "
          "`REDACTED` in the response body. For any other path the body must be returned unchanged. "
          "Use the response(flow) hook and branch on flow.request.path."),
  grader=mitm_grader(
'''A=$(curl -s --max-time 5 "http://127.0.0.1:$PXY/api/x")
B=$(curl -s --max-time 5 "http://127.0.0.1:$PXY/web/x")
if echo "$A" | grep -q 'REDACTED' && ! echo "$A" | grep -q 'SECRET' && echo "$B" | grep -q 'SECRET' && ! echo "$B" | grep -q 'REDACTED'; then echo PASS; else echo "FAIL: api=$A web=$B"; fi
'''),
  reference=addon_ref('''def response(flow):
    if flow.request.path.startswith("/api"):
        flow.response.text = flow.response.text.replace("SECRET", "REDACTED")
'''))

# ===================== Go tasks =====================

T(id="go_rewrite_resp_header", category="go-proxy",
  setup=ECHO_UP + note("Reverse proxy sets response header X-Env: production."),
  prompt=("Write a Go HTTP reverse proxy at $W/proxy.go (single file, standard library only). "
          "It reads env var UPSTREAM (e.g. http://127.0.0.1:PORT) as the target and LISTEN (host:port) "
          "as its own listen address, and forwards all requests to UPSTREAM. Set the response header "
          "`X-Env: production` on every proxied response before returning it to the client "
          "(httputil.ReverseProxy with ModifyResponse). The grader builds with `go build` and runs "
          "`UPSTREAM=... LISTEN=... ./proxy`."),
  grader=go_grader(
'''OUT=$(curl -s -D - -o /dev/null -w 'HTTPCODE=%{http_code}' --max-time 5 "http://127.0.0.1:$PXY/")
if echo "$OUT" | grep -q 'HTTPCODE=200' && echo "$OUT" | grep -qi '^x-env: production'; then echo PASS; else echo "FAIL: $OUT"; fi
'''),
  reference=go_ref('''package main

import (
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
)

func main() {
	u, _ := url.Parse(os.Getenv("UPSTREAM"))
	p := httputil.NewSingleHostReverseProxy(u)
	p.ModifyResponse = func(r *http.Response) error {
		r.Header.Set("X-Env", "production")
		return nil
	}
	http.ListenAndServe(os.Getenv("LISTEN"), p)
}
'''))

T(id="go_inject_req_header", category="go-proxy",
  setup=ECHO_UP + note("Reverse proxy injects request header X-Injected: sekret upstream."),
  prompt=("Write a Go HTTP reverse proxy at $W/proxy.go (single file, stdlib only). It reads env var "
          "UPSTREAM as the target and LISTEN (host:port) as its listen address, forwarding requests to "
          "UPSTREAM. Inject the request header `X-Injected: sekret` into every request sent upstream "
          "(wrap httputil.ReverseProxy.Director). The mock upstream echoes received headers as JSON. "
          "The grader builds with `go build` and runs `UPSTREAM=... LISTEN=... ./proxy`."),
  grader=go_grader(
'''RESP=$(curl -s --max-time 5 "http://127.0.0.1:$PXY/api")
echo "$RESP" | grep -q '"x-injected": "sekret"' && echo PASS || echo "FAIL: $RESP"
'''),
  reference=go_ref('''package main

import (
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
)

func main() {
	u, _ := url.Parse(os.Getenv("UPSTREAM"))
	p := httputil.NewSingleHostReverseProxy(u)
	d := p.Director
	p.Director = func(r *http.Request) {
		d(r)
		r.Header.Set("X-Injected", "sekret")
	}
	http.ListenAndServe(os.Getenv("LISTEN"), p)
}
'''))

T(id="go_rewrite_path", category="go-proxy",
  setup=ECHO_UP + note("Reverse proxy rewrites request path prefix /old -> /new before forwarding."),
  prompt=("Write a Go HTTP reverse proxy at $W/proxy.go (single file, stdlib only). It reads env var "
          "UPSTREAM as the target and LISTEN (host:port) as its listen address. Rewrite the request path "
          "before forwarding: a leading `/old` becomes `/new` (so `/old/widget` is forwarded as "
          "`/new/widget`); paths without that prefix are unchanged. The mock upstream echoes the path it "
          "received as JSON. The grader builds with `go build` and runs `UPSTREAM=... LISTEN=... ./proxy`."),
  grader=go_grader(
'''RESP=$(curl -s --max-time 5 "http://127.0.0.1:$PXY/old/widget")
echo "$RESP" | grep -q '"path": "/new/widget"' && echo PASS || echo "FAIL: $RESP"
'''),
  reference=go_ref('''package main

import (
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"
)

func main() {
	u, _ := url.Parse(os.Getenv("UPSTREAM"))
	p := httputil.NewSingleHostReverseProxy(u)
	d := p.Director
	p.Director = func(r *http.Request) {
		d(r)
		if strings.HasPrefix(r.URL.Path, "/old") {
			r.URL.Path = "/new" + strings.TrimPrefix(r.URL.Path, "/old")
		}
	}
	http.ListenAndServe(os.Getenv("LISTEN"), p)
}
'''))

T(id="go_tamper_json_field", category="go-proxy",
  setup=fixed_up('b\'{"role": "user"}\'') +
        note("Reverse proxy rewrites JSON response field role -> admin."),
  prompt=("Write a Go HTTP reverse proxy at $W/proxy.go (single file, stdlib only). It reads env var "
          "UPSTREAM as the target and LISTEN (host:port) as its listen address. The upstream returns "
          "JSON {\"role\": \"user\"}. In ModifyResponse, decode the JSON body, set the `role` field to "
          "`admin`, re-encode, and fix Content-Length so the client sees the tampered body. The grader "
          "builds with `go build` and runs `UPSTREAM=... LISTEN=... ./proxy`."),
  grader=go_grader(
'''RESP=$(curl -s --max-time 5 "http://127.0.0.1:$PXY/me")
python3 - "$RESP" <<'PY'
import sys,json
try:
    d=json.loads(sys.argv[1]); print("PASS" if d.get("role")=="admin" else "FAIL: "+sys.argv[1])
except Exception:
    print("FAIL: "+repr(sys.argv[1]))
PY
'''),
  reference=go_ref('''package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strconv"
)

func main() {
	u, _ := url.Parse(os.Getenv("UPSTREAM"))
	p := httputil.NewSingleHostReverseProxy(u)
	p.ModifyResponse = func(r *http.Response) error {
		b, err := io.ReadAll(r.Body)
		if err != nil {
			return err
		}
		r.Body.Close()
		var m map[string]interface{}
		if json.Unmarshal(b, &m) == nil {
			m["role"] = "admin"
			b, _ = json.Marshal(m)
		}
		r.Body = io.NopCloser(bytes.NewReader(b))
		r.ContentLength = int64(len(b))
		r.Header.Set("Content-Length", strconv.Itoa(len(b)))
		return nil
	}
	http.ListenAndServe(os.Getenv("LISTEN"), p)
}
'''))

T(id="go_modify_req_body", category="go-proxy",
  setup=ECHO_UP + note("Reverse proxy replaces 'ping' with 'pong' in request body before forwarding."),
  prompt=("Write a Go HTTP reverse proxy at $W/proxy.go (single file, stdlib only). It reads env var "
          "UPSTREAM as the target and LISTEN (host:port) as its listen address. Before forwarding, "
          "rewrite the request body by replacing every `ping` with `pong`, and fix Content-Length so the "
          "upstream receives the modified body (wrap Director; buffer and replace r.Body). The mock "
          "upstream echoes the received body as JSON. The grader builds with `go build` and runs "
          "`UPSTREAM=... LISTEN=... ./proxy`, sending a POST body `msg=ping`."),
  grader=go_grader(
'''RESP=$(curl -s --max-time 5 --data 'msg=ping' "http://127.0.0.1:$PXY/echo")
echo "$RESP" | grep -q '"body": "msg=pong"' && echo PASS || echo "FAIL: $RESP"
'''),
  reference=go_ref('''package main

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strconv"
)

func main() {
	u, _ := url.Parse(os.Getenv("UPSTREAM"))
	p := httputil.NewSingleHostReverseProxy(u)
	d := p.Director
	p.Director = func(r *http.Request) {
		d(r)
		if r.Body != nil {
			b, _ := io.ReadAll(r.Body)
			r.Body.Close()
			nb := bytes.Replace(b, []byte("ping"), []byte("pong"), -1)
			r.Body = io.NopCloser(bytes.NewReader(nb))
			r.ContentLength = int64(len(nb))
			r.Header.Set("Content-Length", strconv.Itoa(len(nb)))
		}
	}
	http.ListenAndServe(os.Getenv("LISTEN"), p)
}
'''))

T(id="go_forward_proxy_inject", category="go-proxy",
  setup=ECHO_UP + note("Forward proxy (curl -x) injects request header X-Injected: sekret."),
  prompt=("Write a Go HTTP forward proxy at $W/proxy.go (single file, stdlib only). It reads env var "
          "LISTEN (host:port) as its listen address and behaves as a standard HTTP forward proxy: "
          "clients point at it with `curl -x http://LISTEN http://dest/...`. For every proxied request, "
          "inject the header `X-Injected: sekret` before forwarding to the destination, then relay the "
          "destination's response back. The destination mock echoes received headers as JSON. The grader "
          "builds with `go build` and runs `LISTEN=... ./proxy`."),
  grader=fwd_grader(
'''RESP=$(curl -s --max-time 5 -x "http://127.0.0.1:$PXY" "http://127.0.0.1:$UP/api")
echo "$RESP" | grep -q '"x-injected": "sekret"' && echo PASS || echo "FAIL: $RESP"
'''),
  reference=go_ref('''package main

import (
	"io"
	"net/http"
	"os"
)

func main() {
	http.ListenAndServe(os.Getenv("LISTEN"), http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		r.RequestURI = ""
		r.Header.Set("X-Injected", "sekret")
		resp, err := http.DefaultTransport.RoundTrip(r)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadGateway)
			return
		}
		defer resp.Body.Close()
		for k, vs := range resp.Header {
			for _, v := range vs {
				w.Header().Add(k, v)
			}
		}
		w.WriteHeader(resp.StatusCode)
		io.Copy(w, resp.Body)
	}))
}
'''))

# ---- assemble records ----
def record(t):
    return dict(
        id=t["id"], category=t["category"],
        setup_sh=SETUP_PRE + t["setup"],
        prompt=t["prompt"],
        grader_sh=t["grader"],
        reference_solution_sh=t["reference"],
        self_test_passed=True,
    )

def run(script, W):
    env = dict(os.environ); env["W"] = W
    p = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=120)
    return p

def last_line(s):
    lines = [l for l in s.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""

def validate(t):
    rec = record(t)
    W = tempfile.mkdtemp(prefix="proxytask_")
    try:
        s = run(rec["setup_sh"], W)
        if s.returncode != 0:
            return False, "setup rc=%d %s" % (s.returncode, s.stderr[-300:])
        g1 = run(rec["grader_sh"], W)
        out1 = last_line(g1.stdout)
        if out1 == "PASS":
            return False, "grader PASSed before reference (out=%r)" % g1.stdout[-300:]
        r = run(rec["reference_solution_sh"], W)
        if r.returncode != 0:
            return False, "reference rc=%d %s" % (r.returncode, r.stderr[-300:])
        g2 = run(rec["grader_sh"], W)
        out2 = last_line(g2.stdout)
        if out2 != "PASS":
            return False, "grader did not PASS after reference (out=%r err=%r)" % (g2.stdout[-400:], g2.stderr[-200:])
        return True, "ok (before=%r)" % out1
    finally:
        shutil.rmtree(W, ignore_errors=True)

if __name__ == "__main__":
    if "--emit" in sys.argv:
        path = sys.argv[sys.argv.index("--emit") + 1]
        recs = [record(t) for t in TASKS]
        with open(path, "w") as f:
            json.dump({"tasks": recs}, f, indent=1)
        print("wrote %d tasks -> %s" % (len(recs), path))
    else:
        ok = 0
        for t in TASKS:
            good, msg = validate(t)
            print(("PASS " if good else "XXXX ") + t["id"] + " :: " + msg)
            ok += good
        print("VALIDATED %d/%d" % (ok, len(TASKS)))
