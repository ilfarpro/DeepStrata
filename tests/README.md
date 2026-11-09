# Tests

| Folder | What it covers | Needs |
| --- | --- | --- |
| `tests/terminal` | The Terminal, Control, launching and update checks | CPU only unless a test says otherwise |
| `tests/serve` | The Python server and its OpenAI/Anthropic-compatible API | CPU only; a model or an engine is replaced by a mock |
| `tests/tools` | Setup, packaging and update helper scripts | CPU only; downloads and hardware are mocked |
| `tests/*` | Engine, backend and benchmark checks | A built engine, a model or a matching GPU; most skip elsewhere |

Run the CPU tests from the repository root with the checkout's Python:

```sh
python -m pytest tests/terminal
python -m pytest tests/serve tests/tools
```

The root `conftest.py` gives every run its own temporary config, state and data folders, so a test never touches
the person's real settings. `tests/terminal/conftest.py` also turns off GPU probing for that folder.

The tests that used to sit beside the code in `serve/` and `tools/` now live here. Test-only data stays with the
code it tests: `serve/fixtures/` and `serve/chat_template.jinja` are runtime fallbacks as well, and the setup
golden record is in `tests/tools/test_setup_golden.json`.
