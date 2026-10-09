#!/usr/bin/env python3
"""Build + self-test gradeable ALGORITHM tasks. Each: setup writes input.txt into $W,
prompt asks for answer.txt, grader recomputes the answer independently and prints PASS/FAIL,
reference solves it. Validate: setup->grader = NOT PASS; setup->reference->grader = PASS."""
import json, os, subprocess, tempfile, time, sys

OUT = "/private/tmp/claude-503/-Users-flash-gb10/9698b8ec-be3d-495b-98d5-10e0b424bba5/scratchpad/tasks2_algorithms.json"

def T(id, category, setup, prompt, grader, reference):
    return dict(
        id=id, category=category,
        setup_sh="#!/usr/bin/env bash\nset -e\ncd \"$W\"\n" + setup,
        prompt=prompt,
        grader_sh="#!/usr/bin/env bash\ncd \"$W\" || exit 1\n" + grader,
        reference_solution_sh="#!/usr/bin/env bash\nset -e\ncd \"$W\"\n" + reference,
        self_test_passed=True,
        notes="hard/algorithm-challenge; deterministic single instance, stdlib python3 only")

TASKS = []

# ---------------------------------------------------------------- 1. longest palindrome
TASKS.append(T("alg_longest_palindrome", "string",
r'''python3 - <<'PY'
import random
random.seed(1201)
n=4000
s=[random.choice('abcd') for _ in range(n)]
half=[random.choice('abcd') for _ in range(150)]
pal=half+[random.choice('abcd')]+half[::-1]
s[1500:1500+len(pal)]=pal
open('input.txt','w').write(''.join(s)+'\n')
PY''',
"input.txt holds one line: a string of lowercase letters (length 4000). Find the length of the "
"longest palindromic substring (a contiguous substring that reads the same forwards and backwards). "
"Write that single integer to answer.txt.",
r'''python3 - <<'PY'
import os
s=open('input.txt').read().strip()
n=len(s); best=1
for c in range(n):
    l,r=c,c
    while l>=0 and r<n and s[l]==s[r]: l-=1; r+=1
    if r-l-1>best: best=r-l-1
    l,r=c,c+1
    while l>=0 and r<n and s[l]==s[r]: l-=1; r+=1
    if r-l-1>best: best=r-l-1
ok = os.path.exists('answer.txt') and open('answer.txt').read().strip()==str(best)
print('PASS' if ok else 'FAIL')
PY''',
r'''python3 - <<'PY'
s=open('input.txt').read().strip()
n=len(s); best=1
for c in range(n):
    l,r=c,c
    while l>=0 and r<n and s[l]==s[r]: l-=1; r+=1
    if r-l-1>best: best=r-l-1
    l,r=c,c+1
    while l>=0 and r<n and s[l]==s[r]: l-=1; r+=1
    if r-l-1>best: best=r-l-1
open('answer.txt','w').write(str(best))
PY'''))

# ---------------------------------------------------------------- 2. edit distance
TASKS.append(T("alg_edit_distance", "string",
r'''python3 - <<'PY'
import random
random.seed(207)
a=''.join(random.choice('acgt') for _ in range(1200))
b=''.join(random.choice('acgt') for _ in range(1200))
open('input.txt','w').write(a+'\n'+b+'\n')
PY''',
"input.txt holds two lines, string A then string B (each 1200 letters). Compute the Levenshtein edit "
"distance between A and B: the minimum number of single-character insertions, deletions, or "
"substitutions to turn A into B. Write that integer to answer.txt.",
r'''python3 - <<'PY'
import os
L=open('input.txt').read().splitlines()
a,b=L[0],L[1]
m,n=len(a),len(b)
prev=list(range(n+1))
for i in range(1,m+1):
    cur=[i]+[0]*n; ai=a[i-1]
    for j in range(1,n+1):
        c=0 if ai==b[j-1] else 1
        v=prev[j-1]+c
        if prev[j]+1<v: v=prev[j]+1
        if cur[j-1]+1<v: v=cur[j-1]+1
        cur[j]=v
    prev=cur
d=prev[n]
ok=os.path.exists('answer.txt') and open('answer.txt').read().strip()==str(d)
print('PASS' if ok else 'FAIL')
PY''',
r'''python3 - <<'PY'
L=open('input.txt').read().splitlines()
a,b=L[0],L[1]
m,n=len(a),len(b)
prev=list(range(n+1))
for i in range(1,m+1):
    cur=[i]+[0]*n; ai=a[i-1]
    for j in range(1,n+1):
        c=0 if ai==b[j-1] else 1
        v=prev[j-1]+c
        if prev[j]+1<v: v=prev[j]+1
        if cur[j-1]+1<v: v=cur[j-1]+1
        cur[j]=v
    prev=cur
open('answer.txt','w').write(str(prev[n]))
PY'''))

