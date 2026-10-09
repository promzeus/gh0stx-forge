#!/usr/bin/env python3
# Export one opencode session as an SFT conversation (messages + tool_calls) for LoRA.
# usage: export_sft.py <db> <session_id> <rules_path> <out_jsonl_append> [meta_json]
import sys, json, sqlite3

db, sid, rules_path, out = sys.argv[1:5]
meta = json.loads(sys.argv[5]) if len(sys.argv) > 5 else {}
TOOL_OUT_CAP = 4000

rules = open(rules_path, encoding="utf-8", errors="replace").read().strip()
con = sqlite3.connect(db)
rows = con.execute(
    "SELECT p.data, m.data FROM part p JOIN message m ON p.message_id=m.id "
    "WHERE p.session_id=? ORDER BY p.time_created", (sid,)).fetchall()

messages = [{"role": "system", "content": rules}]
think, content = [], []          # buffers for the current assistant step
def flush_assistant(tool=None):
    global think, content
    txt = ("\n".join(think + content)).strip()
    if tool is None:
        if txt:
            messages.append({"role": "assistant", "content": txt})
    else:
        msg = {"role": "assistant", "content": txt}
        msg["tool_calls"] = [tool]
        messages.append(msg)
    think, content = [], []

for pdata, mdata in rows:
    p = json.loads(pdata); m = json.loads(mdata)
    role = m.get("role"); t = p.get("type")
    if t == "text":
        txt = p.get("text", "")
        if role == "user":
            messages.append({"role": "user", "content": txt})
        else:
            content.append(txt)
    elif t == "reasoning":
        think.append(p.get("text", ""))
    elif t == "tool":
        st = p.get("state", {})
        call = {
            "id": p.get("callID", ""),
            "type": "function",
            "function": {"name": p.get("tool", ""),
                         "arguments": json.dumps(st.get("input", {}), ensure_ascii=False)},
        }
        flush_assistant(tool=call)
        outp = str(st.get("output", ""))[:TOOL_OUT_CAP]
        messages.append({"role": "tool", "tool_call_id": p.get("callID", ""), "content": outp})
    # step-start / step-finish ignored
flush_assistant()

rec = {"messages": messages}
rec.update(meta)
with open(out, "a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
# report
nt = sum(1 for x in messages if x["role"] == "tool")
na = sum(1 for x in messages if x["role"] == "assistant")
print(f"exported sid={sid} msgs={len(messages)} assistant={na} tool={nt}")
