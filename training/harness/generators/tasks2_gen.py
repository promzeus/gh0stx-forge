#!/usr/bin/env python3
"""Emit tasks2_protocol.json: gradeable PROTOCOL & FORMAT-PARSING tasks.
Each setup plants a raw blob / parser pair / encoded structure. Grader recomputes
independently and echoes PASS/FAIL as first token. Reference solves it."""
import json

def T(id, category, setup_py, prompt, grader_py, ref_py):
    return dict(
        id=id, category=category, behavior=category,
        setup_sh="#!/usr/bin/env bash\nset -e\npython3 - <<'PY'\n" + setup_py + "\nPY\n",
        prompt=prompt,
        grader_sh="#!/usr/bin/env bash\ncd \"$W\" || exit 1\npython3 - <<'PY'\n" + grader_py + "\nPY\n",
        reference_solution_sh="#!/usr/bin/env bash\nset -e\ncd \"$W\" || exit 1\npython3 - <<'PY'\n" + ref_py + "\nPY\n",
        self_test_passed=True, notes="protocol/format-parsing harvest")

TASKS = []

# ---------------------------------------------------------------- 1. CL.TE smuggling
TASKS.append(T("smuggle_clte", "smuggling",
r'''
p = r"""
def front_len(raw):
    head, sep, body = raw.partition(b"\r\n\r\n")
    cl = None
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            cl = int(line.split(b":", 1)[1].strip())
    if cl is None:
        cl = len(body)
    return len(head) + 4 + cl

def back_chunked(raw):
    head, sep, body = raw.partition(b"\r\n\r\n")
    i = 0
    out = b""
    while True:
        j = body.find(b"\r\n", i)
        if j < 0:
            break
        size = int(body[i:j].split(b";")[0], 16)
        i = j + 2
        if size == 0:
            i += 2
            break
        out += body[i:i+size]
        i += size + 2
    consumed = len(head) + 4 + i
    return out, raw[consumed:]
"""
open("parsers.py", "w").write(p)
''',
"A front-end proxy and back-end server disagree on framing. parsers.py defines "
"front_len(raw) (front-end: uses Content-Length, returns how many bytes it treats as one request) "
"and back_chunked(raw) (back-end: uses Transfer-Encoding: chunked, returns (body, leftover_bytes)). "
"Craft a single raw HTTP/1.1 request (bytes) and write it to req.bin so that: (1) the front-end "
"consumes the WHOLE blob as one request (front_len(raw) == len(raw)), and (2) the back-end, parsing "
"the same bytes as chunked, terminates the first request early and leaves a smuggled request in the "
"leftover that starts with b'GET /admin'. This is the classic CL.TE desync. Import parsers to test.",
r'''
try:
    import parsers
    raw = open("req.bin", "rb").read()
    consumed = parsers.front_len(raw)
    body, left = parsers.back_chunked(raw)
    ok = (consumed == len(raw)) and left.startswith(b"GET /admin")
    print("PASS" if ok else "FAIL " + repr((consumed, len(raw), left[:24])))
except Exception as e:
    print("FAIL " + repr(e))
''',
r'''
smug = b"GET /admin HTTP/1.1\r\nHost: internal\r\nContent-Length: 0\r\n\r\n"
body = b"0\r\n\r\n" + smug
head = b"POST / HTTP/1.1\r\nHost: front\r\nContent-Length: %d\r\nTransfer-Encoding: chunked\r\n\r\n" % len(body)
open("req.bin", "wb").write(head + body)
'''))

