#!/bin/bash
# fail if anything looks like a secret (never prints matched values)
cd "$(dirname "$0")"; bad=0
pat='hf_[A-Za-z0-9]{20,}|gh[opsu]_[A-Za-z0-9]{20,}|github_pat_|sk-[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY|AKIA[0-9A-Z]{16}|xox[bp]-|seed phrase|mnemonic|private_key|secret_key|api_key\s*='
if rg -l -i -e "$pat" --hidden -g '!.git' -g '!scan.sh' . ; then echo "PATTERN HIT (files above)"; bad=1; fi
for f in ~/.secrets/*; do t=$(tr -d '\n\r ' < "$f"); [ -n "$t" ] && rg -l -F -e "$t" --hidden -g '!.git' . && { echo "SECRET VALUE HIT"; bad=1; }; done
[ $bad = 0 ] && echo "scan clean"; exit $bad
