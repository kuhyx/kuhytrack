## Commands

- run: `python3 server/kt_server.py`
- test: `python3 -m unittest discover -s tests -q`
- test-changed: `scripts/test_changed.sh`
- lint: `ruff check --select E9,F . && shellcheck -S error android/*.sh scripts/*.sh`
- coverage: n/a: stdlib unittest, no coverage tooling configured
- coverage-gaps: n/a: no coverage report (see coverage)
