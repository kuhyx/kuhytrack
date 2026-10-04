#!/bin/bash

# ============================================================================
# Run only the tests related to files changed vs HEAD (staged, unstaged and
# untracked). Quiet: failures plus a one-line summary. Tests are stdlib
# unittest files (tests/test_*.py); a changed test runs itself, any other
# changed Python file maps to tests/test_<name>.py when that exists, else the
# full suite runs (there are only two files). Non-Python changes test nothing.
# ============================================================================

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

CHANGED=()
while IFS= read -r f; do
    [[ -n "$f" && -e "$f" ]] && CHANGED+=("$f")
done < <({ git diff --name-only HEAD 2>/dev/null || true; git ls-files --others --exclude-standard; } | sort -u)

run_tests() {
    local rc=0 out
    for t in "$@"; do
        out="$(python3 "$t" 2>&1)" || rc=1
        [[ $rc -eq 0 ]] || grep -v 'ResourceWarning\|^<sys>' <<<"$out" | tail -30
    done
    echo "ran $# test file(s): $([[ $rc -eq 0 ]] && echo pass || echo FAIL)"
    return "$rc"
}

main() {
    local f base tests=() py_changed=0
    for f in "${CHANGED[@]}"; do
        case "$f" in
            tests/test_*.py) tests+=("$f") ;;
            *.py)
                py_changed=1
                base="$(basename "$f" .py)"
                [[ -f "tests/test_${base}.py" ]] && tests+=("tests/test_${base}.py")
                ;;
        esac
    done
    if [[ ${#CHANGED[@]} -eq 0 ]]; then echo "no changes vs HEAD: nothing to test"; return 0; fi
    if [[ ${#tests[@]} -eq 0 && $py_changed -eq 0 ]]; then echo "no python changes: nothing to test"; return 0; fi
    if [[ ${#tests[@]} -eq 0 ]]; then
        echo "no mapped tests: running full suite"
        mapfile -t tests < <(git ls-files 'tests/test_*.py')
    else
        mapfile -t tests < <(printf '%s\n' "${tests[@]}" | sort -u)
    fi
    run_tests "${tests[@]}"
}

main "$@"; exit $?