# ---------------------------------------------------------------- 3. max subarray
TASKS.append(T("alg_max_subarray", "array",
r'''python3 - <<'PY'
import random
random.seed(413)
n=200000
arr=[random.randint(-1000,1000) for _ in range(n)]
open('input.txt','w').write(str(n)+'\n'+' '.join(map(str,arr))+'\n')
PY''',
"input.txt: line 1 is n (200000); line 2 is n space-separated integers (each between -1000 and 1000). "
"Find the maximum sum of any non-empty contiguous subarray. Write that integer to answer.txt.",
r'''python3 - <<'PY'
import os
t=open('input.txt').read().split()
n=int(t[0]); a=list(map(int,t[1:1+n]))
best=cur=a[0]
for x in a[1:]:
    cur = x if cur+x < x else cur+x
    if cur>best: best=cur
ok=os.path.exists('answer.txt') and open('answer.txt').read().strip()==str(best)
print('PASS' if ok else 'FAIL')
PY''',
r'''python3 - <<'PY'
t=open('input.txt').read().split()
n=int(t[0]); a=list(map(int,t[1:1+n]))
best=cur=a[0]
for x in a[1:]:
    cur = x if cur+x < x else cur+x
    if cur>best: best=cur
open('answer.txt','w').write(str(best))
PY'''))

# ---------------------------------------------------------------- 4. merge intervals (union length)
TASKS.append(T("alg_interval_union", "interval",
r'''python3 - <<'PY'
import random
random.seed(511)
n=100000
lines=[str(n)]
for _ in range(n):
    s=random.randint(0,10**9)
    e=s+random.randint(1,10000)
    lines.append(str(s)+' '+str(e))
open('input.txt','w').write('\n'.join(lines)+'\n')
PY''',
"input.txt: line 1 is n (100000); each of the next n lines has two integers s e describing the "
"half-open interval [s, e) (e exclusive). Compute the total length covered by the union of all n "
"intervals. Write that integer to answer.txt.",
r'''python3 - <<'PY'
import os
t=open('input.txt').read().split()
n=int(t[0]); nums=t[1:]
iv=sorted((int(nums[2*i]),int(nums[2*i+1])) for i in range(n))
total=0; cs=ce=None
for s,e in iv:
    if cs is None: cs,ce=s,e
    elif s>ce:
        total+=ce-cs; cs,ce=s,e
    elif e>ce: ce=e
if cs is not None: total+=ce-cs
ok=os.path.exists('answer.txt') and open('answer.txt').read().strip()==str(total)
print('PASS' if ok else 'FAIL')
PY''',
r'''python3 - <<'PY'
t=open('input.txt').read().split()
n=int(t[0]); nums=t[1:]
iv=sorted((int(nums[2*i]),int(nums[2*i+1])) for i in range(n))
total=0; cs=ce=None
for s,e in iv:
    if cs is None: cs,ce=s,e
    elif s>ce:
        total+=ce-cs; cs,ce=s,e
    elif e>ce: ce=e
if cs is not None: total+=ce-cs
open('answer.txt','w').write(str(total))
PY'''))

