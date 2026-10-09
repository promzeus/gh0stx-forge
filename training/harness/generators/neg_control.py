#!/usr/bin/env python3
"""Negative control: plant a compiling/loading no-op passthrough proxy for each task
and confirm the grader still FAILs (proves graders test the transform, not file presence)."""
import os, sys, subprocess, tempfile, shutil
import build_tasks as B

NOOP_ADDON = "# no-op passthrough\n"

NOOP_GO_REVERSE = '''package main
import ("net/http";"net/http/httputil";"net/url";"os")
func main(){
	u,_:=url.Parse(os.Getenv("UPSTREAM"))
	http.ListenAndServe(os.Getenv("LISTEN"), httputil.NewSingleHostReverseProxy(u))
}
'''

NOOP_GO_FORWARD = '''package main
import ("io";"net/http";"os")
func main(){
	http.ListenAndServe(os.Getenv("LISTEN"), http.HandlerFunc(func(w http.ResponseWriter, r *http.Request){
		r.RequestURI=""
		resp,err:=http.DefaultTransport.RoundTrip(r)
		if err!=nil { http.Error(w, err.Error(), 502); return }
		defer resp.Body.Close()
		for k,vs:=range resp.Header { for _,v:=range vs { w.Header().Add(k,v) } }
		w.WriteHeader(resp.StatusCode)
		io.Copy(w, resp.Body)
	}))
}
'''

def run(script, W):
    env = dict(os.environ); env["W"] = W
    return subprocess.run(["bash","-c",script], env=env, capture_output=True, text=True, timeout=120)

def noop_for(t):
    if t["category"].startswith("mitm"):
        return "addon.py", NOOP_ADDON
    if "forward" in t["id"]:
        return "proxy.go", NOOP_GO_FORWARD
    return "proxy.go", NOOP_GO_REVERSE

ok = 0
for t in B.TASKS:
    rec = B.record(t)
    W = tempfile.mkdtemp(prefix="neg_")
    try:
        run(rec["setup_sh"], W)
        fn, content = noop_for(t)
        with open(os.path.join(W, fn), "w") as f:
            f.write(content)
        g = run(rec["grader_sh"], W)
        out = B.last_line(g.stdout)
        good = (out != "PASS")   # no-op must NOT pass
        print(("GOOD " if good else "BAD! ") + t["id"] + " :: noop grader -> " + repr(out))
        ok += good
    finally:
        shutil.rmtree(W, ignore_errors=True)
print("NOOP-REJECTED %d/%d" % (ok, len(B.TASKS)))
