#!/usr/bin/env python3
"""Generate + self-validate CRYPTANALYSIS/CIPHER tasks. Emits tasks_crypto.json."""
import json, hashlib, struct, itertools, string, os, subprocess, tempfile, shutil

OUT="/private/tmp/claude-503/-Users-flash-gb10/9698b8ec-be3d-495b-98d5-10e0b424bba5/scratchpad/tasks_crypto.json"

# ============================================================
# crypto (used only to COMPUTE embedded constants; solvers reimplement)
# ============================================================
def rc4(key,data):
    S=list(range(256));j=0
    for i in range(256):
        j=(j+S[i]+key[i%len(key)])&0xFF; S[i],S[j]=S[j],S[i]
    out=bytearray();i=j=0
    for b in data:
        i=(i+1)&0xFF;j=(j+S[i])&0xFF;S[i],S[j]=S[j],S[i]
        out.append(b^S[(S[i]+S[j])&0xFF])
    return bytes(out)

# XOR repeating-key
XOR_KEY=bytes.fromhex("9e3779b97f4a7c15")
XOR_PT=b"LICENSE-KEY: PRO-EDITION-2026 seat=42 owner=acme-corp signed=ok"
XOR_CT=bytes(p^XOR_KEY[i%8] for i,p in enumerate(XOR_PT))
XOR_KNOWN=b"LICENSE-KEY: PRO-"

# LCG
LCG_A,LCG_C,LCG_M=1664525,1013904223,1<<32
def lcg_stream(seed,n):
    s=seed;out=bytearray()
    while len(out)<n:
        s=(LCG_A*s+LCG_C)%LCG_M; out+=struct.pack("<I",s)
    return bytes(out[:n])
LCG_SEED=0xDEADBEEF
LCG_PT=b"BEGIN-LICENSE\nproduct=analytics-suite\nexpires=2027-01-01\nseats=100\nEND\n"
LCG_CT=bytes(p^k for p,k in zip(LCG_PT,lcg_stream(LCG_SEED,len(LCG_PT))))
LCG_KNOWN=b"BEGIN-LICENSE"

# custom stream (FNV seed + xorshift32), pw = 4 lowercase
def seed_from_pw(pw):
    s=0x811C9DC5
    for c in pw: s=((s^ord(c))*16777619)&0xFFFFFFFF
    return s
def keystream(pw,n):
    s=seed_from_pw(pw);out=bytearray()
    for _ in range(n):
        s^=(s<<13)&0xFFFFFFFF; s^=(s>>17); s^=(s<<5)&0xFFFFFFFF; s&=0xFFFFFFFF
        out.append(s&0xFF)
    return bytes(out)
STREAM_PW="qxzw"
STREAM_PT=b"REPORT: quarterly revenue up 12 percent; bonus pool approved; ref=ZX99"
STREAM_CT=bytes(p^k for p,k in zip(STREAM_PT,keystream(STREAM_PW,len(STREAM_PT))))

# RC4 sample
RC4_KEY=b"S3cr3t!"
RC4_PT=b"transfer 5000 USD to account 7781"
RC4_CT=rc4(RC4_KEY,RC4_PT)

# hashes
WORDS=["correct","horse","battery","staple","hunter2","sunshine","dragon","letmein",
       "trustno1","password1","qwerty","monkey","admin123","zxcvbn","p@ssw0rd","ninja2024"]
MD5_SECRETS=["hunter2","dragon","p@ssw0rd"]
SHA1_SECRETS=["trustno1","sunshine","ninja2024"]
MD5_HASHES=[hashlib.md5(s.encode()).hexdigest() for s in MD5_SECRETS]
SHA1_HASHES=[hashlib.sha1(s.encode()).hexdigest() for s in SHA1_SECRETS]

# classic
VIG_CT="LXFOPVEFRNHRJLKIXTSDFMOYOCSIBSZBVIZAWAJRCXTSYLDKRBRRQOEELQFVGIDPNYO"
VIG_PT="ATTACKATDAWNXXXXTHEQUICKBROWNFOXJUMPSOVERTHELAZYDOGNEARTHERIVERBANK"
VIG_CRIB="ATTACKATDAWN"
AFF_CT="IHHWVCSWFRCPXCSPYFZWAVZIUGOWZRGVAOVFLIWVZCTZFIWP"
AFF_PT="AFFINECIPHERDECRYPTIONTASKWITHKNOWNPLAINTEXTPAIR"
AFF_CRIB="AFFINE"
HILL_CT="HIATWSTHMWNNQODSBQKOHVWQKOUDPFJZDT"
HILL_PT="HELPMEOBIWANKENOBIYOUREMYONLYHOPEX"
HILL_CRIB="HELPMEOB"

# cipher sample cts (computed in proto, recomputed here via known-good constants)
MAGMA_SAMPLE_CT="410b8817b8f7e955"
MAGMA_OFFICIAL_CT="4ee901e5c2d8ca3d"
KUZ_SAMPLE_CT="ba4b704ddaab14b12d6130a79d42c754"
KUZ_OFFICIAL_CT="7f679d90bebc24305a468d42b9d4edcd"

