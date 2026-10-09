# OFFSEC COVERAGE MAP — эталонная карта покрытия для калибровочного корпуса re-prune

> Источник: владелец, 2026-07-03. Это ДЕФИНИТИВНЫЙ scope домена offsec для re-prune.
> Каждый под-навык → ≥1-несколько РАЗНЫХ полных Q&A (вход+ответ) в `calib_corpus.py`, чтобы роутер
> зажёг экспертов этого под-навыка → они выживут при прунинге (KEEP_FRAC~0.29).
> Принцип: покрыть ВСЮ ШИРИНУ (много разных под-тем важнее глубины-на-тему). Приоритет №1 на весах.
> Crypto/Web3 = категория 3 (складывается в offsec, не отдельный домен).
> Контекст: авторизованное тестирование собственной инфраструктуры компании. См. PRUNE-RECIPE.md, CALIB-COVERAGE-RESEARCH.md.

## 1. WEB, API & APPLICATION SECURITY
- **Client-side:** XSS (Stored/Reflected/DOM/Blind/mXSS); CSRF, XS-Leaks, COOP/COEP bypass; Prototype Pollution (client), DOM Clobbering, client-logic bypass; CSS Injection, Clickjacking, File Upload → RCE (веб-шеллы).
- **Server-side:** SQLi (Error/Boolean/Time/Union), NoSQLi, Command Injection, LDAPi, XPath, SSTI/CSTI; LFI/RFI, XXE (Billion Laughs, OOB), SSRF (Blind, URL-parsing bypass); Insecure Deserialization (Java/PHP/Python/.NET), Object Injection; Race Conditions, IDOR/BOLA, price/param manipulation, OTP/2FA bypass.
- **API & modern:** GraphQL (Injection, Introspection, Batching DDoS, Alias Overloading); SOAP/XML, REST routing bypass, Mass Assignment; HTTP Request Smuggling (CL.TE/TE.CL/TE.TE), Web Cache Poisoning/Deception, Host Header Injection.

## 2. BINARY, REVERSING & LOW-LEVEL
- **Memory corruption:** Stack BOF, SEH overwrite, ret2libc, ROP/SROP/JOP/COP; Heap Overflow, UAF, Double Free, Type Confusion, Heap Feng Shui; Format String (arb read/write); Integer Overflow/Underflow, Sign Extension, off-by-one.
- **Kernel:** null-ptr deref, Write-What-Where, DKOM; обход SMEP/SMAP/KASLR/DSE (BYOVD).
- **Hardware/CPU:** Spectre, Meltdown, Rowhammer, Fault Injection (glitching).

## 3. CRYPTOGRAPHY & WEB3 (BLOCKCHAIN)
- **Крипто-флоу:** Padding Oracle (AES-CBC), Bleichenbacher (RSA); weak RNG (Dual_EC_DRBG, дубль k в ECDSA), related-key; Hash Length Extension (MD5/SHA1), JWT Confusion (RS256↔HS256), RSA-математика (Wiener, Coppersmith).
- **Web3/смарт-контракты/DeFi:** Reentrancy, Integer Overflow (старый Solidity), Unprotected Selfdestruct; Flash Loan атаки (оракулы цен), Front-running/MEV, Sandwich; Compromised Private Keys, Phishing Approval (ERC-20/721).

## 4. NETWORK, INFRASTRUCTURE & WIRELESS
- **L2:** ARP Spoofing, MAC Flooding, DHCP Starvation, Rogue DHCP; VLAN Hopping (Switch Spoofing, Double Tagging).
- **L3/L4:** BGP Hijacking, OSPF/RIP spoofing, IP Spoofing; DNS Cache Poisoning (Kaminsky), DNS Tunneling, DNS Rebinding; DDoS (SYN Flood, UDP/NTP/DNS Amplification, Slowloris, QUIC/HTTP2 L7); MITM (SSL/TLS Stripping, Downgrade — Logjam/FREAK, Rogue SNI).
- **Wireless/radio:** WPA2 handshake/PMKID, KRACK, WPA3 downgrade, Evil Twin; Bluetooth (BlueBorne, BLE Jacking), RFID/NFC cloning, replay (SDR/Flipper), GPS Spoofing, IMSI Catchers.

