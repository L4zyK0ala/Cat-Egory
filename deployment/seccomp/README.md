# Crawler seccomp profile

`crawler.json` is Docker's default seccomp profile, taken from
[moby/profiles](https://github.com/moby/profiles/blob/main/seccomp/default.json) (Apache License 2.0,
Copyright Docker, Inc.) on 2026-11-07, with one rule appended (the last entry):

```json
{"names": ["clone", "unshare", "setns", "chroot"], "action": "SCMP_ACT_ALLOW"}
```

It lets Chromium create the user/PID/network namespaces and the empty chroot its sandbox needs, without giving
the container `CAP_SYS_ADMIN` or `CAP_SYS_CHROOT`. To refresh from upstream, re-download `default.json` and
append the same rule.