HDR_SETUP="#!/usr/bin/env bash\nset -e\ncd \"$W\"\n"
HDR_GRADE="#!/usr/bin/env bash\ncd \"$W\" || exit 1\n"
HDR_REF="#!/usr/bin/env bash\nset -e\ncd \"$W\"\n"

def T(id,category,setup,prompt,grader,ref):
    return dict(id=id,category=category,
                setup_sh=HDR_SETUP+setup,
                prompt=prompt,
                grader_sh=HDR_GRADE+grader,
                reference_solution_sh=HDR_REF+ref,
                self_test_passed=True)

TASKS=[]

# ---- reusable impl snippets for references ----
MAGMA_IMPL=r'''
SB=((12,4,6,2,10,5,11,9,14,8,13,7,0,3,15,1),(6,8,2,3,9,10,5,12,1,14,4,7,11,13,0,15),
(11,3,5,8,2,15,10,13,14,1,7,4,12,9,6,0),(12,8,2,1,13,4,15,6,7,0,10,5,3,14,9,11),
(7,15,5,10,8,1,6,13,0,9,3,14,11,4,2,12),(5,13,15,6,9,2,12,10,11,7,8,1,4,3,14,0),
(8,14,2,5,6,9,1,12,15,4,11,0,13,10,3,7),(1,7,14,13,0,5,8,3,4,15,10,6,9,12,11,2))
def t(x):
    r=0
    for i in range(8): r|=SB[i][(x>>(4*i))&0xF]<<(4*i)
    return r
def g(k,a):
    x=(a+k)&0xFFFFFFFF; x=t(x); return ((x<<11)|(x>>21))&0xFFFFFFFF
def keys(key):
    K=[(key>>(32*(7-i)))&0xFFFFFFFF for i in range(8)]; return K*3+K[::-1]
def enc(key,blk):
    ks=keys(key); a1=(blk>>32)&0xFFFFFFFF; a0=blk&0xFFFFFFFF
    for i in range(31): a1,a0=a0,a1^g(ks[i],a0)
    return ((a1^g(ks[31],a0))<<32)|a0
def dec(key,blk):
    ks=keys(key)[::-1]; a1=(blk>>32)&0xFFFFFFFF; a0=blk&0xFFFFFFFF
    for i in range(31): a1,a0=a0,a1^g(ks[i],a0)
    return ((a1^g(ks[31],a0))<<32)|a0
'''

KUZ_IMPL=r'''
PI=(252,238,221,17,207,110,49,22,251,196,250,218,35,197,4,77,233,119,240,219,147,46,153,186,23,54,241,187,20,205,95,193,
249,24,101,90,226,92,239,33,129,28,60,66,139,1,142,79,5,132,2,174,227,106,143,160,6,11,237,152,127,212,211,31,235,
52,44,81,234,200,72,171,242,42,104,162,253,58,206,204,181,112,14,86,8,12,118,18,191,114,19,71,156,183,93,135,21,161,
150,41,16,123,154,199,243,145,120,111,157,158,178,177,50,117,25,61,255,53,138,126,109,84,198,128,195,189,13,87,223,245,
36,169,62,168,67,201,215,121,214,246,124,34,185,3,224,15,236,222,122,148,176,188,220,232,40,80,78,51,10,74,167,151,
96,115,30,0,98,68,26,184,56,130,100,159,38,65,173,69,70,146,39,94,85,47,140,163,165,125,105,213,149,59,7,88,179,
64,134,172,29,247,48,55,107,228,136,217,231,137,225,27,131,73,76,63,248,254,141,83,170,144,202,216,133,97,32,113,103,
164,45,43,9,91,203,155,37,208,190,229,108,82,89,166,116,210,230,244,180,192,209,102,175,194,57,75,99,182)
PINV=[0]*256
for i,v in enumerate(PI): PINV[v]=i
LC=(148,32,133,16,194,192,1,251,1,192,194,16,133,32,148,1)
def gmul(a,b):
    p=0
    for _ in range(8):
        if b&1:p^=a
        hi=a&0x80;a=(a<<1)&0xFF
        if hi:a^=0xC3
        b>>=1
    return p
def R(b):
    s=0
    for i in range(16): s^=gmul(b[i],LC[i])
    return bytes([s])+b[:15]
def L(b):
    for _ in range(16): b=R(b)
    return b
def Rinv(b):
    prev=b[1:]+bytes([0]); s=b[0]
    for i in range(15): s^=gmul(prev[i],LC[i])
    return prev[:15]+bytes([s])
def Linv(b):
    for _ in range(16): b=Rinv(b)
    return b
def S(b): return bytes(PI[x] for x in b)
def Sinv(b): return bytes(PINV[x] for x in b)
def X(a,b): return bytes(x^y for x,y in zip(a,b))
def C():
    return [L(bytes([0]*15+[i])) for i in range(1,33)]
def kk(key):
    Cc=C(); a,b=key[:16],key[16:]; keys=[a,b]
    for i in range(4):
        for j in range(8):
            inner=L(S(X(a,Cc[i*8+j]))); a,b=X(inner,b),a
        keys.append(a);keys.append(b)
    return keys
def enc(key,blk):
    K=kk(key);b=blk
    for i in range(9): b=L(S(X(b,K[i])))
    return X(b,K[9])
def dec(key,blk):
    K=kk(key);b=X(blk,K[9])
    for i in range(8,-1,-1): b=X(Sinv(Linv(b)),K[i])
    return b
'''

