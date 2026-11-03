---
name: registration-proxy
description: Recall when routing Token Harbor registration through an HTTP or SOCKS5 proxy.
metadata:
  type: project
  scope: TH2API registration runs that need a non-default egress IP
  status: active
  last_verified: 2026-11-03
---

`register_account(proxy=...)` and `th2api-register --proxy` apply one proxy to
every request in the pipeline: DuckMail, the sign-up action, the verification
link, the free-model consent, and key creation.

Use `socks5h://` rather than `socks5://` for SOCKS5 proxies. This machine's DNS
returns fake-IP addresses (`198.18.0.0/15`), so local resolution hands the proxy
an unroutable address and it closes the connection (`curl: (52) Empty reply from
server`); `socks5h://` resolves at the proxy and works. HTTP proxies use
`http://`.

**Evidence:** 2026-11-03 — `curl --socks5 … ipinfo.io` failed with an empty
reply while `--socks5-hostname` returned a ZA IP; the same `socks5h://` URL
registered `thighy20sbdu@niceground.shop` end-to-end through
`th2api-register --proxy`, and the resulting key passed `/v1/models`.

**Why:** a SOCKS5 proxy that "does not work" here is usually a DNS-location
mistake, not a dead proxy.

**How to apply:** pass `socks5h://user:pass@host:port`; for rejected-IP errors
retry with a different proxy, since there is no pool rotation
([[tokenharbor-ip-reputation]]).

**Recheck when:** the machine stops using fake-IP DNS or the proxy library
changes scheme handling.
