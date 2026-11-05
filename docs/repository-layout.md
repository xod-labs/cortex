# Repository layout

What sits where in the Cortex repository, [`xod-labs/cortex`](https://github.com/xod-labs/cortex). Three of its trees are **the spec** — `agents/`, `templates/` and `docs/` — what a release puts in the store, `~/.cortex/versions/X.Y.Z`, and what a host project reads. Everything else is the tooling around it: the `cortex` command, the core it shares with the runtime, the runtime, the install scripts and the release cycle.

In this tree, `cortex/` is the repository's root. In a host project, the bootstrap and the docs use `cortex/` for the directory that `spec` names in `cortex.local.toml` — the spec of the version the project pins.

```
cortex/
├── README.md                          # What Cortex is, setup, the commands, how-tos
├── CHANGELOG.md                       # Every release — the single source of truth for versions
├── CONTRIBUTING.md                    # Test loop, conventions, branches, releases
├── CODE_OF_CONDUCT.md, SECURITY.md, LICENSE, NOTICE
├── CLAUDE.md                          # The bootstrap of an assistant working on Cortex itself
├── project-overview.md                # Cortex's own context, read by that bootstrap
├── project-context.md
├── install.sh                         # One-line install of the `cortex` command (Linux, macOS)
├── install.ps1                        # One-line install of the `cortex` command (Windows)
├── assets/                            # The logo
├── bin/
│   ├── check-english.sh               # English-only check (CI)
│   ├── setup-labels.sh                # GitHub labels for multi-phase ADRs
│   ├── setup-release.sh               # The repository's settings the release cycle relies on (ADR-009)
│   ├── setup_release.py               #   … what it runs
│   └── tests/
│
├── templates/
│   ├── bootstrap-instructions.md            # Bootstrap — single project mode (any AI tool)
│   ├── bootstrap-instructions-workspace.md  # Bootstrap — multi-project workspace mode
│   ├── project-overview.md.template         # Template: project overview (vision & business)
│   ├── project-context.md.template          # Template: technical context
│   └── workflow.md.template                 # Template for creating a project workflow
│
├── agents/                            # ── The spec (portable Markdown cascade) ──
│   ├── roles/                         # Layer 1: Business roles (stack-agnostic)
│   │   ├── prompt-manager.md          # Entry point (root, always active)
│   │   ├── engineering/               # architect, lead-backend/frontend, dba, platform, qa-automation,
│   │   │                              #   performance, support…
│   │   ├── product/                   # product-owner, business-analyst, market-analyst,
│   │   │                              #   innovation-funding-advisor
│   │   ├── security-compliance/       # security-engineer, compliance-officer, regulatory-compliance-writer
│   │   ├── data/                      # data-analyst
│   │   └── communication/             # tech-writer
│   │
│   ├── capabilities/                  # Layer 2: Loadable technical skills
│   │   ├── languages/                 # php, typescript
│   │   ├── frameworks/                # symfony, vue, starlight
│   │   ├── infrastructure/            # docker, kubernetes
│   │   ├── databases/                 # mysql, postgresql, mongodb
│   │   ├── release/                   # semver, changelog, conventional-commits, release-please
│   │   ├── search/                    # opensearch
│   │   ├── testing/                   # component-testing, e2e-testing
│   │   ├── practices/                 # code-comments, competitive-intelligence
│   │   └── security/                  # owasp
│   │
│   ├── personalities/                 # Layer 3: Personality themes
│   │   ├── h2g2/                      # H2G2 theme (The Hitchhiker's Guide)
│   │   └── breaking-bad/              # Breaking Bad / Better Call Saul theme
│   │
│   └── workflows/                     # Layer 4: Multi-agent orchestration templates
│       ├── engineering/               # feature-development, code-review, frontend-testing,
│       │                              #   release-automation, support-triage, adr-implementation
│       └── intelligence/              # tech-watch, market-study, innovation-funding
│
├── core/                              # ── cortex-core — the cascade in code (ADR-007) ──
│   ├── cortex_core/                   # resolution, merge semantics, capability catalog, prompt
│   │                                  #   assembly, overlay validation, the grammar of a project's
│   │                                  #   files — standard library only
│   └── tests/                         # runs on Python 3.11+ with nothing installed
│
├── cli/                               # ── the `cortex` command (ADR-008) ──
│   ├── cortex_cli/                    # every command `cortex --help` lists — built into a native binary
│   ├── build.py                       # the build of that binary (PyInstaller)
│   └── tests/
│
├── runtime/                           # ── ⚙️ cortex-runtime — the deployable engine ──
│   ├── cortex_runtime/                # agnostic API, agentic loop, StateStore,
│   │                                  #   security gate, job queue, model backends
│   ├── tests/                         # unit + integration suite
│   └── docs/                          # e.g. claude-cli-setup.md
│
├── mcp/                               # MCP servers (e.g. Jira Service Management)
├── deploy/                            # Docker image, compose stack, Traefik, DBHub profile
│
├── docs/
│   ├── getting-started.md             # Step-by-step install (design-time spec)
│   ├── extending-layers.md            # Practical guide for overlays (the cascade)
│   ├── creating-a-theme.md            # Guide for creating a personality theme
│   ├── migrating-to-the-binary.md     # From a git submodule or clone to the `cortex` command
│   ├── moving-to-xod-labs.md          # Cortex moved from adorey/cortex: what to update
│   ├── repository-layout.md           # This file
│   ├── process/                       # Delivering a multi-phase ADR
│   ├── adr/                           # Architecture Decision Records
│   └── api/                           # OpenAPI spec + Postman collection
│
├── changelog/                         # Per-version release notes (index: CHANGELOG.md)
│
└── .github/
    ├── workflows/                     # CI: the test suites, the binary, the release cycle (ADR-009)
    ├── actions/                       # The release cycle's own actions: gate, guard, draft…
    └── ISSUE_TEMPLATE/                # ADR epic, phase and task issues
```

> **Note on overlays:** the `agents/{roles,capabilities,personalities,workflows}/` trees also exist (mirrored) in host projects under `{workspace}/agents/...` and `{service}/agents/...` — those are the override locations, not part of Cortex itself. See [extending-layers.md](extending-layers.md).

Where to change what — a role, a capability, a theme, a workflow — is in [CONTRIBUTING.md](../CONTRIBUTING.md#-repository-structure-what-to-touch-where).