# ============================================================
# TASK 1: XOR repeating-key KPA
# ============================================================
setup=("printf '%s' \"" + XOR_CT.hex() + "\" > ct.hex\n"
       "printf '%s' \"" + XOR_KNOWN.decode() + "\" > known.txt\n")
prompt=("ct.hex holds a hex-encoded license blob encrypted with a repeating-key XOR stream, "
        "key length 8 bytes. known.txt is the known plaintext prefix. Recover the 8-byte key, "
        "decrypt the whole blob, and write the recovered plaintext BYTES to plain.txt "
        "(exact bytes, no trailing newline). Optionally write the key hex to key.hex.")
grader=("python3 - <<'PY'\n"
        "try:\n"
        "    exp=bytes.fromhex('"+XOR_PT.hex()+"')\n"
        "    got=open('plain.txt','rb').read()\n"
        "    print('PASS' if got==exp else 'FAIL')\n"
        "except Exception as e: print('FAIL:'+str(e))\n"
        "PY")
ref=("python3 - <<'PY'\n"
     "ct=bytes.fromhex(open('ct.hex').read().strip())\n"
     "kn=open('known.txt','rb').read()\n"
     "key=bytes(ct[i]^kn[i] for i in range(8))\n"
     "pt=bytes(ct[i]^key[i%8] for i in range(len(ct)))\n"
     "open('plain.txt','wb').write(pt)\n"
     "open('key.hex','w').write(key.hex())\n"
     "PY")
TASKS.append(T("ca_xor_license_kpa","kpa-xor",setup,prompt,grader,ref))

# ============================================================
# TASK 2: LCG keystream KPA
# ============================================================
setup=("printf '%s' \"" + LCG_CT.hex() + "\" > ct.hex\n"
       "printf '%s' \"" + LCG_KNOWN.decode() + "\" > known.txt\n")
prompt=("ct.hex is a hex license file XORed with a keystream from a 32-bit LCG: "
        "state = (1664525*state + 1013904223) mod 2**32, and each successive state is emitted as "
        "4 little-endian bytes of keystream (the FIRST keystream block is the state right after the "
        "first update). known.txt gives the known plaintext prefix. Recover the keystream, decrypt "
        "the file, and write the recovered plaintext BYTES to plain.txt (exact bytes, no trailing newline).")
grader=("python3 - <<'PY'\n"
        "try:\n"
        "    exp=bytes.fromhex('"+LCG_PT.hex()+"')\n"
        "    got=open('plain.txt','rb').read()\n"
        "    print('PASS' if got==exp else 'FAIL')\n"
        "except Exception as e: print('FAIL:'+str(e))\n"
        "PY")
ref=("python3 - <<'PY'\n"
     "import struct\n"
     "A,C,M=1664525,1013904223,1<<32\n"
     "ct=bytes.fromhex(open('ct.hex').read().strip())\n"
     "kn=open('known.txt','rb').read()\n"
     "s=struct.unpack('<I',bytes(ct[i]^kn[i] for i in range(4)))[0]\n"
     "ks=bytearray(struct.pack('<I',s))\n"
     "while len(ks)<len(ct):\n"
     "    s=(A*s+C)%M; ks+=struct.pack('<I',s)\n"
     "pt=bytes(ct[i]^ks[i] for i in range(len(ct)))\n"
     "open('plain.txt','wb').write(pt)\n"
     "PY")
TASKS.append(T("ca_lcg_keystream_kpa","kpa-lcg",setup,prompt,grader,ref))

# ============================================================
# TASK 3: nonlinear license key brute
# ============================================================
setup=("cat > validate.py <<'PY'\n"
       "def validate(k):\n"
       "    if not (0 <= k < 10000000): return False\n"
       "    if (k*k) % 100003 != 12345: return False\n"
       "    if (k ^ 0x5A5A5A) % 97 != 3: return False\n"
       "    if (k % 13) != 7: return False\n"
       "    return True\n"
       "PY\n")
prompt=("validate.py defines validate(k) for an integer license key k. Find any integer that "
        "satisfies it (z3 is not installed; use brute force or algebra) and write it as a decimal "
        "string to key.txt.")
grader=("python3 - <<'PY'\n"
        "try:\n"
        "    import validate\n"
        "    k=int(open('key.txt').read().strip())\n"
        "    print('PASS' if validate.validate(k) else 'FAIL')\n"
        "except Exception as e: print('FAIL:'+str(e))\n"
        "PY")
ref=("python3 - <<'PY'\n"
     "import validate\n"
     "for k in range(10000000):\n"
     "    if validate.validate(k):\n"
     "        open('key.txt','w').write(str(k)); break\n"
     "PY")