# ---------------------------------------------------------------- 2. TE.CL smuggling
TASKS.append(T("smuggle_tecl", "smuggling",
r'''
p = r"""
def front_chunked(raw):
    head, sep, body = raw.partition(b"\r\n\r\n")
    i = 0
    while True:
        j = body.find(b"\r\n", i)
        if j < 0:
            return len(raw)
        size = int(body[i:j].split(b";")[0], 16)
        i = j + 2
        if size == 0:
            i += 2
            break
        i += size + 2
    return len(head) + 4 + i

def back_len(raw):
    head, sep, body = raw.partition(b"\r\n\r\n")
    cl = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            cl = int(line.split(b":", 1)[1].strip())
    consumed = len(head) + 4 + cl
    return raw[consumed:]
"""
open("parsers.py", "w").write(p)
''',
"parsers.py defines front_chunked(raw) (front-end: parses the body as Transfer-Encoding: chunked, "
"returns bytes consumed as one request) and back_len(raw) (back-end: uses Content-Length, returns "
"the leftover bytes after that many body bytes). Craft one raw HTTP/1.1 request, write it to req.bin, "
"so that: (1) the front-end consumes the WHOLE blob as one request (front_chunked(raw) == len(raw)), "
"and (2) the back-end, honoring a small Content-Length, leaves a smuggled request in its leftover that "
"starts with b'GET /admin'. This is the classic TE.CL desync. Import parsers to test.",
r'''
try:
    import parsers
    raw = open("req.bin", "rb").read()
    consumed = parsers.front_chunked(raw)
    left = parsers.back_len(raw)
    ok = (consumed == len(raw)) and left.startswith(b"GET /admin")
    print("PASS" if ok else "FAIL " + repr((consumed, len(raw), left[:24])))
except Exception as e:
    print("FAIL " + repr(e))
''',
r'''
smug = b"GET /admin HTTP/1.1\r\nHost: internal\r\nX-Ignore: y"
sizeline = b"%x\r\n" % len(smug)
body = sizeline + smug + b"\r\n" + b"0\r\n\r\n"
cl = len(sizeline)
head = b"POST / HTTP/1.1\r\nHost: front\r\nContent-Length: %d\r\nTransfer-Encoding: chunked\r\n\r\n" % cl
open("req.bin", "wb").write(head + body)
'''))

# ---------------------------------------------------------------- 3. protobuf decode
TASKS.append(T("protobuf_decode", "protobuf",
r'''
open("blob.hex", "w").write("089601120774657374696e6718c0c407")
''',
"blob.hex holds the hex of a serialized protobuf message (proto3 wire format). Hand-decode the "
"wire format (tag = (field_number<<3)|wire_type, varints are little-endian base-128) and recover "
"the value of field number 3 (a varint, wire type 0). Write only its decimal integer value to "
"answer.txt. No protobuf library.",
r'''
import binascii
try:
    data = binascii.unhexlify(open("blob.hex").read().strip())
    def rv(b, i):
        r = 0; s = 0
        while True:
            x = b[i]; i += 1
            r |= (x & 0x7f) << s
            if not (x & 0x80):
                break
            s += 7
        return r, i
    i = 0; f = {}
    while i < len(data):
        tag, i = rv(data, i)
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            v, i = rv(data, i); f[fn] = v
        elif wt == 2:
            ln, i = rv(data, i); f[fn] = data[i:i+ln]; i += ln
        else:
            raise ValueError("wt")
    exp = f[3]
    got = int(open("answer.txt").read().strip())
    print("PASS" if got == exp else "FAIL " + repr((got, exp)))
except Exception as e:
    print("FAIL " + repr(e))
''',
r'''
import binascii
data = binascii.unhexlify(open("blob.hex").read().strip())
def rv(b, i):
    r = 0; s = 0
    while True:
        x = b[i]; i += 1
        r |= (x & 0x7f) << s
        if not (x & 0x80):
            break
        s += 7
    return r, i
i = 0; f = {}
while i < len(data):
    tag, i = rv(data, i)
    fn, wt = tag >> 3, tag & 7
    if wt == 0:
        v, i = rv(data, i); f[fn] = v
    elif wt == 2:
        ln, i = rv(data, i); f[fn] = data[i:i+ln]; i += ln
open("answer.txt", "w").write(str(f[3]))
'''))

