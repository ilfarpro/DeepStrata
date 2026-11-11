# Tests

| Folder | What it covers | Needs |
| --- | --- | --- |
| `tests/terminal` | The Terminal, Control, launching and update checks | CPU only unless a test says otherwise |
| `tests/serve` | The Python server and its OpenAI/Anthropic-compatible API | CPU only; a model or an engine is replaced by a mock |
| `tests/tools` | Setup, packaging and update helper scripts | CPU only; downloads and hardware are mocked |
| `tests/tuning` | Measuring and choosing settings, and the stub engine it measures | CPU only |
| `tests/split` | The two-card split engine's config conversion and keys | CPU only |
| `tests/time_skip` | The time-skipping (past-token window) arithmetic and its files | CPU only |
| `tests/core`, `tests/cuda`, `tests/hip`, `tests/sycl`, `tests/dsv41`, `tests/prefill`, `tests/program` | Engine, kernel and backend checks (C++, built by `CMakeLists.txt`) | A built engine, a model or a matching GPU; most skip elsewhere |

Run the CPU tests from the repository root with the checkout's Python:

```sh
python -m pytest tests/terminal
python -m pytest tests/serve tests/tools
python -m pytest tests/tuning tests/split tests/time_skip
```

The root `conftest.py` gives every run its own temporary config, state and data folders, so a test never touches
the person's real settings. `tests/terminal/conftest.py` also turns off GPU probing for that folder.

The tests that used to sit beside the code in `serve/` and `tools/` now live here. Test-only data stays with the
code it tests: `serve/fixtures/` and `serve/chat_template.jinja` are runtime fallbacks as well, and the setup
golden record is in `tests/tools/test_setup_golden.json`.

## What runs on every pull request

`.github/workflows/python-tests.yml` runs the lint, the collection check and one job per suite folder above.
The suites wait more than they compute, so they run under `pytest-xdist` (`-n 4`): `tests/tools` takes about a
minute and a half instead of twelve. A red check names the folder.

- The lint is ruff, with the rule set in `pyproject.toml`: the rules that find a program that cannot run
  (undefined and redefined names, syntax that only fails at runtime), not a house style. Run it locally with
  `python -m pip install ruff==0.17.4 && python -m ruff check .`
- The collection check (`python -m pytest --collect-only -q tests`) fails on a test module that no longer
  imports, which is how a whole file's worth of tests disappears without a red suite.
- The monitor tests draw a Textual app and need a terminal that reports colour: run them with
  `TERM=xterm-256color` and without `NO_COLOR`, or three light-theme tests read the dark palette and fail.

`apps/host` is separate (`.github/workflows/app-host-tests.yml`): `npm run typecheck` and `npm test` in that
folder, Node 24.

The full command the release gate runs is still `python -m pytest -q tests/terminal tests/serve tests/tools`
(`packaging/release.py`); the workflow above is the same three suites, split so a failure points at one.
