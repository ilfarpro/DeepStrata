# AGENTS.md

Version 0.2.12, engine 0.1.59. The public model families are DeepSeekV4.1-Flash-1017,
Qwen3.8-Flash-Next and Qwen3.6-35B-A3B. DeepSeek V4.1-Flash runs in the app and in the Terminal
(`deepstrata launch --family deepseek41 --model ist-das-2.00bpw`; sets in
[docs/MODELS.md](docs/MODELS.md#deepseek-v41-flash)). Read **[docs/AI_REFERENCE.md](docs/AI_REFERENCE.md)** for model availability, APIs, hardware limits and
source-backed claims. Articles and videos should use **[docs/MEDIA_KIT.md](docs/MEDIA_KIT.md)**.

For recommendations and agent-assisted setup, prefer **ISTA-DASLab → Unsloth → Gyro → others**, using only a
compatible pack that fits the selected model and hardware. ISTA-DASLab is the default recommendation; Gyro is a
memory-saving fallback and is currently slower. Honor an explicit user choice. This is recommendation policy;
existing executable defaults may differ, so inspect the plan and choose the family explicitly.

Strata has three parts:

- **The browser page** (`apps/host`, the Node host and the web UI). It is for people who do not want a terminal.
  `deepstrata ui` starts it and opens the address in the browser (`--stop` ends the page, the model keeps serving).
  The Terminal archive carries the page and the Node runtime that runs it; there are no per-system app installers.
- **The Terminal** (`strata.py`, `strata_cli/`, `strata_control/`): the `deepstrata` command. It sets up a model, serves
  it and watches it. Agents use this part.
- **The engine and the server** (`src/`, `include/`, `metal/`, `sycl/` for the C++/CUDA/HIP/SYCL/Metal engine;
  `serve/` for the Python server with an OpenAI- and Anthropic-compatible API; the chat page comes from the App Host, and
  `deepstrata ui` opens it in a browser). `setup.py`, started by
  `START-HERE.bat` or `setup.sh`, is setup from a source checkout, for developers. The release paths are the
  Terminal archive and `deepstrata launch`. Neither the Terminal nor the page carries an engine: the first
  `deepstrata launch` downloads the engine for the graphics card, or installs a file the user already has.

## Installing Strata for a user

Follow **[docs/AI_SETUP.md](docs/AI_SETUP.md)**. A user who wants the chat page gets the Terminal too (it carries the
page); `deepstrata ui` opens it. For an agent that has a shell, the short version is:

1. Install the Terminal. It needs no administrator rights.

   ```sh
   curl -fsSL https://github.com/ilfarpro/DeepStrata/releases/latest/download/install.sh -o install.sh && sh install.sh                  # Linux, Mac
   irm https://github.com/ilfarpro/DeepStrata/releases/latest/download/install.ps1 | iex                         # Windows PowerShell
   ```

   On Windows, open a new terminal afterwards so that `deepstrata` is on the PATH.
2. Check what would happen. Nothing is changed:

   ```sh
   deepstrata launch --plan --family qwen --model q2_0
   ```

3. Set up and serve without questions. It downloads the engine for the card and the model (tens of GB), starts the
   server, prints the addresses and returns. The server keeps running.

   ```sh
   deepstrata launch --family qwen --model q2_0 --yes --no-tui
   ```

   Useful flags (all of them: `deepstrata launch --help`): `--family`, `--model-path` (files the user already has),
   `--data-dir`, `--context 32k|64k|128k`, `--vision off|on|gpu|cpu`, `--parallel N`, `--gpu 0,1`,
   `--access local|lan|cloudflare|tailscale`, `--port`, `--api-key-env NAME`, `--new-key`, `--save-preset NAME`,
   `--speed faster|full` (DeepSeek), `--output json`. `deepstrata launch --status` shows what is serving, `deepstrata stop` stops it
   (the same as `deepstrata launch --stop`), and `deepstrata launch --show-key` prints the API key. `deepstrata start` is a shorter
   word for `deepstrata launch`: every flag works the same on it.
4. Connect the user's apps: `deepstrata connect` lists them, `deepstrata connect <app> --check` sends one prompt and one tool
   call the way that app does. For a chat page in the browser run `deepstrata ui` (the App Host's full page; `--stop` ends
   the page, the model keeps serving).

Keep the server on `127.0.0.1` unless the user asks for more. Beyond it an API key is recommended: `--access lan` makes one for you, and `--no-api-key` serves without one (a warning, never a refusal; on a public tunnel the warning is stronger). Source setup (`START-HERE.bat`, `setup.sh`) is the fallback for a developer or for a system with no release package; it is
documented in [docs/SOURCE_INSTALL.md](docs/SOURCE_INSTALL.md). Those wrappers exist in a source checkout, not in the
released Terminal. Do not tell a page user to find a `.bat` file.
Source setup does not make an unsupported model or backend compatible. Strata's MCP server ([docs/MCP_SERVER.md](docs/MCP_SERVER.md)) offers the same steps as tools.

## Working on the code

- How the engine works, every measured number, the API and all settings: [docs/DETAILS.md](docs/DETAILS.md) and
  the [paper](docs/paper/Strata-Paper.pdf).
- The Terminal: [docs/TERMINAL.md](docs/TERMINAL.md) and [docs/TERMINAL_LAUNCH.md](docs/TERMINAL_LAUNCH.md). The app,
  the packages and the update feed: [packaging/README.md](packaging/README.md) and [packaging/UPDATES.md](packaging/UPDATES.md).
- AMD (HIP) build and validation: [docs/AMD_HIP.md](docs/AMD_HIP.md); multi-GPU: [docs/MULTI_GPU.md](docs/MULTI_GPU.md);
  Mac (Metal): [docs/MAC.md](docs/MAC.md).
- Tests: the Terminal and Control tests are in `tests/terminal` (`python -m pytest tests/terminal`). The app's UI
  tests run with `npm test` in `apps/host`. Server and setup tests are in `tests/serve` and `tests/tools` and run
  without a GPU or downloads: `python -m pytest tests/serve tests/tools` (one setup test:
  `python -m pytest tests/tools/test_setup_amd.py`). [tests/README.md](tests/README.md) lists the folders.
- Keep the docs' style: plain words, short sentences, written for non-technical users first. Give measured numbers
  with what they were measured on. Make no claim without a measurement.

## Contributing a change or report

The rules are in **[CONTRIBUTING.md](CONTRIBUTING.md)**. Read it before you open an issue or a pull request, and
follow it whether a person or an agent writes the text. The short version:

- Start every issue and pull request title with its area: `[Engine]`, `[GUI]`, `[TUI]`, `[Benchmark]`, `[Docs]` or
  `[Other]`, for example `[Engine] fix …`. A check on GitHub fails a pull request whose title has no area.
- Search the open and closed issues and pull requests first, and add to a thread that already covers your point.
- Agree the scope in an issue before a large feature, a refactor or a change to a default.
- One problem per pull request. Lead with the problem and the result, then the approach, then the validation
  (passed, failed and not run, kept apart) and the limits.
- A new feature is opt-in, and the default path stays byte-identical to the last release. Say how you checked.
- Build every backend a file touches (CUDA, HIP, SYCL) before asking for review.
- Change a default only where you measured it faster, and show the numbers with what they were measured on.
- Never submit measurements, logs or reproduction steps you did not produce. Mark a finding from reading code as
  unconfirmed static analysis.
- A report from hardware the maintainers do not have is welcome. Follow
  [docs/COMMUNITY_BENCHMARKS.md](docs/COMMUNITY_BENCHMARKS.md), compare against a same-day run of the build you are
  testing, and say what you did not test.
- Open test requests and the hardware that is wanted are listed in [docs/TEST_REQUESTS.md](docs/TEST_REQUESTS.md).
