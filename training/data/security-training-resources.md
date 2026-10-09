# Security / vuln-finding data sources

Curated 2026-09-05 for a future round (v6+), not the v5 corpus. All links are public
pages/repos reachable without login (verified via fetch without a session) except where
marked. Collection runs on the box (open WAN), structuring reuses the harvest LLM +
`../harness/export_sft.py`. Keep everything local; check each source's ToS/license before
baking into the set.

## HackenProof — primary source (public pages, no login)

Public program pages share the dashboard slugs: `hackenproof.com/programs/<slug>` ==
`dashboard.hackenproof.com/user/programs/<slug>`, without auth.

- Program listing (323 programs, `?page=N`): https://hackenproof.com/programs
- Vuln taxonomy, smart contracts: https://docs.hackenproof.com/bug-bounty/vulnerability-classification/smart-contracts
- Vuln taxonomy, web/mobile: https://docs.hackenproof.com/bug-bounty/vulnerability-classification/web-and-mobile
- Report template: https://docs.hackenproof.com/bug-bounty/reports-basics
- Disclosure policy: https://docs.hackenproof.com/good-to-know/vulnerability-disclosure
- Public org with disclosed reports: https://github.com/hackenproof-public (e.g. https://github.com/hackenproof-public/suidex-reports)

Reference program pages by category (format examples):
- Blockchain/protocol: https://hackenproof.com/programs/starknet-blockchain-slash-dlt
- Smart contract + bridge: https://hackenproof.com/programs/starknet-web-and-sc
- Web/mobile/API (CEX): https://hackenproof.com/programs/pionex-app-and-api
- Web/API: https://hackenproof.com/programs/cetus-web

## Smart-contract audits + disclosed findings (aggregators)

- Solodit — largest search base of public audit findings (all major firms + contests): https://solodit.cyfrin.io/
- Code4rena contest reports: https://github.com/code-423n4 (collection: https://github.com/zhongeric/code4rena-reports)
- Sherlock judging repos (issue = one finding with severity): https://github.com/sherlock-audit
- Cantina finished competitions: https://github.com/cantina-competitions
- DeFiHackLabs — real DeFi exploit reproductions on Foundry, with PoC: https://github.com/SunWeb3Sec/DeFiHackLabs
- DeFiVulnLabs — typical smart-contract vuln teaching examples: https://github.com/SunWeb3Sec/DeFiVulnLabs
- SWC Registry — vuln-type classification + test cases: https://github.com/SmartContractSecurity/SWC-registry
- Immunefi public writeups list: https://github.com/sayan011/Immunefi-bug-bounty-writeups-list
- rekt.news — real large incidents/exploits: https://rekt.news/leaderboard
- Meta awesome-lists: https://github.com/acorn421/awesome-smart-contract-datasets, https://github.com/saeidshirazi/Awesome-Smart-Contract-Security

## Web / API / mobile (non-crypto)

- HackerOne Hacktivity — largest public disclosed-report feed, all industries: https://hackerone.com/hacktivity
- PortSwigger Web Security Academy — labs + per-class breakdowns: https://portswigger.net/web-security
- PayloadsAllTheThings — techniques/payloads per attack class: https://github.com/swisskyrepo/PayloadsAllTheThings
- OWASP Web Security Testing Guide: https://owasp.org/www-project-web-security-testing-guide/
- OWASP Mobile Application Security (MASTG/MASVS): https://mas.owasp.org/
- Google Bug Hunters — public report breakdowns: https://bughunters.google.com/
- CVE/NVD — real-vuln feed with description + CVSS: https://nvd.nist.gov/vuln/data-feeds

## Dataset-design notes

Out-of-scope lists on program pages are near-template and repeat across programs — a ready
checklist of "predictably-rejected noise that is not a bug". Train on it as negative
examples: the model must tell a real finding from a predictably-declined one, not just
recognize vulns.

Every HackenProof page repeats: "AI-generated reports without runnable PoC are not
accepted". The pipeline's target artifact is a working PoC, not a text description — grade
on runnable PoC, mirroring the harvest self-test pattern.

## Legal

Everything stays local (project rule), model is never published — so redistribution risk is
small. Owner decision: private-dashboard content, including not-yet-disclosed findings, goes
into the set too; tag those records `source: private_dashboard` in meta so they stay
separable. Residual risk on the owner: copying undisclosed data may breach a program's
disclosure terms, and a local model can still emit a memorized finding to whoever queries
it. Automated scraping may violate HackenProof ToS even for public pages — the account is
the one that feeds the GUR CTF funnel.