TASKS.append(T("ca_license_key_brute","key-solve",setup,prompt,grader,ref))

# ============================================================
# TASK 4: Magma implement + decrypt
# ============================================================
setup=("printf '%s' \"" + MAGMA_SAMPLE_CT + "\" > sample_ct.hex\n"
       "cat > spec.md <<'EOF'\n"
       "GOST R 34.12-2015 Magma (64-bit block, 256-bit key, 32 rounds).\n"
       "Key K = ffeeddccbbaa99887766554433221100f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff\n"
       "  split into K1..K8 big-endian (K1 = most significant 32 bits).\n"
       "Round keys: rounds 1-24 use K1..K8 (three times), rounds 25-32 use K8..K1.\n"
       "Round function g[k](a): x=(a+k) mod 2^32; substitute each of the 8 nibbles i (i=0=LSB)\n"
       "  through S-box row i; then cyclic-left-rotate the 32-bit result by 11.\n"
       "Feistel: (a1,a0)->(a0, a1 XOR g[k](a0)); 31 rounds swap, final round does not swap.\n"
       "S-boxes (row 0..7, low nibble to high nibble):\n"
       "0: 12,4,6,2,10,5,11,9,14,8,13,7,0,3,15,1\n"
       "1: 6,8,2,3,9,10,5,12,1,14,4,7,11,13,0,15\n"
       "2: 11,3,5,8,2,15,10,13,14,1,7,4,12,9,6,0\n"
       "3: 12,8,2,1,13,4,15,6,7,0,10,5,3,14,9,11\n"
       "4: 7,15,5,10,8,1,6,13,0,9,3,14,11,4,2,12\n"
       "5: 5,13,15,6,9,2,12,10,11,7,8,1,4,3,14,0\n"
       "6: 8,14,2,5,6,9,1,12,15,4,11,0,13,10,3,7\n"
       "7: 1,7,14,13,0,5,8,3,4,15,10,6,9,12,11,2\n"
       "Official test vector: plaintext fedcba9876543210 -> ciphertext 4ee901e5c2d8ca3d.\n"
       "sample_ct.hex is a ciphertext produced with the SAME key K.\n"
       "Write out.txt with exactly two lines:\n"
       "  line1 = hex of encrypt(K, 0xfedcba9876543210)  (must be 4ee901e5c2d8ca3d)\n"
       "  line2 = hex (16 lowercase digits) of decrypt(K, sample_ct)\n"
       "EOF\n")
prompt=("Implement GOST Magma per spec.md, confirm the official test vector, then decrypt the "
        "ciphertext in sample_ct.hex using the same key. Write out.txt: line1 = hex of encrypting "
        "0xfedcba9876543210, line2 = 16-hex-digit decryption of sample_ct.")
grader=("python3 - <<'PY'\n"
        "try:\n"
        "    L=[x.strip().lower() for x in open('out.txt') if x.strip()]\n"
        "    ok = L[0]=='"+MAGMA_OFFICIAL_CT+"' and L[1]=='0011223344556677'\n"
        "    print('PASS' if ok else 'FAIL')\n"
        "except Exception as e: print('FAIL:'+str(e))\n"
        "PY")
ref=("cat > solve.py <<'PY'\n"+MAGMA_IMPL+
     "KEY=0xffeeddccbbaa99887766554433221100f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff\n"
     "sc=int(open('sample_ct.hex').read().strip(),16)\n"
     "l1='%016x'%enc(KEY,0xfedcba9876543210)\n"
     "l2='%016x'%dec(KEY,sc)\n"
     "open('out.txt','w').write(l1+'\\n'+l2+'\\n')\n"
     "PY\npython3 solve.py\n")
TASKS.append(T("ca_magma_vectors","cipher-impl",setup,prompt,grader,ref))

