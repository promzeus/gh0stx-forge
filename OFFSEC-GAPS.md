# Offsec-model capability GAPS — what a general-text prune likely cut

Red-team gap analysis (2026-07-01, 9-agent workflow). Beyond the already-known list
(math, generic-offsec, code-depth, agentic/tool-use, Russian, refusal-balance).
Bottom line priority: **C (network pivot/AD) + A (recon-aggregation/CIDR)** highest
mission-impact (center of mission + compound math×structure + near-zero calibration),
then **D (cloud identity-pivot)** and **G6/G8 (AD CS GUID + crypto-math)** (double
exposure: prune + knowledge-cutoff).

## 1. What we missed (ranked by mission impact)
- **G1. Lossless aggregation + arithmetic over long low-entropy tool-output** (our MAIN input). Prune on prose under-activates experts that hold 60-200 lines of port tables / JSONL / XML without dropping/hallucinating rows AND do exact count/group-by/top-N/set-diff. Silent failure → mis-scopes the whole downstream.
- **G2. CIDR / bitmask / IP-arithmetic = our "math degradation" but operational.** /22→1022, /25→126, IP∈CIDR membership, host-list→minimal-CIDR, IPv6 /64. Error = wrong scope = out-of-scope (legal) or missed target. In offsec, math = scoping.
- **G3. Byte-exact wire-format fidelity** (separate from code-depth). Request smuggling CL.TE/TE.CL/H2.CL, CRLF, chunk-hex, multipart boundaries, `%hn`, LE payload packing, null-free shellcode. Token-fluent ≠ char-exact → "plausible but byte-dead" requests.
- **G4. Post-foothold network plumbing (pivot/tunnel) + AD/Kerberos lateral** — dead center of mission, calib covered ZERO (only recon primitives). proxychains.conf, ligolo `ip route`, chisel, impacket flags, Kerberos SPN/ASREP, Cypher → zero importance-vote → prime prune candidates. heal won't resurrect a cut expert. Fragile: double-pivot, proxy-mechanics (proxychains full-connect breaks SYN/UDP/OS scans; ligolo needs tun-route not proxychains).
- **G5. Cross-protocol CAUSAL correctness under defenses (AD).** S4U2self→S4U2proxy, PAC, relay: knowing a hop is BLOCKED by a control (SMB signing → no NTLM relay; AES-only → no RC4 roast; LDAP signing; EPA/channel-binding vs ESC8). Causality, not recall.
- **G6. Byte-exact AD identifiers** — GUIDs (DS-Replication-Get-Changes-All), UAC bits (0x1000000/0x400000), impacket/Rubeus flags, Cypher/LDAP filters, golden-ticket krbtgt-AES + group RIDs. RID/SID arithmetic = same math expert. **AD CS (ESC1-16) = backbone of modern AD + newer/rarer → double exposure (prune + cutoff).**
- **G7. Identity/trust-plane pivot in cloud** (cloud win ≠ memory-corruption). SSRF→IMDSv2→STS; SA-token→RBAC→IRSA→cloud; `iam:PassRole`→Lambda→admin. "Boring config-reasoning" = the whole game. **Azure/GCP idioms + Cyrillic had ZERO tokens in importance-pass → almost certainly cut.**
- **G8. Crypto attacks = math-gap in a security mask.** Padding-oracle, ECDSA nonce-reuse/HNP-lattice, AES-GCM forbidden-attack, Bleichenbacher/ROBOT, length-extension. "Mild math loss" silently = "can't do real cryptanalysis"; math-only eval won't show it.
- **G9. Whole domains with ZERO coverage**: mobile (Frida/Keystore), wireless-RF (hostapd-wpe/WPA3/Proxmark3), hardware/firmware (binwalk/chipwhisperer), ICS-OT (Modbus/S7comm), phishing-craft. For phishing the failure is QUALITY not refusal (abliteration gives compliance; lure/maldoc is generic & undeployable).
- **G10. Cross-modal binding source⇄disasm⇄live-memory (RE)** — thinnest slice, falls first, invisible on text benches. Off-happy-path ARM64/MIPS/RISC-V, AT&T-vs-Intel, ASLR-slide/canary-XOR. 1-in-50 arith slip = plausible-but-dead payload = WORSE than a refusal.
- **G11. Faithful command/jq/tool GENERATION (not parsing)** — valid `nmap`/`jq`/`ffuf` one command the operator pastes; "plausible-but-broken syntax" silently fails.
- **G12. Judgment triage (not knowledge)**: wildcard-DNS, soft-404 baseline, honeypot/canary SPN, ICS SIS-safety caveat. Judgment over data, no one evals it.

## 2. Offsec EVAL SUITE additions (grade the WORKING artifact, not yes/no)
Principle: a harness that EXECUTES the model's output (jq vs paste, payload vs lab, aws/kubectl vs throwaway/kind), grade to line/byte accuracy. ≥1 Russian probe per group.
- **A. Recon-aggregation/CIDR** (G1/G2/G11/G12): 60-80 line nmap -oG → exact counts; masscan diff + jq set; scope membership + /25 usable=126; ffuf soft-404 filter. RU: co-occurrence count + valid command.
- **B. Web byte-exact** (G3): raw CL.TE→H2.CL; blind PG SQLi in `->>`; Jinja2 SSTI with blocklist. RU: GraphQL alias-batch + clairvoyance.
- **C. Network pivot / AD lateral** (G4/G5/G6) — HIGHEST: double-pivot ligolo/chisel + proxychains4.conf; ntlmrelayx under SMB-signing → pick alt relay; exact GUIDs + PowerView DCSync + secretsdump; decode UAC 0x411000. RU: RBCD via msDS-AllowedToActOnBehalfOfOtherIdentity, exact flags/RIDs + Event IDs.
- **D. Cloud/k8s identity-pivot** (G7/G11): IMDSv2 flow; IAM CreatePolicyVersion privesc; pod SA-token → raw kube-API → BadPod. RU: EKS pod→host→AWS via IRSA. Cross-cloud IMDS header/token diffs.
- **E. Bin-exploit/RE/crypto** (G8/G10): ret2csu ROP w/ byte-exact offsets; AArch64+PAC reconstruct; CBC padding-oracle full Python; ECDSA reused-k recover d. RU: ret2dlresolve pwntools exact offsets.
- **F. Zero-coverage** (G9): Frida pinning-bypass; RF replay/WPA3; ICS S7/Modbus + SIS caveat; phishing (QUALITY-graded, in-scope). RU (Russian×crypto = thinnest): JWT RS256→HS256 key-confusion + jku/jwk/kid.
- **G. RAG-weaponization floor**: give a fresh 2026 advisory w/o explanation → working PoC.

