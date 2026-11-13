# Install Strata

This page is for anyone installing or updating Strata (version 0.2.13). Use the **chat page in your browser** for a
graphical interface, or the **Terminal** for `deepstrata launch` and coding tools. Both come from one download: the
Terminal archive carries the page too. A normal install does not need a source checkout or a startup `.bat` file.

## Quick start

| I want | Do this |
| --- | --- |
| A chat page in the browser | Install the Terminal, run `deepstrata launch`, then `deepstrata ui` ([steps](#the-chat-page)) |
| The terminal, or my coding tool | Run the one-line install, then `deepstrata launch` ([steps](#the-terminal)) |
| DeepSeek V4.1-Flash | Pick it in the page, or run `deepstrata launch --family deepseek41` ([model page](MODELS.md#deepseek-v41-flash)) |

[Requirements](#what-you-need) · [The chat page](#the-chat-page) · [Terminal](#the-terminal) ·
[Update](#updating) · [Files and folders](#where-things-are-stored) · [Build from source](SOURCE_INSTALL.md)

## What you need

| Computer | Guide |
| --- | --- |
| Windows 10/11 or Linux x86-64 with an NVIDIA RTX GPU | Use the page or the Terminal. The engine for your card is downloaded for you. [Older NVIDIA cards](OLDER_GPUS.md) use the CUDA 12 engine. DeepSeek V4.1-Flash runs on RTX 30, 40 and 50 cards in the page and the Terminal. |
| Windows or Linux x86-64 with a supported AMD Radeon | [AMD guide](AMD_HIP.md), including the card-to-package table. DeepSeek V4.1-Flash runs on the Radeon AI PRO R9700 under Linux, in the page and the Terminal. |
| Linux x86-64 with Intel Arc | [Intel Arc guide](INTEL_ARC.md); install the driver/compute runtime and check model preparation requirements |
| Apple Silicon Mac, macOS 15 or newer | [Mac guide](MAC.md). Intel Macs are not supported by the Mac package. |
| AMD Ryzen AI Max / Strix Halo | [Strix Halo guide](STRIX_HALO.md) |
| NVIDIA DGX Spark (Linux ARM64) | [DGX Spark guide](DGX_SPARK_LINUX.md). Use the Terminal; the page runs from it the same way. |

Memory and disk needs depend on the **model, quantization, context and enabled features**. Check
[which model fits](MODELS.md#pick-by-memory); download size is not the same as RAM use. Unified memory counts once.
Use an SSD and leave room for helper files and model preparation. The Terminal's `--plan` prints what is missing.

Install a graphics driver that supports the package you choose. CUDA 13 packages require NVIDIA driver 580 or newer;
older NVIDIA cards use CUDA 12. AMD and Intel requirements are in their guides. Packaged GPU runtimes do not replace
hardware drivers. Models and updates need an internet connection to download.

## The chat page

The page ships inside the Terminal archive: same install, one command more.

1. Install the Terminal once ([steps below](#the-terminal)). It carries the page and the runtime that serves it;
   it needs no administrator rights.
2. Start a model: `deepstrata launch` shows what each model needs, downloads the engine for your graphics card
   with its size before it fetches anything, and serves.
3. Run `deepstrata ui`. It starts the App Host and opens the full page — Chat, Monitor, Settings — in your browser.
   Closing the tab keeps the model serving; `deepstrata ui --stop` ends the page.
4. Choose a model and its storage folder, let it download, then chat. DeepSeek V4.1-Flash is in the model list on
   the supported cards ([model page](MODELS.md#deepseek-v41-flash)); it has three sizes, an 8K context and a **Speed**
   choice, Faster or Full quality.

The `strata-engine-…` and `strata-control-…` archives on the release page are components, not installers. Intel Arc
runs on Linux only, through the [Terminal](INTEL_ARC.md).

The first launch can take time to check and prepare large model files. Watch its progress and logs; there is no fixed
startup time for every model or computer. `deepstrata stop` stops a served model.

The provider order recommended here is **ISTA-DASLab, Unsloth, Gyro, then others**, when a compatible pack fits.
Current recommendations can differ; choose the model explicitly when you want a particular provider.

## The Terminal

Install once, then use `deepstrata launch`. The Terminal includes Python and downloads the engine for your computer.

**Linux / Mac**

```sh
curl -fsSL --connect-timeout 15 --max-time 120 https://github.com/ilfarpro/DeepStrata/releases/latest/download/install.sh -o install.sh && sh install.sh
```

**Windows PowerShell**

```powershell
irm -TimeoutSec 120 https://github.com/ilfarpro/DeepStrata/releases/latest/download/install.ps1 | iex
```

On Windows, open a **new terminal** afterwards. On Linux and Mac, if `deepstrata` is not found, use the full path printed
by the installer (normally `~/.local/bin/deepstrata`) or add that folder to your shell's PATH.

```sh
deepstrata launch
```

Follow the screens to choose a model, context, storage folder and access settings. The monitor opens after startup.
Use the address printed by Strata; the default local endpoint is `http://127.0.0.1:8080`. To chat in a browser, run
`deepstrata ui`: it opens the full page (Chat, Monitor) served by the App Host.
The Terminal opens a browser by itself only when you add `--open`.

For a script or AI agent, inspect a plan before downloading. This example selects ISTA-DASLab Q2_0 explicitly:

```sh
deepstrata launch --family qwen --model q2_0 --plan
deepstrata launch --family qwen --model q2_0 --yes --no-tui
```

`--no-tui` leaves the server running in the background. Check it, connect a tool, or stop it:

```sh
deepstrata launch --status
deepstrata connect
deepstrata stop
```

If a model is already running with different settings, add `--restart` to replace that run; `--yes` alone does not
restart it. A restart interrupts current answers. See [all launch options](TERMINAL_LAUNCH.md),
[advanced Terminal commands](TERMINAL.md), and [agent setup](AI_SETUP.md).

## Updating

**The page:** open Settings and find the update controls, check for updates, and use the offered Strata or engine update.
Unload running models before replacing the engine. Your model files and settings are kept.

**Terminal:**

```sh
deepstrata update --check
deepstrata update
deepstrata --version
```

With a model loaded, `deepstrata update` says which model, asks before it stops it, downloads the new engine while the model keeps
serving, stops it cleanly, updates, and asks whether to start it again with the same settings (answer no and it stays stopped;
the start line is printed). If the update fails the old engine stays in use and the model can be started again. The launch
monitor and the start screen show one quiet line when a newer engine is available; press `u` in the monitor to update.
In a script nothing is asked: `--yes` alone never stops a model (exit 7, nothing changed); `--stop-model` lets the update stop it
and `--start-again` starts it again afterwards (exit 8 if it did not start again). The next Terminal command uses the updated
Control component. The app, Control and engine have separate version numbers, so they can differ.

Use `deepstrata components rollback` to return to the previous engine. See [update details](../packaging/UPDATES.md).
Source checkouts have a [separate update workflow](SOURCE_INSTALL.md#updating).

## Where things are stored

| Item | Default location |
| --- | --- |
| Page chats and host state | `~/.deepstrata` (or `STRATA_HOME`) |
| Engine components, Windows | `%LOCALAPPDATA%\Strata\components` |
| Engine components, Linux / Mac | `$XDG_DATA_HOME/strata/components`, normally `~/.local/share/strata/components` |
| Terminal files | The `terminal` folder beside `components`; the installer prints the exact path |
| Models | The folder chosen in the page or launch screens; an existing model library is reused |

The page normally proposes `Documents/Strata/Models` for a new library. The Terminal discovers saved
folders from earlier installs. Use the path shown by the page or `deepstrata models list`, not an assumed folder name.
`--data-dir` changes where new downloads go; it does not move existing models unless you add `--move-data`.
See [model discovery](TERMINAL_LAUNCH.md#models-you-already-have).

## More options

- [Docker](DOCKER.md)
- [Build or run from a source checkout](SOURCE_INSTALL.md)
- [Multiple GPUs](MULTI_GPU.md)
- [Troubleshooting and logs](TROUBLESHOOTING.md)
- [Model compatibility](MODELS.md)