# ---------------------------------------------------------------- 5. sliding window max (sum of maxima)
TASKS.append(T("alg_sliding_window_max", "array",
r'''python3 - <<'PY'
import random
random.seed(619)
n=200000; k=1000
arr=[random.randint(0,10**6) for _ in range(n)]
open('input.txt','w').write(str(n)+' '+str(k)+'\n'+' '.join(map(str,arr))+'\n')
PY''',
"input.txt: line 1 is n and k (n=200000, k=1000); line 2 is n non-negative integers. For every "
"contiguous window of exactly k elements, take its maximum, and sum those maxima over all n-k+1 "
"windows. Write that total sum (a single integer) to answer.txt.",
r'''python3 - <<'PY'
import os
from collections import deque
t=open('input.txt').read().split()
n=int(t[0]); k=int(t[1]); a=list(map(int,t[2:2+n]))
dq=deque(); total=0
for i,x in enumerate(a):
    while dq and a[dq[-1]]<=x: dq.pop()
    dq.append(i)
    if dq[0]<=i-k: dq.popleft()
    if i>=k-1: total+=a[dq[0]]
ok=os.path.exists('answer.txt') and open('answer.txt').read().strip()==str(total)
print('PASS' if ok else 'FAIL')
PY''',
r'''python3 - <<'PY'
from collections import deque
t=open('input.txt').read().split()
n=int(t[0]); k=int(t[1]); a=list(map(int,t[2:2+n]))
dq=deque(); total=0
for i,x in enumerate(a):
    while dq and a[dq[-1]]<=x: dq.pop()
    dq.append(i)
    if dq[0]<=i-k: dq.popleft()
    if i>=k-1: total+=a[dq[0]]
open('answer.txt','w').write(str(total))
PY'''))

# ---------------------------------------------------------------- 6. dijkstra on weighted grid
TASKS.append(T("alg_dijkstra_grid", "graph",
r'''python3 - <<'PY'
import random
random.seed(727)
N=400
rows=[str(N)]
for _ in range(N):
    rows.append(' '.join(str(random.randint(1,99)) for _ in range(N)))
open('input.txt','w').write('\n'.join(rows)+'\n')
PY''',
"input.txt: line 1 is N (400); the next N lines each hold N integers (1..99), a grid of cell weights. "
"Starting at the top-left cell (0,0) and moving one step up/down/left/right at a time, reach the "
"bottom-right cell (N-1,N-1). The cost of a path is the sum of the weights of every cell it visits, "
"including both the start and the end cell. Write the minimum possible path cost to answer.txt.",
r'''python3 - <<'PY'
import os, heapq
t=open('input.txt').read().split()
N=int(t[0]); g=[[int(t[1+r*N+c]) for c in range(N)] for r in range(N)]
INF=float('inf')
dist=[[INF]*N for _ in range(N)]
dist[0][0]=g[0][0]
pq=[(g[0][0],0,0)]
while pq:
    d,r,c=heapq.heappop(pq)
    if d>dist[r][c]: continue
    for dr,dc in ((1,0),(-1,0),(0,1),(0,-1)):
        nr,nc=r+dr,c+dc
        if 0<=nr<N and 0<=nc<N:
            nd=d+g[nr][nc]
            if nd<dist[nr][nc]:
                dist[nr][nc]=nd; heapq.heappush(pq,(nd,nr,nc))
ans=dist[N-1][N-1]
ok=os.path.exists('answer.txt') and open('answer.txt').read().strip()==str(ans)
print('PASS' if ok else 'FAIL')
PY''',
r'''python3 - <<'PY'
import heapq
t=open('input.txt').read().split()
N=int(t[0]); g=[[int(t[1+r*N+c]) for c in range(N)] for r in range(N)]
INF=float('inf')
dist=[[INF]*N for _ in range(N)]
dist[0][0]=g[0][0]
pq=[(g[0][0],0,0)]
while pq:
    d,r,c=heapq.heappop(pq)
    if d>dist[r][c]: continue
    for dr,dc in ((1,0),(-1,0),(0,1),(0,-1)):
        nr,nc=r+dr,c+dc
        if 0<=nr<N and 0<=nc<N:
            nd=d+g[nr][nc]
            if nd<dist[nr][nc]:
                dist[nr][nc]=nd; heapq.heappush(pq,(nd,nr,nc))
open('answer.txt','w').write(str(dist[N-1][N-1]))
PY'''))