# ============================================================
# TASK 5: Kuznyechik implement + decrypt
# ============================================================
setup=("printf '%s' \"" + KUZ_SAMPLE_CT + "\" > sample_ct.hex\n"
       "cat > spec.md <<'EOF'\n"
       "GOST R 34.12-2015 Kuznyechik (128-bit block, 256-bit key, 10 rounds).\n"
       "Key = 8899aabbccddeeff0011223344556677fedcba98765432100123456789abcdef\n"
       "Round op per round i (1..9): X[k_i] then S then L; round 10: X[k_10].\n"
       "  X[k](a)=a XOR k. S = byte substitution via table PI (below). L = R applied 16 times.\n"
       "  R(a15..a0): l = XOR over i of GF_mul(a_i, LC[i]); output = (l, a15, ..., a1),\n"
       "    i.e. a linear feedback that shifts one byte in. Byte 0 is the leftmost (most significant).\n"
       "GF multiply is mod x^8+x^7+x^6+x+1 (reduction byte 0xC3).\n"
       "LC = 148,32,133,16,194,192,1,251,1,192,194,16,133,32,148,1  (LC[0] pairs with leftmost byte).\n"
       "Key schedule: k1=key[0:16], k2=key[16:32]. Constants C_i = L( bytes([0]*15+[i]) ) for i=1..32.\n"
       "  For n=0..3: a,b=k_{2n+1},k_{2n+2}; repeat 8 times (j=0..7 using C_{8n+j+1}):\n"
       "    a,b = ( X( L(S( X(a, C) )), b ), a ); then k_{2n+3}=a, k_{2n+4}=b. Yields k1..k10.\n"
       "PI table (256 bytes):\n"
       "252,238,221,17,207,110,49,22,251,196,250,218,35,197,4,77,233,119,240,219,147,46,153,186,23,54,241,187,20,205,95,193,\n"
       "249,24,101,90,226,92,239,33,129,28,60,66,139,1,142,79,5,132,2,174,227,106,143,160,6,11,237,152,127,212,211,31,235,\n"
       "52,44,81,234,200,72,171,242,42,104,162,253,58,206,204,181,112,14,86,8,12,118,18,191,114,19,71,156,183,93,135,21,161,\n"
       "150,41,16,123,154,199,243,145,120,111,157,158,178,177,50,117,25,61,255,53,138,126,109,84,198,128,195,189,13,87,223,245,\n"
       "36,169,62,168,67,201,215,121,214,246,124,34,185,3,224,15,236,222,122,148,176,188,220,232,40,80,78,51,10,74,167,151,\n"
       "96,115,30,0,98,68,26,184,56,130,100,159,38,65,173,69,70,146,39,94,85,47,140,163,165,125,105,213,149,59,7,88,179,\n"
       "64,134,172,29,247,48,55,107,228,136,217,231,137,225,27,131,73,76,63,248,254,141,83,170,144,202,216,133,97,32,113,103,\n"
       "164,45,43,9,91,203,155,37,208,190,229,108,82,89,166,116,210,230,244,180,192,209,102,175,194,57,75,99,182\n"
       "Official test vector: plaintext 1122334455667700ffeeddccbbaa9988 -> 7f679d90bebc24305a468d42b9d4edcd.\n"
       "sample_ct.hex is encrypted with the SAME key. Write out.txt two lines:\n"
       "  line1 = hex of encrypt(plaintext 1122334455667700ffeeddccbbaa9988)  (must be 7f679d90bebc24305a468d42b9d4edcd)\n"
       "  line2 = 32-hex-digit decrypt(sample_ct).\n"
       "EOF\n")
prompt=("Implement GOST Kuznyechik per spec.md, confirm the official test vector, then decrypt "
        "sample_ct.hex with the same key. Write out.txt: line1 = hex of encrypting "
        "1122334455667700ffeeddccbbaa9988, line2 = 32-hex-digit decryption of sample_ct.")
grader=("python3 - <<'PY'\n"
        "try:\n"
        "    L=[x.strip().lower() for x in open('out.txt') if x.strip()]\n"
        "    ok = L[0]=='"+KUZ_OFFICIAL_CT+"' and L[1]=='00112233445566778899aabbccddeeff'\n"
        "    print('PASS' if ok else 'FAIL')\n"
        "except Exception as e: print('FAIL:'+str(e))\n"
        "PY")
ref=("cat > solve.py <<'PY'\n"+KUZ_IMPL+
     "KEY=bytes.fromhex('8899aabbccddeeff0011223344556677fedcba98765432100123456789abcdef')\n"
     "sc=bytes.fromhex(open('sample_ct.hex').read().strip())\n"
     "pt=bytes.fromhex('1122334455667700ffeeddccbbaa9988')\n"
     "l1=enc(KEY,pt).hex()\n"
     "l2=dec(KEY,sc).hex()\n"
     "open('out.txt','w').write(l1+'\\n'+l2+'\\n')\n"
     "PY\npython3 solve.py\n")
TASKS.append(T("ca_kuznyechik_vectors","cipher-impl",setup,prompt,grader,ref))

# ============================================================
# TASK 6: custom stream cipher, brute password
# ============================================================
setup=("printf '%s' \"" + STREAM_CT.hex() + "\" > ct.hex\n"
       "cat > keygen.py <<'PY'\n"
       "# Keystream generator. Password is 4 lowercase letters (a-z).\n"
       "def seed_from_pw(pw):\n"
       "    s=0x811C9DC5\n"
       "    for c in pw: s=((s ^ ord(c))*16777619) & 0xFFFFFFFF\n"
       "    return s\n"
       "def keystream(pw,n):\n"
       "    s=seed_from_pw(pw); out=bytearray()\n"
       "    for _ in range(n):\n"
       "        s^=(s<<13)&0xFFFFFFFF; s^=(s>>17); s^=(s<<5)&0xFFFFFFFF; s&=0xFFFFFFFF\n"
       "        out.append(s & 0xFF)\n"
       "    return bytes(out)\n"
       "PY\n")
prompt=("ct.hex is a message XORed with keystream(pw, len) from keygen.py, where pw is 4 lowercase "
        "letters. The plaintext is known to start with the ASCII bytes 'REPORT:'. Brute-force the "
        "password, decrypt the message, and write the recovered plaintext BYTES to plain.txt "
        "(exact bytes, no trailing newline). Optionally write the password to pw.txt.")