# ---------------------------------------------------------------- 4. protobuf encode
TASKS.append(T("protobuf_encode", "protobuf",
r'''
open("spec.txt", "w").write("field1 varint=42\nfield2 string=hello\nfield5 varint=300\n")
''',
"Construct a serialized protobuf message (proto3 wire format) with exactly three fields: field number 1 "
"= varint 42, field number 2 = length-delimited UTF-8 string 'hello', field number 5 = varint 300. "
"Write the raw bytes to out.bin. No protobuf library; emit tags and varints by hand.",
r'''
try:
    data = open("out.bin", "rb").read()
    def rv(b, i):
        r = 0; s = 0
        while True:
            x = b[i]; i += 1
            r |= (x & 0x7f) << s
            if not (x & 0x80):
                break
            s += 7
        return r, i
    i = 0; f = {}
    while i < len(data):
        tag, i = rv(data, i)
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            v, i = rv(data, i); f[fn] = v
        elif wt == 2:
            ln, i = rv(data, i); f[fn] = data[i:i+ln]; i += ln
        else:
            raise ValueError("wt")
    ok = f.get(1) == 42 and f.get(2) == b"hello" and f.get(5) == 300
    print("PASS" if ok else "FAIL " + repr(f))
except Exception as e:
    print("FAIL " + repr(e))
''',
r'''
def var(n):
    o = b""
    while True:
        b = n & 0x7f; n >>= 7
        if n:
            o += bytes([b | 0x80])
        else:
            o += bytes([b]); break
    return o
out = bytes([(1 << 3) | 0]) + var(42)
out += bytes([(2 << 3) | 2]) + var(5) + b"hello"
out += bytes([(5 << 3) | 0]) + var(300)
open("out.bin", "wb").write(out)
'''))

# ---------------------------------------------------------------- 5. DER CN extraction
TASKS.append(T("der_cn", "asn1",
r'''
import base64
def tlv(t, v):
    return bytes([t, len(v)]) + v
oid = tlv(0x06, bytes([0x55, 0x04, 0x03]))
ps = tlv(0x13, b"example.com")
atav = tlv(0x30, oid + ps)
rdn = tlv(0x31, atav)
name = tlv(0x30, rdn)
serial = tlv(0x02, bytes([0x0a, 0x1b, 0x2c, 0x3d]))
cert = tlv(0x30, serial + name)
open("cert.b64", "w").write(base64.b64encode(cert).decode())
''',
"cert.b64 is base64 of a DER-encoded structure (SEQUENCE { INTEGER serial, Name }). Parse the DER "
"by hand (each element is tag / length / value; lengths here are single-byte). Find the commonName "
"attribute (OID 2.5.4.3, DER bytes 06 03 55 04 03) and extract its value string. Write only that "
"string to answer.txt. No ASN.1 library.",
r'''
import base64
try:
    data = base64.b64decode(open("cert.b64").read())
    def parse(b):
        i = 0; out = []
        while i < len(b):
            t = b[i]; i += 1
            l = b[i]; i += 1
            if l & 0x80:
                n = l & 0x7f; l = int.from_bytes(b[i:i+n], "big"); i += n
            v = b[i:i+l]; i += l
            out.append((t, v))
        return out
    cn = [None]
    def walk(b):
        ch = parse(b)
        if len(ch) >= 2 and ch[0][0] == 0x06 and ch[0][1] == bytes([0x55, 4, 3]):
            cn[0] = ch[1][1]
        for t, v in ch:
            if t in (0x30, 0x31):
                walk(v)
    walk(data)
    exp = cn[0].decode()
    got = open("answer.txt").read().strip()
    print("PASS" if got == exp else "FAIL " + repr((got, exp)))
except Exception as e:
    print("FAIL " + repr(e))
''',
r'''
import base64
data = base64.b64decode(open("cert.b64").read())
def parse(b):
    i = 0; out = []
    while i < len(b):
        t = b[i]; i += 1
        l = b[i]; i += 1
        v = b[i:i+l]; i += l
        out.append((t, v))
    return out
cn = [None]
def walk(b):
    ch = parse(b)
    if len(ch) >= 2 and ch[0][0] == 0x06 and ch[0][1] == bytes([0x55, 4, 3]):
        cn[0] = ch[1][1]
    for t, v in ch:
        if t in (0x30, 0x31):
            walk(v)
walk(data)
open("answer.txt", "w").write(cn[0].decode())
'''))