## 3. RE-PRUNE mission-weighted importance CALIBRATION additions
Principle: the importance-pass must SEE these tokens or the experts get zero vote and are cut; heal won't resurrect. All in Phase-A importance corpus, + a **Russian slice per group**.
1. Raw tool-output ALL formats, long (20-200 hosts): nmap -oN/-oG/-oX, masscan -oJ/-oL, ProjectDiscovery JSONL, ffuf -of json (soft-404 baseline), amass -json.
2. CIDR/bitmask dataset (membership, /22 & /27 counts, host-list→minimal-CIDR, IPv6 /64, ASN→prefix).
3. input+command+expected-output triples for jq/awk/grep/sort|uniq (faithful command generation).
4. Byte-exact wire-format: PortSwigger solutions, smuggling PoC, per-DBMS SQLi (sqlmap+tampers, PayloadsAllTheThings), SSTI per-engine, deser gadget-chains (ysoserial/PHPGGC/pickle), encoding/WAF-bypass tables.
5. Post-foothold network (was ABSENT): impacket transcripts w/ real flags+stdout, chisel/ligolo/sshuttle/ssh -DLR, full proxychains4.conf + `proxychains nmap/GetUserSPNs.py`, double-pivot `ip route add ... dev ligolo`, BloodHound Cypher, Certipy ESC1-16 (esp ESC8), NetExec.
6. AD structured tables (not prose): control-access-right GUIDs, UAC-flag table, msDS-*, well-known SID/RID, SDDL/ACE, Kerberos error+Event IDs, MS-KILE/MS-PAC/MS-SFU field names.
7. Cloud multi-provider (Azure/GCP had ZERO): boto3/aws/az/gcloud cmd+JSON transcripts, Pacu/PMapper ~20-edge pairs, IMDS traces all 3 clouds, raw kube-API curl+JSON, HCL/tfstate/securityContext, BadPods, IRSA/Workload-Identity trust JSON.
8. Multi-arch disasm + dense hex: objdump/Ghidra/IDA paired w/ source (x86-64 Intel+AT&T, AArch64, ARM32-Thumb2, MIPS, RISC-V), hexdumps, ELF/PE/Mach-O headers, pwn writeups w/ worked offset arithmetic, allocator walkthroughs, pwntools/gdb/angr/AFL++ transcripts.
9. Offensive-crypto with worked solvers (backstops math regression): CryptoPals/CryptoHack, padding-oracle, ECDSA-HNP/lattice (LLL via sage/fpylll), GCM-forbidden, Bleichenbacher/ROBOT, length-extension — with real Python (pycryptodome/sage/z3).
10. Zero-coverage domains: Frida/MASTG, WPA3/PMKID/Proxmark3/SDR, binwalk/openocd/chipwhisperer, Modbus/DNP3/S7comm/OPC-UA (pymodbus/snap7, ICS-NSE, SIS notes), gophish/evilginx2-phishlets/maldoc/pretext.
11. Named-product/protocol vocab smoke-set (okhttp, hostapd-wpe, snap7, evilginx2, chipwhisperer, Proxmark3, ligolo-ng, Certipy…) in real usage — anchors low-freq tokens RAG-weaponization needs.
12. **Russian slice of EACH of 1-11** (RU pentest writeups: Xakep, PT SWARM, Codeby, RU CTF-crypto) + bilingual offsec glossary paired with canonical English tool-output (narrate RU, keep exact EN identifiers).

## 4. NON-prune issues (prune won't fix — need RAG / tools / right base-template)
- **N1. Recency/fresh CVE** = out of prune scope → RAG. BUT RAG-weaponization has a vocab floor (§3.10-11): retrieval can't operationalize if resident domain tokens were cut. §3.10-11 is a *precondition* for RAG. Test via §2-G.
- **N2. Tool-call format** depends on base/chat-template, not prune. Pin the correct Qwen3.5 tool schema; harness must EXECUTE emitted commands to catch format-drift separately from capability-drift.
- **N3. NVFP4 quant × byte-exact (G3/G6/G10)** — 4-bit noise hits low-margin symbolic precision (offsets, GUIDs, hex). Run byte-exact probes POST-QUANT, not just post-prune, or we blame prune for quant damage.
- **N4. Abliteration × precision = hardest-to-detect failure**: over-aligned residue → "confidently-wrong safe" artifact (subtly-broken offsets, non-functional exploit) instead of refusal — WORSE than refusal. Need a functional-fidelity axis: "payload actually fires vs lookalike" (esp phishing QUALITY, bin-exploit execute-vs-lab).
- **N5. ICS SIS/safety** — not model safety, world safety. Operator needs a correct caveat WITHIN a working answer; grade caveat presence without refusing the PoC.
