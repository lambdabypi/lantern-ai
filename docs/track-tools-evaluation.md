# Track tools evaluation (3 Oct 2026)

These are the tools suggested for the Personal AI track, each checked against its official docs. Sources are listed at the end.

## Summary

| Tool | What it is | Fit with our stack | Integration | Verdict |
|---|---|---|---|---|
| **OpenShell** | Sandbox runtime: Landlock filesystem, default-deny egress proxy, seccomp, credential placeholders | Execution-sandbox backend. It supports our broker but doesn't replace it | Medium | **Maybe.** Try it as a sandbox backend on a Nebius Ubuntu VM |
| **NemoClaw** | NVIDIA's reference stack for running OpenClaw / Hermes / Deep Agents inside OpenShell | Runs *other* agents, has no SDK, and supports one operator per host | High | **Skip** as a dependency |
| **Hermes Agent** | Nous Research's self-improving agent (Python, MIT) | Could replace our loop, but uses one process per user and capped memory files | High (core) / Low (formats) | **Skip as the core.** Borrow formats |
| **Nebius Serverless Endpoints** | Runs any container on managed CPU/GPU VMs behind an HTTPS URL | Can host our FastAPI image | Low–medium | **Maybe.** Same price as a VM, with no scale-to-zero |
| **Nebius Serverless Jobs** | One-off batch containers | No documented cron, and the minimum timeout is 1 h | — | **Skip.** Keep APScheduler |

## OpenShell

- **What it does:** a gateway (control plane) manages sandboxes. Each sandbox is enforced at four levels: filesystem (Landlock), network (an egress proxy that denies everything unless allowed), process (seccomp, no privilege escalation) and provider credentials. Code inside sees only opaque placeholders; the proxy swaps in the real key once policy allows the call. It also has a policy advisor and a prover for policy changes. The old managed inference route (`openshell inference`) has been removed.
- **Requirements:** Debian/Ubuntu on x86_64 or arm64 with **kernel ≥ 6.2**, plus Docker ≥ 28 or Podman 5. Kubernetes is optional. No GPU is needed. Windows works only through WSL 2, which is experimental. It is undocumented whether it can run inside Daytona.
- **How we'd use it:** the Python SDK `openshell` (PyPI 0.1.2, Python ≥ 3.11, gRPC to the gateway): `SandboxClient.from_active_cluster()`, `Sandbox(...)`, `SandboxSession.exec([...])` / `exec_python(fn, args=...)`. It can run any command or Python function, not just agent harnesses.
- **Policy:** YAML with `filesystem_policy`, `landlock` and `network_policies` (per host, port and binary). Only network policy can change while a sandbox runs; filesystem and process rules are fixed when the sandbox is created.
- **Maturity:** Apache-2.0, v0.1.2 (28 Sep 2026). Very young, and the API is still moving.
- **Fit:** a strong second backend behind `app/sandbox/base.py`. Credential placeholders could keep the Tavily and Token Factory keys out of untrusted code. It does **not** cover app-level rules (memory-file scopes, ask-before-acting), so our permission broker stays.

## NemoClaw

- **What it is:** a host CLI plus a YAML blueprint plus an agent integration layer, sitting on the OpenShell gateway. It supports OpenClaw (the default), Hermes and LangChain Deep Agents Code. There is no documented path for custom agents beyond images that meet its agent-image contract.
- **Requirements:** ≥ 4 vCPU, 8 GB RAM, 20 GB disk, Node 22.19+, Docker or Podman, and k3s. The sandbox image is about 2.4 GB.
- **Inference:** supports a custom OpenAI-compatible endpoint, but Token Factory isn't documented or verified.
- **Users and API:** "single trusted operator per host"; multi-tenant use is out of scope. There is no Python SDK ("NemoClaw does not offer that SDK today").
- **Maturity:** Apache-2.0, labelled alpha, v0.0.130.
- **Fit:** it can't host our agent, it conflicts with a multi-user web demo, and it's heavy for a small VM.

## Hermes Agent