# ---------------------------------------------------------------- 6. custom binary struct
TASKS.append(T("binstruct_records", "binstruct",
r'''
import struct
recs = [(101, 3.5), (102, 9.25), (103, 1.0), (104, 7.5)]
payload = b"".join(struct.pack(">If", i, s) for i, s in recs)
hdr = struct.pack(">4sHHI", b"RECS", 1, 0, len(recs))
open("data.bin", "wb").write(hdr + payload)
''',
"data.bin is a binary message: a 12-byte header big-endian struct '>4sHHI' = "
"(magic 'RECS', version uint16, flags uint16, count uint32), followed by `count` records, each an "
"8-byte big-endian struct '>If' = (id uint32, score float32). Parse it, find the record with the "
"highest score, and write that record's id (decimal) to answer.txt. Use struct.",
r'''
import struct
try:
    data = open("data.bin", "rb").read()
    magic, ver, flags, cnt = struct.unpack(">4sHHI", data[:12])
    assert magic == b"RECS"
    best = None
    for k in range(cnt):
        off = 12 + 8 * k
        rid, sc = struct.unpack(">If", data[off:off+8])
        if best is None or sc > best[1]:
            best = (rid, sc)
    got = int(open("answer.txt").read().strip())
    print("PASS" if got == best[0] else "FAIL " + repr((got, best)))
except Exception as e:
    print("FAIL " + repr(e))
''',
r'''
import struct
data = open("data.bin", "rb").read()
magic, ver, flags, cnt = struct.unpack(">4sHHI", data[:12])
best = None
for k in range(cnt):
    off = 12 + 8 * k
    rid, sc = struct.unpack(">If", data[off:off+8])
    if best is None or sc > best[1]:
        best = (rid, sc)
open("answer.txt", "w").write(str(best[0]))
'''))

# ---------------------------------------------------------------- 7. bit-packed decode
TASKS.append(T("bitpack", "bitpack",
r'''
v = (13 << 27) | (1337 << 16) | (42 << 10) | 555
open("packed.hex", "w").write("%08x" % v)
''',
"packed.hex is one 32-bit big-endian word in hex. It packs four fields, most-significant bit first: "
"A = 5 bits, B = 11 bits, C = 6 bits, D = 10 bits (A|B|C|D fills all 32 bits). Unpack and write "
"'B,D' (the decimal values of fields B and D, comma-separated, no spaces) to answer.txt.",
r'''
try:
    v = int(open("packed.hex").read().strip(), 16)
    B = (v >> 16) & 0x7ff
    D = v & 0x3ff
    exp = "%d,%d" % (B, D)
    got = open("answer.txt").read().strip()
    print("PASS" if got == exp else "FAIL " + repr((got, exp)))
except Exception as e:
    print("FAIL " + repr(e))
''',
r'''
v = int(open("packed.hex").read().strip(), 16)
B = (v >> 16) & 0x7ff
D = v & 0x3ff
open("answer.txt", "w").write("%d,%d" % (B, D))
'''))