grader=("python3 - <<'PY'\n"
        "try:\n"
        "    exp=bytes.fromhex('"+STREAM_PT.hex()+"')\n"
        "    got=open('plain.txt','rb').read()\n"
        "    print('PASS' if got==exp else 'FAIL')\n"
        "except Exception as e: print('FAIL:'+str(e))\n"
        "PY")
ref=("python3 - <<'PY'\n"
     "import itertools, string, keygen\n"
     "ct=bytes.fromhex(open('ct.hex').read().strip())\n"
     "crib=b'REPORT:'\n"
     "for c in itertools.product(string.ascii_lowercase,repeat=4):\n"
     "    pw=''.join(c); ks=keygen.keystream(pw,len(crib))\n"
     "    if bytes(ct[i]^ks[i] for i in range(len(crib)))==crib:\n"
     "        full=keygen.keystream(pw,len(ct))\n"
     "        open('plain.txt','wb').write(bytes(ct[i]^full[i] for i in range(len(ct))))\n"
     "        open('pw.txt','w').write(pw); break\n"
     "PY")
TASKS.append(T("ca_custom_stream_recover","key-brute",setup,prompt,grader,ref))

# ============================================================
# TASK 7: MD5 crack
# ============================================================
setup=("cat > hashes.txt <<'EOF'\n"+"\n".join(MD5_HASHES)+"\nEOF\n"
       "cat > wordlist.txt <<'EOF'\n"+"\n".join(WORDS)+"\nEOF\n")
prompt=("hashes.txt has 3 unsalted MD5 hex digests (one per line). wordlist.txt is a candidate "
        "password list. Crack each hash against the wordlist and write cracked.txt with the "
        "recovered plaintext for each hash, one per line, in the SAME order as hashes.txt.")
grader=("python3 - <<'PY'\n"
        "import hashlib\n"
        "try:\n"
        "    H=[h.strip() for h in open('hashes.txt') if h.strip()]\n"
        "    C=[l.rstrip('\\n') for l in open('cracked.txt')]\n"
        "    C=[c for c in C]\n"
        "    ok=len(C)==len(H) and all(hashlib.md5(C[i].encode()).hexdigest()==H[i] for i in range(len(H)))\n"
        "    print('PASS' if ok else 'FAIL')\n"
        "except Exception as e: print('FAIL:'+str(e))\n"
        "PY")
ref=("python3 - <<'PY'\n"
     "import hashlib\n"
     "H=[h.strip() for h in open('hashes.txt') if h.strip()]\n"
     "W=[w.strip() for w in open('wordlist.txt') if w.strip()]\n"
     "m={hashlib.md5(w.encode()).hexdigest():w for w in W}\n"
     "open('cracked.txt','w').write('\\n'.join(m[h] for h in H))\n"
     "PY")
TASKS.append(T("ca_md5_crack","hash-crack",setup,prompt,grader,ref))

# ============================================================
# TASK 8: SHA1 crack
# ============================================================
setup=("cat > hashes.txt <<'EOF'\n"+"\n".join(SHA1_HASHES)+"\nEOF\n"
       "cat > wordlist.txt <<'EOF'\n"+"\n".join(WORDS)+"\nEOF\n")
prompt=("hashes.txt has 3 unsalted SHA-1 hex digests (one per line). wordlist.txt is a candidate "
        "password list. Crack each hash against the wordlist and write cracked.txt with the "
        "recovered plaintext for each hash, one per line, in the SAME order as hashes.txt.")
grader=("python3 - <<'PY'\n"
        "import hashlib\n"
        "try:\n"
        "    H=[h.strip() for h in open('hashes.txt') if h.strip()]\n"
        "    C=[l.rstrip('\\n') for l in open('cracked.txt')]\n"
        "    ok=len(C)==len(H) and all(hashlib.sha1(C[i].encode()).hexdigest()==H[i] for i in range(len(H)))\n"
        "    print('PASS' if ok else 'FAIL')\n"
        "except Exception as e: print('FAIL:'+str(e))\n"
        "PY")
ref=("python3 - <<'PY'\n"
     "import hashlib\n"
     "H=[h.strip() for h in open('hashes.txt') if h.strip()]\n"
     "W=[w.strip() for w in open('wordlist.txt') if w.strip()]\n"
     "m={hashlib.sha1(w.encode()).hexdigest():w for w in W}\n"
     "open('cracked.txt','w').write('\\n'.join(m[h] for h in H))\n"
     "PY")
TASKS.append(T("ca_sha1_crack","hash-crack",setup,prompt,grader,ref))