# ---------------------------------------------------------------- 7. connected components
TASKS.append(T("alg_connected_components", "graph",
r'''python3 - <<'PY'
import random
random.seed(829)
N=100000; M=130000
lines=[str(N)+' '+str(M)]
for _ in range(M):
    u=random.randrange(N); v=random.randrange(N)
    lines.append(str(u)+' '+str(v))
open('input.txt','w').write('\n'.join(lines)+'\n')
PY''',
"input.txt: line 1 is N and M (N=100000 nodes labeled 0..N-1, M=130000 edges); each of the next M "
"lines has two node ids u v meaning an undirected edge. Count the number of connected components "
"(isolated nodes with no edges each count as their own component). Write that integer to answer.txt.",
r'''python3 - <<'PY'
import os, sys
sys.setrecursionlimit(10000)
t=open('input.txt').read().split()
N=int(t[0]); M=int(t[1])
p=list(range(N))
def f(x):
    while p[x]!=x:
        p[x]=p[p[x]]; x=p[x]
    return x
idx=2
for _ in range(M):
    u=int(t[idx]); v=int(t[idx+1]); idx+=2
    ru,rv=f(u),f(v)
    if ru!=rv: p[ru]=rv
comp=sum(1 for i in range(N) if f(i)==i)
ok=os.path.exists('answer.txt') and open('answer.txt').read().strip()==str(comp)
print('PASS' if ok else 'FAIL')
PY''',
r'''python3 - <<'PY'
t=open('input.txt').read().split()
N=int(t[0]); M=int(t[1])
p=list(range(N))
def f(x):
    while p[x]!=x:
        p[x]=p[p[x]]; x=p[x]
    return x
idx=2
for _ in range(M):
    u=int(t[idx]); v=int(t[idx+1]); idx+=2
    ru,rv=f(u),f(v)
    if ru!=rv: p[ru]=rv
comp=sum(1 for i in range(N) if f(i)==i)
open('answer.txt','w').write(str(comp))
PY'''))

# ---------------------------------------------------------------- 8. coin change (number of ways)
TASKS.append(T("alg_coin_change_ways", "dp",
r'''python3 - <<'PY'
coins=[1,2,5,10,20,25,50,100]
amount=30000
open('input.txt','w').write(str(amount)+'\n'+' '.join(map(str,coins))+'\n')
PY''',
"input.txt: line 1 is a target amount (30000); line 2 is the available coin denominations "
"(space-separated). Each denomination may be used any number of times. Count the number of distinct "
"multisets of coins that sum to exactly the amount (order does not matter). Write that exact integer "
"(it is very large) to answer.txt.",
r'''python3 - <<'PY'
import os
L=open('input.txt').read().splitlines()
amount=int(L[0]); coins=list(map(int,L[1].split()))
ways=[0]*(amount+1); ways[0]=1
for c in coins:
    for a in range(c,amount+1):
        ways[a]+=ways[a-c]
ans=ways[amount]
ok=os.path.exists('answer.txt') and open('answer.txt').read().strip()==str(ans)
print('PASS' if ok else 'FAIL')
PY''',
r'''python3 - <<'PY'
L=open('input.txt').read().splitlines()
amount=int(L[0]); coins=list(map(int,L[1].split()))
ways=[0]*(amount+1); ways[0]=1
for c in coins:
    for a in range(c,amount+1):
        ways[a]+=ways[a-c]
open('answer.txt','w').write(str(ways[amount]))
PY'''))

# ---------------------------------------------------------------- 9. 0/1 knapsack
TASKS.append(T("alg_knapsack01", "dp",
r'''python3 - <<'PY'
import random
random.seed(1013)
N=800; W=4000
lines=[str(N)+' '+str(W)]
for _ in range(N):
    w=random.randint(1,300); v=random.randint(1,1000)
    lines.append(str(w)+' '+str(v))
open('input.txt','w').write('\n'.join(lines)+'\n')
PY''',
"input.txt: line 1 is N and W (N=800 items, W=4000 capacity); each of the next N lines has an item's "
"weight and value (w v). Select a subset of items whose total weight is at most W and whose total "
"value is maximal (each item used at most once). Write the maximum total value to answer.txt.",
r'''python3 - <<'PY'
import os
t=open('input.txt').read().split()
N=int(t[0]); W=int(t[1])
items=[(int(t[2+2*i]),int(t[3+2*i])) for i in range(N)]
dp=[0]*(W+1)
for w,v in items:
    for cap in range(W,w-1,-1):
        c=dp[cap-w]+v
        if c>dp[cap]: dp[cap]=c
ans=dp[W]
ok=os.path.exists('answer.txt') and open('answer.txt').read().strip()==str(ans)
print('PASS' if ok else 'FAIL')
PY''',
r'''python3 - <<'PY'
t=open('input.txt').read().split()
N=int(t[0]); W=int(t[1])
items=[(int(t[2+2*i]),int(t[3+2*i])) for i in range(N)]
dp=[0]*(W+1)
for w,v in items:
    for cap in range(W,w-1,-1):
        c=dp[cap-w]+v
        if c>dp[cap]: dp[cap]=c
open('answer.txt','w').write(str(dp[W]))
PY'''))