# ---------------------------------------------------------------- 8. chunked decode
TASKS.append(T("chunked_decode", "chunked",
r'''
body = b"4\r\nWiki\r\n5\r\npedia\r\ne\r\n in\r\n\r\nchunks.\r\n0\r\n\r\n"
open("chunked.bin", "wb").write(body)
''',
"chunked.bin is an HTTP/1.1 chunked transfer-encoded body: repeated frames of "
"(hex-size CRLF, that many data bytes, CRLF), terminated by a 0-size chunk then CRLF. Decode it and "
"write the reassembled body bytes to out.txt. Note: chunk data may itself contain CR/LF bytes.",
r'''
try:
    data = open("chunked.bin", "rb").read()
    i = 0; out = b""
    while True:
        j = data.find(b"\r\n", i)
        size = int(data[i:j], 16); i = j + 2
        if size == 0:
            break
        out += data[i:i+size]; i += size + 2
    got = open("out.txt", "rb").read()
    print("PASS" if got == out else "FAIL " + repr((got, out)))
except Exception as e:
    print("FAIL " + repr(e))
''',
r'''
data = open("chunked.bin", "rb").read()
i = 0; out = b""
while True:
    j = data.find(b"\r\n", i)
    size = int(data[i:j], 16); i = j + 2
    if size == 0:
        break
    out += data[i:i+size]; i += size + 2
open("out.txt", "wb").write(out)
'''))

# ---------------------------------------------------------------- 9. multipart extract
TASKS.append(T("multipart_extract", "multipart",
r'''
b = "----b0undary42"
body = (
    "--" + b + "\r\n"
    'Content-Disposition: form-data; name="user"\r\n\r\nalice\r\n'
    "--" + b + "\r\n"
    'Content-Disposition: form-data; name="token"\r\n\r\ns3cr3t-abc.def\r\n'
    "--" + b + "\r\n"
    'Content-Disposition: form-data; name="note"\r\n\r\nhello world\r\n'
    "--" + b + "--\r\n"
)
open("multipart.bin", "wb").write(body.encode())
''',
"multipart.bin is a multipart/form-data body with boundary '----b0undary42'. Each part starts with "
"'--<boundary>' CRLF, a Content-Disposition header naming the field, a blank line, then the value, "
"then CRLF. Extract the value of the part whose name is 'token' and write just that value to "
"answer.txt.",
r'''
try:
    b = "----b0undary42"
    data = open("multipart.bin", "rb").read().decode()
    val = None
    for p in data.split("--" + b):
        if 'name="token"' in p:
            val = p.split("\r\n\r\n", 1)[1].rstrip("\r\n")
    got = open("answer.txt").read()
    if got.endswith("\n"):
        got = got[:-1]
    print("PASS" if got == val else "FAIL " + repr((got, val)))
except Exception as e:
    print("FAIL " + repr(e))
''',
r'''
b = "----b0undary42"
data = open("multipart.bin", "rb").read().decode()
val = None
for p in data.split("--" + b):
    if 'name="token"' in p:
        val = p.split("\r\n\r\n", 1)[1].rstrip("\r\n")
open("answer.txt", "w").write(val)
'''))

# ---------------------------------------------------------------- 10. JWT construct
TASKS.append(T("jwt_construct", "jwt",
r'''
open("secret.txt", "w").write("training-secret")
''',
"Construct a compact JWS (JWT) with HS256. Header must be {\"alg\":\"HS256\",\"typ\":\"JWT\"}; payload "
"must be {\"sub\":\"1234567890\",\"role\":\"admin\",\"iat\":1516239022}. Sign with HMAC-SHA256 using the "
"raw secret bytes in secret.txt. Use base64url with NO padding for all three segments, joined by dots, "
"and write the token to token.txt. Signature is over the ASCII of '<b64header>.<b64payload>'.",
r'''
import base64, json, hmac, hashlib
try:
    def b64d(x):
        return base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))
    tok = open("token.txt").read().strip()
    h, p, s = tok.split(".")
    hdr = json.loads(b64d(h))
    pl = json.loads(b64d(p))
    sec = open("secret.txt", "rb").read()
    sig = base64.urlsafe_b64encode(hmac.new(sec, (h + "." + p).encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    ok = (hdr == {"alg": "HS256", "typ": "JWT"}
          and pl == {"sub": "1234567890", "role": "admin", "iat": 1516239022}
          and s == sig and "=" not in tok)
    print("PASS" if ok else "FAIL " + repr((hdr, pl, s == sig)))
except Exception as e:
    print("FAIL " + repr(e))
''',
r'''
import base64, json, hmac, hashlib
def b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=")
h = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
p = b64(json.dumps({"sub": "1234567890", "role": "admin", "iat": 1516239022}, separators=(",", ":")).encode())
sec = open("secret.txt", "rb").read()
sig = b64(hmac.new(sec, h + b"." + p, hashlib.sha256).digest())
open("token.txt", "wb").write(h + b"." + p + b"." + sig)
'''))