# ============================================================
# TASK 9: RC4 implement + decrypt
# ============================================================
setup=("printf '%s' \"" + RC4_CT.hex() + "\" > sample_ct.hex\n"
       "printf '%s' \"S3cr3t!\" > key.txt\n"
       "cat > spec.md <<'EOF'\n"
       "RC4 stream cipher. KSA: S=0..255; j=0; for i in 0..255: j=(j+S[i]+key[i mod keylen])&255; swap S[i],S[j].\n"
       "PRGA: i=j=0; for each byte: i=(i+1)&255; j=(j+S[i])&255; swap; out = byte XOR S[(S[i]+S[j])&255].\n"
       "Official test vector: key b'Key', plaintext b'Plaintext' -> ciphertext bbf316e8d940af0ad3.\n"
       "key.txt holds the key (ASCII, no newline) for sample_ct.hex.\n"
       "Write out.txt two lines:\n"
       "  line1 = hex of RC4(b'Key', b'Plaintext')  (must be bbf316e8d940af0ad3)\n"
       "  line2 = ASCII decryption of sample_ct.hex under the key in key.txt.\n"
       "EOF\n")
prompt=("Implement RC4 per spec.md, confirm the official test vector, then decrypt sample_ct.hex "
        "with the key in key.txt (RC4 decryption == encryption). Write out.txt: line1 = hex of "
        "RC4(b'Key', b'Plaintext'), line2 = the ASCII plaintext of sample_ct.")
grader=("python3 - <<'PY'\n"
        "try:\n"
        "    L=open('out.txt').read().split('\\n')\n"
        "    exp2='"+RC4_PT.decode()+"'\n"
        "    ok=L[0].strip().lower()=='bbf316e8d940af0ad3' and L[1]==exp2\n"
        "    print('PASS' if ok else 'FAIL')\n"
        "except Exception as e: print('FAIL:'+str(e))\n"
        "PY")
ref=("python3 - <<'PY'\n"
     "def rc4(key,data):\n"
     "    S=list(range(256));j=0\n"
     "    for i in range(256): j=(j+S[i]+key[i%len(key)])&255; S[i],S[j]=S[j],S[i]\n"
     "    out=bytearray();i=j=0\n"
     "    for b in data:\n"
     "        i=(i+1)&255;j=(j+S[i])&255;S[i],S[j]=S[j],S[i]\n"
     "        out.append(b^S[(S[i]+S[j])&255])\n"
     "    return bytes(out)\n"
     "key=open('key.txt','rb').read()\n"
     "sc=bytes.fromhex(open('sample_ct.hex').read().strip())\n"
     "l1=rc4(b'Key',b'Plaintext').hex()\n"
     "l2=rc4(key,sc).decode()\n"
     "open('out.txt','w').write(l1+'\\n'+l2+'\\n')\n"
     "PY")
TASKS.append(T("ca_rc4_vectors","cipher-impl",setup,prompt,grader,ref))

# ============================================================
# TASK 10: Vigenere crib KPA
# ============================================================
setup=("printf '%s' \"" + VIG_CT + "\" > ct.txt\n"
       "printf '%s' \"" + VIG_CRIB + "\" > crib.txt\n")
prompt=("ct.txt is a Vigenere ciphertext (uppercase A-Z, no spaces). crib.txt is the known plaintext "
        "prefix. The key length is unknown. Recover the repeating key, decrypt the whole message, and "
        "write the recovered plaintext (uppercase, no spaces) to plain.txt.")
grader=("python3 - <<'PY'\n"
        "try:\n"
        "    exp='"+VIG_PT+"'\n"
        "    got=open('plain.txt').read().strip()\n"
        "    print('PASS' if got==exp else 'FAIL')\n"
        "except Exception as e: print('FAIL:'+str(e))\n"
        "PY")
ref=("python3 - <<'PY'\n"
     "ct=open('ct.txt').read().strip(); crib=open('crib.txt').read().strip()\n"
     "rk=[(ord(ct[i])-ord(crib[i]))%26 for i in range(len(crib))]\n"
     "def per(s):\n"
     "    for p in range(1,len(s)+1):\n"
     "        if all(s[i]==s[i%p] for i in range(len(s))): return p\n"
     "    return len(s)\n"
     "p=per(rk); key=[rk[i] for i in range(p)]\n"
     "pt=''.join(chr((ord(ct[i])-65-key[i%p])%26+65) for i in range(len(ct)))\n"
     "open('plain.txt','w').write(pt)\n"
     "PY")
TASKS.append(T("ca_vigenere_crib","classic-kpa",setup,prompt,grader,ref))

# ============================================================
# TASK 11: Affine cipher solve
# ============================================================
setup=("printf '%s' \"" + AFF_CT + "\" > ct.txt\n"
       "printf '%s' \"" + AFF_CRIB + "\" > crib.txt\n")
prompt=("ct.txt is an affine-cipher ciphertext over A-Z: E(x) = (a*x + b) mod 26. crib.txt is the "
        "known plaintext prefix. Recover a and b, decrypt the whole message, and write the plaintext "
        "(uppercase) to plain.txt.")
grader=("python3 - <<'PY'\n"
        "try:\n"
        "    exp='"+AFF_PT+"'\n"
        "    got=open('plain.txt').read().strip()\n"
        "    print('PASS' if got==exp else 'FAIL')\n"
        "except Exception as e: print('FAIL:'+str(e))\n"
        "PY")