# ---------------------------------------------------------------- 10. CRT
TASKS.append(T("alg_crt", "number-theory",
r'''python3 - <<'PY'
import random
random.seed(1117)
primes=[9973,9967,9949,9941,9931,9929,9923,9907]
lines=[str(len(primes))]
for p in primes:
    r=random.randrange(p)
    lines.append(str(r)+' '+str(p))
open('input.txt','w').write('\n'.join(lines)+'\n')
PY''',
"input.txt: line 1 is k (the number of congruences); each of the next k lines has two integers r m, "
"meaning x is congruent to r modulo m. The moduli are pairwise coprime. Find the unique smallest "
"non-negative integer x satisfying all k congruences simultaneously. Write x to answer.txt.",
r'''python3 - <<'PY'
import os
t=open('input.txt').read().split()
k=int(t[0])
rs=[int(t[1+2*i]) for i in range(k)]
ms=[int(t[2+2*i]) for i in range(k)]
x=0; M=1
for r,m in zip(rs,ms):
    inv=pow(M%m,-1,m)
    x=x+M*(((r-x)*inv)%m)
    M*=m
x%=M
ok=os.path.exists('answer.txt') and open('answer.txt').read().strip()==str(x)
print('PASS' if ok else 'FAIL')
PY''',
r'''python3 - <<'PY'
t=open('input.txt').read().split()
k=int(t[0])
rs=[int(t[1+2*i]) for i in range(k)]
ms=[int(t[2+2*i]) for i in range(k)]
x=0; M=1
for r,m in zip(rs,ms):
    inv=pow(M%m,-1,m)
    x=x+M*(((r-x)*inv)%m)
    M*=m
open('answer.txt','w').write(str(x%M))
PY'''))

# ---------------------------------------------------------------- 11. evaluate arithmetic expression
TASKS.append(T("alg_eval_expression", "parsing",
r'''python3 - <<'PY'
import random
random.seed(1223)
def expr(d):
    parts=[term(d)]
    for _ in range(random.randint(1,3)):
        parts.append(random.choice(['+','-'])); parts.append(term(d))
    return ''.join(parts)
def term(d):
    parts=[factor(d)]
    for _ in range(random.randint(0,2)):
        parts.append('*'); parts.append(factor(d))
    return ''.join(parts)
def factor(d):
    if d<=0 or random.random()<0.5:
        return str(random.randint(0,99))
    return '('+expr(d-1)+')'
e=expr(4)
open('input.txt','w').write(e+'\n')
PY''',
"input.txt holds one line: an arithmetic expression over non-negative integers using +, -, *, and "
"parentheses, with standard operator precedence (* binds tighter than + and -). Evaluate it exactly "
"and write the resulting integer (which may be negative) to answer.txt.",
r'''python3 - <<'PY'
import os
e=open('input.txt').read().strip()
assert set(e)<=set('0123456789+-*() ')
val=eval(compile(e,'<e>','eval'),{'__builtins__':{}},{})
ok=os.path.exists('answer.txt') and open('answer.txt').read().strip()==str(val)
print('PASS' if ok else 'FAIL')
PY''',
r'''python3 - <<'PY'
e=open('input.txt').read().strip()
val=eval(compile(e,'<e>','eval'),{'__builtins__':{}},{})
open('answer.txt','w').write(str(val))
PY'''))