# ---------------------------------------------------------------- 11. varint length-prefixed stream
TASKS.append(T("varint_stream", "framing",
r'''
def var(n):
    o = b""
    while True:
        b = n & 0x7f; n >>= 7
        if n:
            o += bytes([b | 0x80])
        else:
            o += bytes([b]); break
    return o
items = ["alpha", "beta", "gamma-δ", "this is a longer record number four"]
data = b"".join(var(len(s.encode())) + s.encode() for s in items)
open("stream.bin", "wb").write(data)
''',
"stream.bin is a sequence of length-prefixed records. Each record = a base-128 varint giving the "
"byte length of the following UTF-8 payload, then that many payload bytes. Parse the stream and write "
"the 3rd record (1-indexed), decoded as UTF-8, to answer.txt.",
r'''
try:
    data = open("stream.bin", "rb").read()
    def rv(b, i):
        r = 0; s = 0
        while True:
            x = b[i]; i += 1
            r |= (x & 0x7f) << s
            if not (x & 0x80):
                break
            s += 7
        return r, i
    i = 0; recs = []
    while i < len(data):
        ln, i = rv(data, i)
        recs.append(data[i:i+ln]); i += ln
    exp = recs[2]
    got = open("answer.txt", "rb").read()
    if got.endswith(b"\n"):
        got = got[:-1]
    print("PASS" if got == exp else "FAIL " + repr((got, exp)))
except Exception as e:
    print("FAIL " + repr(e))
''',
r'''
data = open("stream.bin", "rb").read()
def rv(b, i):
    r = 0; s = 0
    while True:
        x = b[i]; i += 1
        r |= (x & 0x7f) << s
        if not (x & 0x80):
            break
        s += 7
    return r, i
i = 0; recs = []
while i < len(data):
    ln, i = rv(data, i)
    recs.append(data[i:i+ln]); i += ln
open("answer.txt", "wb").write(recs[2])
'''))

# ---------------------------------------------------------------- 12. DNS QNAME extract
TASKS.append(T("dns_qname", "dns",
r'''
import struct
hdr = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
def enc(name):
    o = b""
    for lab in name.split("."):
        o += bytes([len(lab)]) + lab.encode()
    return o + b"\x00"
q = enc("www.example.com") + struct.pack(">HH", 1, 1)
open("dns.bin", "wb").write(hdr + q)
''',
"dns.bin is a raw DNS query message: a 12-byte header, then the question section. The QNAME is a "
"sequence of labels, each = one length byte then that many bytes, terminated by a zero length byte, "
"followed by QTYPE and QCLASS (2 bytes each). Decode the QNAME and write the domain (labels joined "
"by '.', no trailing dot) to answer.txt.",
r'''
try:
    data = open("dns.bin", "rb").read()
    i = 12; labels = []
    while True:
        ln = data[i]; i += 1
        if ln == 0:
            break
        labels.append(data[i:i+ln].decode()); i += ln
    exp = ".".join(labels)
    got = open("answer.txt").read().strip()
    print("PASS" if got == exp else "FAIL " + repr((got, exp)))
except Exception as e:
    print("FAIL " + repr(e))
''',
r'''
data = open("dns.bin", "rb").read()
i = 12; labels = []
while True:
    ln = data[i]; i += 1
    if ln == 0:
        break
    labels.append(data[i:i+ln].decode()); i += ln
open("answer.txt", "w").write(".".join(labels))
'''))

print(json.dumps({"tasks": TASKS}, indent=1))