ref=("python3 - <<'PY'\n"
     "ct=open('ct.txt').read().strip(); crib=open('crib.txt').read().strip()\n"
     "def inv(a):\n"
     "    for i in range(26):\n"
     "        if (a*i)%26==1: return i\n"
     "p1=ord(crib[0])-65;p2=ord(crib[1])-65;c1=ord(ct[0])-65;c2=ord(ct[1])-65\n"
     "a=((c1-c2)*inv((p1-p2)%26))%26; b=(c1-a*p1)%26; ai=inv(a)\n"
     "pt=''.join(chr((ai*((ord(ch)-65)-b))%26+65) for ch in ct)\n"
     "open('plain.txt','w').write(pt)\n"
     "PY")
TASKS.append(T("ca_affine_solve","classic-kpa",setup,prompt,grader,ref))

# ============================================================
# TASK 12: Hill 2x2 KPA
# ============================================================
setup=("printf '%s' \"" + HILL_CT + "\" > ct.txt\n"
       "printf '%s' \"" + HILL_CRIB + "\" > crib.txt\n")
prompt=("ct.txt is a Hill-cipher ciphertext over A-Z using a 2x2 key matrix mod 26 (plaintext taken "
        "two letters at a time as a column vector, C = K*P mod 26). crib.txt is the known plaintext "
        "prefix (at least 4 letters). Recover K, decrypt the whole message, and write the plaintext "
        "(uppercase) to plain.txt.")
grader=("python3 - <<'PY'\n"
        "try:\n"
        "    exp='"+HILL_PT+"'\n"
        "    got=open('plain.txt').read().strip()\n"
        "    print('PASS' if got==exp else 'FAIL')\n"
        "except Exception as e: print('FAIL:'+str(e))\n"
        "PY")
ref=("python3 - <<'PY'\n"
     "ct=open('ct.txt').read().strip(); crib=open('crib.txt').read().strip()\n"
     "def inv(a):\n"
     "    for i in range(26):\n"
     "        if (a*i)%26==1: return i\n"
     "def minv(m):\n"
     "    det=(m[0][0]*m[1][1]-m[0][1]*m[1][0])%26; di=inv(det)\n"
     "    return [[(m[1][1]*di)%26,(-m[0][1]*di)%26],[(-m[1][0]*di)%26,(m[0][0]*di)%26]]\n"
     "def mul(A,B):\n"
     "    return [[sum(A[i][k]*B[k][j] for k in range(2))%26 for j in range(2)] for i in range(2)]\n"
     "pn=[ord(c)-65 for c in crib]; cn=[ord(c)-65 for c in ct]\n"
     "P=[[pn[0],pn[2]],[pn[1],pn[3]]]; C=[[cn[0],cn[2]],[cn[1],cn[3]]]\n"
     "K=mul(C,minv(P)); Ki=minv(K)\n"
     "n=[ord(c)-65 for c in ct]; out=[]\n"
     "for i in range(0,len(n),2):\n"
     "    x0,x1=n[i],n[i+1]\n"
     "    out.append((Ki[0][0]*x0+Ki[0][1]*x1)%26); out.append((Ki[1][0]*x0+Ki[1][1]*x1)%26)\n"
     "open('plain.txt','w').write(''.join(chr(v+65) for v in out))\n"
     "PY")
TASKS.append(T("ca_hill2x2_kpa","classic-kpa",setup,prompt,grader,ref))

# ============================================================
# SELF-TEST HARNESS
# ============================================================
def run(script, W):
    p=subprocess.run(["bash","-c",script],cwd=W,env={**os.environ,"W":W},
                     capture_output=True,text=True,timeout=120)
    return p

def grade_out(p):
    out=(p.stdout or "").strip().splitlines()
    return out[-1].strip() if out else ""

passing=[]
report=[]
for t in TASKS:
    W=tempfile.mkdtemp(prefix="ca_")
    try:
        rs=run(t["setup_sh"],W)
        if rs.returncode!=0:
            report.append((t["id"],"SETUP_ERR",rs.stderr[-300:])); continue
        g0=run(t["grader_sh"],W); g0v=grade_out(g0)
        pre_fail = (g0v!="PASS")
        rr=run(t["reference_solution_sh"],W)
        ref_ok = rr.returncode==0
        g1=run(t["grader_sh"],W); g1v=grade_out(g1)
        post_pass = (g1v=="PASS")
        ok = pre_fail and ref_ok and post_pass
        report.append((t["id"], "OK" if ok else "BAD",
                       f"pre={g0v!r} refrc={rr.returncode} post={g1v!r} referr={rr.stderr[-200:] if not ref_ok else ''}"))
        if ok: passing.append(t)
    finally:
        shutil.rmtree(W,ignore_errors=True)

print("=== SELF-TEST REPORT ===")
for r in report: print(r[0].ljust(28), r[1].ljust(6), r[2])
print(f"\nPASSING {len(passing)}/{len(TASKS)}")

with open(OUT,"w") as f:
    json.dump({"tasks":passing}, f, indent=1)
print("wrote", OUT)