# ---------------------------------------------------------------- 12. game of life
TASKS.append(T("alg_game_of_life", "simulation",
r'''python3 - <<'PY'
import random
random.seed(1231)
H=60; W=60; N=120
grid=[''.join('1' if random.random()<0.35 else '0' for _ in range(W)) for _ in range(H)]
open('input.txt','w').write(str(H)+' '+str(W)+'\n'+str(N)+'\n'+'\n'.join(grid)+'\n')
PY''',
"input.txt: line 1 is H W (60 60); line 2 is N (120); the next H lines each hold W characters ('0' "
"dead, '1' live) giving the initial grid. Evolve it N generations under Conway's Game of Life rules "
"(a live cell with 2 or 3 live neighbors survives, a dead cell with exactly 3 live neighbors is "
"born; the 8 neighbors are the surrounding cells and cells outside the grid are always dead). Write "
"the number of live cells after N generations to answer.txt.",
r'''python3 - <<'PY'
import os
L=open('input.txt').read().splitlines()
H,W=map(int,L[0].split()); N=int(L[1])
g=[[1 if ch=='1' else 0 for ch in L[2+r]] for r in range(H)]
for _ in range(N):
    ng=[[0]*W for _ in range(H)]
    for r in range(H):
        for c in range(W):
            cnt=0
            for dr in (-1,0,1):
                for dc in (-1,0,1):
                    if dr==0 and dc==0: continue
                    nr,nc=r+dr,c+dc
                    if 0<=nr<H and 0<=nc<W: cnt+=g[nr][nc]
            ng[r][c]=1 if (cnt==3 or (g[r][c] and cnt==2)) else 0
    g=ng
ans=sum(sum(row) for row in g)
ok=os.path.exists('answer.txt') and open('answer.txt').read().strip()==str(ans)
print('PASS' if ok else 'FAIL')
PY''',
r'''python3 - <<'PY'
L=open('input.txt').read().splitlines()
H,W=map(int,L[0].split()); N=int(L[1])
g=[[1 if ch=='1' else 0 for ch in L[2+r]] for r in range(H)]
for _ in range(N):
    ng=[[0]*W for _ in range(H)]
    for r in range(H):
        for c in range(W):
            cnt=0
            for dr in (-1,0,1):
                for dc in (-1,0,1):
                    if dr==0 and dc==0: continue
                    nr,nc=r+dr,c+dc
                    if 0<=nr<H and 0<=nc<W: cnt+=g[nr][nc]
            ng[r][c]=1 if (cnt==3 or (g[r][c] and cnt==2)) else 0
    g=ng
open('answer.txt','w').write(str(sum(sum(row) for row in g)))
PY'''))

# =============================================================== self-test harness
def run(script, W, timeout=60):
    p = subprocess.run(["bash", "-c", script], env={**os.environ, "W": W},
                       capture_output=True, text=True, timeout=timeout)
    return p

def self_test(task):
    d = tempfile.mkdtemp()
    try:
        t0=time.time()
        r = run(task["setup_sh"], d)
        if r.returncode != 0:
            return False, "setup rc=%d %s" % (r.returncode, r.stderr[-300:])
        # grader with no answer.txt -> must NOT be PASS
        r = run(task["grader_sh"], d)
        pre = r.stdout.strip()
        if "PASS" in pre:
            return False, "grader PASS before solving (should FAIL): %r" % pre
        # reference solves
        r = run(task["reference_solution_sh"], d)
        if r.returncode != 0:
            return False, "reference rc=%d %s" % (r.returncode, r.stderr[-300:])
        # grader after solving -> must be PASS
        r = run(task["grader_sh"], d)
        post = r.stdout.strip()
        dt=time.time()-t0
        if post != "PASS":
            return False, "grader after reference not PASS: %r %s" % (post, r.stderr[-200:])
        return True, "ok %.1fs" % dt
    finally:
        import shutil; shutil.rmtree(d, ignore_errors=True)

def main():
    passed=[]
    for t in TASKS:
        ok,msg = self_test(t)
        t["self_test_passed"]=ok
        print("%-28s %s  %s" % (t["id"], "PASS" if ok else "FAIL", msg))
        if ok: passed.append(t)
    json.dump({"tasks":passed}, open(OUT,"w"), indent=1)
    print("\nvalidated %d/%d -> %s" % (len(passed), len(TASKS), OUT))

if __name__=="__main__":
    main()
