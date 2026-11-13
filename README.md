<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="apps/host/public/brand/strata-mark-dark.svg">
    <img src="apps/host/public/brand/strata-mark-light.svg" alt="DeepStrata" width="76">
  </picture>
</p>

<h1 align="center">DeepStrata</h1>

<h3 align="center">Run powerful AI on your own computer.</h3>
<p align="center">Chat, code and work with images, locally.</p>

<p align="center">
  <a href="#models">Models</a> · <a href="#install"><b>Install</b></a> ·
  <a href="#our-release">Our release</a> · <a href="#hardware">Hardware</a> ·
  <a href="#tools">Tools</a> · <a href="#help">Help</a>
</p>

<p align="center">
  <a href="https://github.com/ilfarpro/DeepStrata/releases/latest"><img src="https://img.shields.io/github/v/release/ilfarpro/DeepStrata?label=download&color=0ab887" alt="Download DeepStrata"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/ilfarpro/DeepStrata" alt="MIT license"></a>
  <a href="#built-on-strata"><img src="https://img.shields.io/badge/fork_of-Strata-6b7280?logo=github&logoColor=fff" alt="DeepStrata is a fork of Strata"></a>
</p>

**DeepStrata is a fork of [Strata](https://github.com/Niko1221/Strata).** The engine, the models and the measurements
come from that project; we run them under our own name and install beside the original. Own folders, own ports, own
update feed, and the command `deepstrata`. Model files are shared on purpose: a model you already have is never
downloaded twice. Every difference and the rules for keeping them through a sync are in
[DIVERGENCE.md](DIVERGENCE.md); our own plan and the items still open on it are in
[docs/roadmap/](docs/roadmap).

**Free, open source, and yours to run.** No account needed.

<a id="our-release"></a>

## What our release carries today

Our newest release is **DeepStrata 0.2.13.1** — the Terminal and the engines **0.1.61**, the first release on the
upstream 0.2.13 line (this tree synced with upstream 0.2.13 on 13 November 2026). The version is the upstream line we
are based on plus our own number, so `0.2.13.1`; the next release, built from this tree, is `0.2.13.2` on upstream 0.2.13
([how we number releases](DIVERGENCE.md#release-numbering-and-tags)).

- **The Terminal — `deepstrata`, Control 0.1.61** — for Linux x86-64 and Windows x86-64. The chat page rides inside it;
  `deepstrata ui` opens it in your browser.
- **The Qwen engine 0.1.61** (CUDA 13) — for Linux and Windows x86-64 with NVIDIA RTX 20, 30, 40 or 50 and driver
  580.65 or newer.
- **The DeepSeek V4.1-Flash engine 0.1.61** (CUDA 13) — the same two systems, on NVIDIA RTX 30, 40 or 50 with driver
  580.65 or newer.
- The Windows parts are built on a Windows PC from the same commit and signed here. SmartScreen may ask once; the
  signed update feed checks the sha256 of every file before anything is installed.
- **Not built yet:** the macOS and ARM64 Linux Terminals, and the AMD, Intel and Mac engines. Our installer runs
  everywhere it knows and stops with a plain line naming the package this release does not carry — nothing half
  installed. Until our own packages land on those systems, use a [source build](docs/SOURCE_INSTALL.md) or
  [Docker](docs/DOCKER.md).
- Every command on this page can be run with `--plan` first, as the examples show. A plan downloads and changes
  nothing: it prints the download, the disk and the memory your computer needs, and names what your computer is missing.

## Models

Model packs come from **ISTA-DASLab**, **Unsloth** and other providers; the pack you pick must be compatible and fit
your memory and disk ([provider order](docs/MODELS.md#quick-start)). Our release carries both engines today —
**DeepSeek** and **Qwen** — for NVIDIA cards under CUDA 13; AMD, Intel and Mac engine builds still mean a
[source build](docs/SOURCE_INSTALL.md).

<details open>
<summary><img src="docs/media/models/deepseek.svg" width="22" alt="" align="middle"> &nbsp;<b>DeepSeekV4.1-Flash-1017</b> — runs from our release today</summary>

In the **browser page** and the **Terminal**. What does not fit in memory is read from your SSD, so it
needs a lot of free disk space — an NVMe drive is what it was measured on.

| Set | Download | RAM to run |
| --- | ---: | ---: |
| ISTA-DAS Coder (half of the experts removed) | 191 GB | 24 GB |
| ISTA-DAS 1.75 bpw | 239 GB | 34 GB |
| Unsloth UD-IQ1_S | 241 GB | 36 GB |
| ISTA-DAS 2.00 bpw | 257 GB | 40 GB |
| Unsloth UD-IQ1_M | 258 GB | 40 GB |
| ISTA-DAS 2.25 bpw | 274 GB | 44 GB |
| Unsloth UD-IQ2_XXS | 308 GB | 48 GB |
| Unsloth UD-Q2_K_XL | 343 GB | 58 GB |
| Unsloth UD-IQ3_XXS | 403 GB | 68 GB |
| Unsloth UD-IQ3_S | 432 GB | 77 GB |
| Unsloth UD-Q3_K_XL | 493 GB | 82 GB |
| Unsloth UD-Q4_K_XL | 501 GB | 298 GB |
| Unsloth UD-Q8_K_XL | 508 GB | 306 GB |

The five sets from UD-IQ2_XXS to UD-Q3_K_XL are the fork's own work: their n-gram tables (Q5_K, Q6_K, MXFP8) and their
mixed expert layers were unreadable to any engine until our 0.1.59 and 0.1.60 grew those readers. Their quality is not
measured against the original yet, and on a Mac or an ARM computer they stay hidden until they run there. The two last
sets are for machines with hundreds of GB of RAM or several cards
([the whole story](docs/DEEPSEEK41.md)).

```sh
deepstrata launch --family deepseek41 --model ist-das-2.00bpw --plan
deepstrata launch --family deepseek41 --model ud-iq2_xxs --plan      # one of the five big Unsloth sets we made loadable
```

Output is the speed of the answer, prompt is how fast Strata reads your input. Both measured with the 2.00 bpw set on
the machines named, on Linux with CUDA 13 — the boxes, the drives and the rest of the hardware are in
[DEEPSEEK41.md](docs/DEEPSEEK41.md).

| Hardware | Output | Prompt |
| --- | ---: | ---: |
| RTX 5070 12 GB, 64 GB RAM | about 4.7 tokens/s | 121 tokens/s (4K), 231 (8K) |
| RTX 3060 12 GB, 46 GB RAM | 8 to 10 tokens/s | 149 tokens/s (8K) |

[Memory, disk and details](docs/DEEPSEEK41.md) · [Release notes](https://github.com/ilfarpro/DeepStrata/releases)

</details>

<details>
<summary><img src="docs/media/models/qwen.svg" width="22" alt="" align="middle"> &nbsp;<b>Qwen3.8-Flash-Next</b> — runs from our release today</summary>

| Provider | Choices |
| --- | --- |
| **ISTA-DASLab** | Q2\_0, IQ2\_XS, IQ3\_XXS, IQ3\_S. [Coder: IQ1\_M](docs/MODELS.md#coder) for coding. |
| **Unsloth** | UD-IQ4\_XS, UD-Q4\_K\_XL, UD-Q5\_K\_XL, UD-Q6\_K\_XL, Q8\_0 |
| **Gyro · agentionai** | [Gyro-S, Gyro-M](https://huggingface.co/agentionai/Qwen3.8-Flash-Next-Gyro-GGUF). Lower memory use but slower. |
| **Other options** | [Swift 1.5](docs/MODELS.md#swift-15), [NVFP4](docs/NVFP4.md), [OrcaRouter](docs/ORCA.md) |

[Compare sizes, memory needs and compatibility](docs/MODELS.md).

</details>

<details>
<summary><img src="docs/media/models/qwen.svg" width="22" alt="" align="middle"> &nbsp;<b>Qwen3.6-35B-A3B</b> — runs from our release today</summary>

**Unsloth:** UD-Q4_K_XL, UD-Q6_K_XL, Q8_0. These are the currently documented packs for this model.

[Model guide and compatibility](docs/QWEN36.md).

</details>

<a id="the-other-sizes"></a>

Not sure what fits? [Choose a model for your computer](docs/MODELS.md#pick-by-memory).

<a id="install"></a>

## Install

### Linux x86-64 — what our release ships

```sh
curl -fsSL --connect-timeout 15 --max-time 120 https://github.com/ilfarpro/DeepStrata/releases/latest/download/install.sh -o install.sh && sh install.sh
```

It needs no administrator rights, puts `deepstrata` on your PATH and installs beside Strata, never over it. Then:

```sh
deepstrata launch --family deepseek41 --model ist-das-2.00bpw
```

Follow the prompts to download and start a model. [Terminal guide](docs/TERMINAL_LAUNCH.md).

<p align="center">
  <img src="docs/media/terminal-monitor.png" width="900" alt="DeepStrata terminal showing model activity, speed, memory and recent requests">
  <br><sub>Watch your model and hardware while it runs.</sub>
</p>

### Windows x86-64

PowerShell, no administrator rights. Open a new terminal afterwards, so that `deepstrata` is on the PATH:

```powershell
irm -TimeoutSec 120 https://github.com/ilfarpro/DeepStrata/releases/latest/download/install.ps1 | iex
```

Then `deepstrata launch` as above. Windows SmartScreen may ask once before the unsigned Terminal runs; the signed feed
still checks the sha256 of every file before anything is installed.

### macOS and the chat page

Our release folder carries no macOS package yet, so we do not print that command here.
[Build from source](docs/SOURCE_INSTALL.md) · [Docker](docs/DOCKER.md) ·
[Full install guide](docs/INSTALL.md).

<details>
<summary>What a source build gives you today</summary>

The chat page in your browser (start a model and run `deepstrata ui`; chat, files and a live preview in one window),
the Terminal on macOS, and the engine
backends our feed does not carry yet: AMD HIP, Intel SYCL, Apple Metal and Strix Halo. The same code our packages are
built from; it builds on your machine instead of ours.

<p align="center">
  <img src="docs/media/app-chat.png" width="900" alt="The chat page with a chat and live code preview side by side">
  <br><sub>Chat, files and a live preview in one window (the page rides in the Terminal archive).</sub>
</p>

</details>

<a id="what-you-need"></a>

## Hardware

**Runs from our release today**

| Supported hardware | Needs |
| --- | --- |
| **Linux or Windows x86-64 with NVIDIA GeForce RTX 20 / 30 / 40 / 50, 12 GB or more** | Driver 580.65 or newer (CUDA 13). The Qwen engines also run on the RTX 20 series; the DeepSeek engine needs RTX 30, 40 or 50. [NVIDIA setup](docs/INSTALL.md#what-you-need) |

**Built and measured, our packages for them are still to come** — a [source build](docs/SOURCE_INSTALL.md) runs them now.

| Hardware | Guide |
| --- | --- |
| **AMD Radeon RX 7700 / 7800 / 7900, RX 9060 / 9070, AI PRO R9700** | [AMD setup](docs/AMD_HIP.md) |
| **AMD Radeon RX 6800 / 6900 · experimental** | [RX 6000 setup](docs/AMD_HIP.md#rdna2-gfx1030) |
| **Intel Arc · Linux** | [Intel setup](docs/INTEL_ARC.md) |
| **Apple Silicon · M1 or newer** | [Mac setup](docs/MAC.md) |
| **AMD Ryzen AI Max · Strix Halo** | [Strix Halo setup](docs/STRIX_HALO.md) |
| **NVIDIA DGX Spark · Linux** | [DGX Spark setup](docs/DGX_SPARK_LINUX.md) |

<a id="using-it"></a>

## Tools

**Claude Code · Codex CLI · OpenCode · oh-my-pi · pi.dev · Hermes Agent · OpenClaw**, and more through the OpenAI- and
Anthropic-compatible APIs.

Run **`deepstrata connect`** to see setup instructions for your tool.
[Connection guide](docs/TERMINAL_LAUNCH.md#connect-your-apps-deepstrata-connect).

A tool talks to a model you are serving, so from our release today that means the Linux and Windows NVIDIA path above.

<a id="guides"></a>
<a id="how-fast-is-it"></a>
<a id="how-does-it-work"></a>
<a id="something-went-wrong"></a>

## Help

[All guides](docs) · [Troubleshooting](docs/TROUBLESHOOTING.md) ·
[Report a bug](https://github.com/ilfarpro/DeepStrata/issues/new/choose) ·
[Benchmarks](docs/COMMUNITY_BENCHMARKS.md) · [How it works](docs/HOW_IT_WORKS.md) · [Contribute](CONTRIBUTING.md)

Issues and pull requests come to this repository. We do not send them upstream; the scope of a large change is agreed
in an issue here first ([CONTRIBUTING.md](CONTRIBUTING.md)).

## Built on Strata

DeepStrata is a fork of **Strata**, written and measured by
[Niko1221](https://github.com/sponsors/Niko1221), whose original is at
[github.com/Niko1221/Strata](https://github.com/Niko1221/Strata). The engine that makes these models fit on a home
computer is his work; sponsoring him there is the direct way to say so.

DeepStrata is a separate project: our own releases, our own signed update feed, our own issues. What we changed and why
is in [DIVERGENCE.md](DIVERGENCE.md).

[MIT license](LICENSE) · [Credits and model licenses](docs/HOW_IT_WORKS.md#credits) · [Privacy](docs/SAFETY.md)

<a id="option-c-let-your-ai-set-it-up"></a>

<sub>For AI tools and automated readers: <a href="AGENTS.md">technical reference</a> · <a href="docs/AI_SETUP.md">setup instructions</a>.</sub>
