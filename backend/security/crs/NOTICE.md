# Data from the OWASP Core Rule Set

The `*.data` files in this folder are copied unchanged from the OWASP Core Rule Set
(https://github.com/coreruleset/coreruleset), which is licensed under the Apache License 2.0
(see `LICENSE` here). They are used by `security/engine.py`, which re-implements, in Python and as
log-only checks, the ideas of these CRS rule families:

| Group | CRS file |
|---|---|
| 911 | REQUEST-911-METHOD-ENFORCEMENT |
| 913 | REQUEST-913-SCANNER-DETECTION (scanners-user-agents.data) |
| 920 | REQUEST-920-PROTOCOL-ENFORCEMENT (restricted extensions / headers from crs-setup.conf.example) |
| 921 | REQUEST-921-PROTOCOL-ATTACK |
| 930 | REQUEST-930-APPLICATION-ATTACK-LFI (lfi-os-files.data, restricted-files.data) |
| 931 | REQUEST-931-APPLICATION-ATTACK-RFI |
| 932 | REQUEST-932-APPLICATION-ATTACK-RCE (unix-shell.data, unix-shell-builtins.data) |
| 933 | REQUEST-933-APPLICATION-ATTACK-PHP (php-function-names-933150.data, php-variables.data) |
| 934 | REQUEST-934-APPLICATION-ATTACK-GENERIC (ssrf.data) |
| 941 | REQUEST-941-APPLICATION-ATTACK-XSS |
| 942 | REQUEST-942-APPLICATION-ATTACK-SQLI |
| 943 | REQUEST-943-APPLICATION-ATTACK-SESSION-FIXATION |
| 944 | REQUEST-944-APPLICATION-ATTACK-JAVA |

This is not ModSecurity and not the full rule set: the CRS relies on libinjection and hundreds of
generated expressions, and this is a small, readable subset. To update the lists, copy the newer
files over these.