- **Memory:** `MEMORY.md` + `USER.md`, entries separated by `§`, capped at about 2.2k and 1.4k characters, and managed by a `memory` tool (add / replace / remove). Session history is stored in SQLite with FTS5 search.
- **Skills:** `SKILL.md` folders in the agentskills.io format, with optional `scripts/`, `references/` and `assets/`.
- **Other features:** a built-in cron, a messaging gateway, a local dashboard, and terminal backends including Daytona.
- **Nebius:** native provider `nebius-token-factory` (`NEBIUS_API_KEY`).
- **Users:** one operator per instance. Multiple users means one profile and one process per user.
- **Embedding:** possible via `from run_agent import AIAgent`, but there's no wheel; it's installed from a source checkout. It also has an OpenAI-compatible HTTP server.
- **Maturity:** MIT, v0.21.5 (24 Sep 2026), pre-1.0, very active.
- **Fit:** adopting it as the core would lose our per-fact visible memory, multi-user isolation and broker. Instead, borrow:
  - agentskills.io-compatible `SKILL.md` front matter;
  - a memory tool with add / replace / remove actions;
  - natural-language cron as a UX reference.

## Nebius Serverless

- **Endpoints:**
  - `nebius ai endpoint create --image ... --platform cpu-d3 --preset ... --public --container-port ... --auth token --token ...` gives an HTTPS URL with optional bearer auth.
  - No scale-to-zero is documented; the only cost control is `nebius ai endpoint stop`.
  - The container disk is wiped on stop, so persistent data needs a mounted shared filesystem or Object Storage (`--volume`).
- **Jobs:** `nebius ai job create ... --timeout`. Minimum 1 h, maximum 168 h. No native scheduling was found.
- **Cost:** endpoints use Compute prices, and cpu-d3 is $0.015 per vCPU-hour + $0.0045 per GiB-hour.
  - `2vcpu-8gb` ≈ $0.066/h ≈ **$92** for 20 Oct–15 Dec (1,344 h), including a 20 GiB disk.
  - `4vcpu-16gb` ≈ $177, which is over budget.
  - It's unconfirmed whether endpoints accept `2vcpu-8gb`.

## Recommended architecture for build step 4

1. **Keep our own agent loop** (`app/agent/loop.py`) in-process in FastAPI, using OpenAI-style tool calling through the router.
   - **First task:** confirm with a live call that Nemotron on Token Factory supports native `tools` / function calling. Fall back to JSON-mode tool selection if it doesn't.
2. **Model use in the loop:**
   - Nano is the default.
   - `thinking=False` for simple turns (chit-chat, extraction).
   - Thinking on, or escalation to Ultra, for multi-step planning.
3. **One choke point from day one.** The loop calls tools only through a `ToolRegistry.call(name, args, ctx)` function. In step 4 it only checks an allowlist and writes the audit log. In step 8a it becomes the full permission broker without changing the loop.
4. **Formats borrowed from Hermes:** agentskills.io-compatible `SKILL.md` front matter (our permissions stay in `skill.yaml`), and memory tool actions `add` / `replace` / `remove`, operating on one Markdown file per memory.
5. **Sandbox:** `local` and `daytona` backends first. Then try `openshell` on a Nebius Ubuntu 24.04 VM (kernel ≥ 6.2), behind the same interface. The core never depends on it.
6. **Background tasks:** APScheduler, not Serverless Jobs.
7. **Deployment:** one **Nebius CPU VM** (Ubuntu 24.04, `2vcpu-8gb`, about $92 from 20 Oct) running the Docker app and, if adopted, the OpenShell gateway.
   - A Serverless Endpoint costs the same, but it can't host the OpenShell gateway and needs a separate shared filesystem for user data.
   - Credits can't comfortably pay for both.
8. **README:** a "Track tools we evaluated" section that cites OpenShell, NemoClaw and Hermes, and explains what we used or borrowed and why.

## Sources

- OpenShell: [github.com/NVIDIA/OpenShell](https://github.com/NVIDIA/OpenShell) · [docs.nvidia.com/openshell](https://docs.nvidia.com/openshell/latest/index.html)
- NemoClaw: [docs.nvidia.com/nemoclaw](https://docs.nvidia.com/nemoclaw/latest/) · [github.com/NVIDIA/NemoClaw](https://github.com/NVIDIA/NemoClaw)
- Hermes Agent: [github.com/NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) · [hermes-agent docs](https://hermes-agent.nousresearch.com/docs/) · [NVIDIA blog: Hermes + NemoClaw](https://developer.nvidia.com/blog/deploy-self-evolving-agents-for-faster-more-secure-research-with-a-hermes-agent-and-nvidia-nemoclaw/)
- Nebius Serverless: [overview](https://docs.nebius.com/serverless/overview) · [endpoints quickstart](https://docs.nebius.com/serverless/quickstart/endpoints) · [jobs](https://docs.nebius.com/serverless/jobs/manage) · [compute pricing](https://docs.nebius.com/compute/resources/pricing)