## 5. ENTERPRISE WINDOWS & ACTIVE DIRECTORY
- **Identity/Auth:** Kerberos (Kerberoasting, AS-REP Roasting, Golden/Silver Ticket, PtT, Delegation — Unconstrained/Constrained/RBCD); NTLM (PtH, NetNTLM Relaying, Forced Auth — PetitPotam/ShadowCoerce/PrinterBug).
- **AD infra:** DCSync, DCShadow, GPO Abuse, AD CS (ESC1–ESC13); ACL/ACE Abuse (WriteDacl, GenericAll), GPP cpassword в SYSVOL.
- **LPE:** Token Impersonation, Potato (Juicy/Rogue/PrintPotato, SeImpersonate); Unquoted Service Paths, AlwaysInstallElevated, DLL Hijacking; UAC bypass (Mock Folders, COM InprocServer).

## 6. LINUX, UNIX & CONTAINERIZATION
- **LPE:** sudo token reuse, GTFOBins, SUID/SGID; Dirty COW (CVE-2016-5195), PwnKit (CVE-2021-4034), shm sniffing, D-Bus.
- **Containers/K8s:** docker.sock, cgroups release_agent, SYS_ADMIN syscall abuse, runc/containerd CVE; Kubelet anon API, SSRF→cloud metadata из подов, RBAC escalation, Pod creation abuse (монтирование хостовой ФС).

## 7. CLOUD & DEVOPS
- **Cloud (AWS/Azure/GCP):** IMDSv1 SSRF, IMDSv2 bypass; IAM misconfig (AssumeRoleLoop, PassRole, policy injection); public S3/Azure Blobs.
- **DevOps/supply-chain:** Poisoned Pipeline Execution (.github/workflows, gitlab-ci.yml), runner-манипуляция; Dependency Confusion, Typosquatting, malicious upstream; secrets в git-коммитах.

## 8. MOBILE & IOT/EMBEDDED
- **Mobile:** Android (insecure storage, IPC/Intent abuse, Frida/Xposed); iOS (jailbreak detect bypass, Keychain extraction, IPA RE, Mach ports).
- **IoT/hardware/firmware:** дамп через SPI/I2C, хардкод-ключи/бэкдоры; UART/JTAG консоль; Modbus/Zigbee/Z-Wave/CAN bus.

## 9. RED TEAMING, PERSISTENCE & EVASION
- **Evasion:** EDR/AV bypass (API Unhooking, Direct/Indirect Syscalls, patch AMSI/ETW); Process Injection (DLL Injection, Process Hollowing/Ghosting/Herpaderping, Reflective DLL); LOLBAS/GTFOBins.
- **C2/exfil:** Domain Fronting, DNS Tunneling, легит-трафик (Telegram/Slack/Discord/Drive/Notion); Persistence (WMI Event Consumers, Scheduled Tasks, COM Hijacking, SSP injection, PAM/Cron backdoors).

## 10. AI / ML ADVERSARIAL
- Prompt Injection (Direct/Indirect); Data Poisoning (бэкдоры в датасете); Model Inversion/Extraction; Adversarial Examples (evasion).

## 11. SOCIAL ENGINEERING & PHYSICAL
- Digital: Spear-phishing, Smishing, Vishing (ИИ-дипфейки). Physical: Tailgating, Badge Cloning (RFID/NFC), USB Drop (BadUSB), Lock Picking.

## 12. PHASES, RECON & FORENSICS (OSINT/DFIR)
- Recon/OSINT: Active/Passive DNS enum, Shodan/Censys/Zoomeye, leaked creds, Google Dorking, ASN mapping.
- DFIR/anti-forensics: Timestomping, удаление логов (Event Logs, auth.log), ADS (NTFS alternate streams).

---

## Как строить корпус по этой карте
- На КАЖДЫЙ под-навык (жирные пункты) — минимум несколько РАЗНЫХ полных Q&A (вопрос + технический ответ с кодом/командами). Ширина > глубины: 12 категорий × ~все под-темы, каждая зажигает своих экспертов.
- offsec доля 0.26 (приоритет); crypto (кат.3) внутри offsec. Остальные домены: code 0.16, russian 0.12, math 0.11, precision 0.11, general 0.10 (общий баланс — CALIB-COVERAGE-RESEARCH.md).
- Дедуп примеров (k-means/эмбеддинги), полные вход+OUTPUT (EASY-EP: In+Out >> prompt-only).
