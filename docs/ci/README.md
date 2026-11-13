# CI files waiting to be installed

The token this branch is pushed with has no `workflow` scope, so GitHub
refuses any push that touches `.github/workflows/`. These files are the
P0.3 CI changes; move them into place from a machine with a token that
has the scope:

```sh
git mv docs/ci/tests.yml .github/workflows/tests.yml
git apply docs/ci/wlan-scan-test-job.patch     # release workflow: test job only
git rm -r docs/ci && git commit -m "P0.3: install CI workflows"
```

- `tests.yml`: runs the tests on every branch push and pull request
  (Linux, `QT_QPA_PLATFORM=offscreen`, installs Qt's system libraries).
- `wlan-scan-test-job.patch`: the release workflow's `test` job installs
  `requirements-dev.txt` and Qt's libraries so the GUI tests run there too.
  The build and release jobs are unchanged.

Without the patch the release workflow still passes: GUI tests skip
themselves when pytest-qt or Qt's libraries are missing (`need_qt()` in
`tests/conftest.py`).
