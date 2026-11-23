# Workflow: Launch

<!-- Can be overridden by {service}/agents/workflows/product/launch.md -->

| Step | Role | Notes |
|---|---|---|
| 1 | `roles/engineering/architect.md` (@Slartibartfast) | design |
| 2 | `roles/product/market-analyst.md` (@Zarniwoop) | the market |
| 3 | `@Explore` + `roles/engineering/lead-backend.md` (@Hactar) | the code |
| 4 | `architect` (@Slartibartfast) + `product/market-analyst` (@Zarniwoop) | sign-off |

**Agent:** @Explore / @Hactar
**Agents :** `engineering/lead-backend` + `product/market-analyst` · **Load:** `release/semver.md`

- **@Hactar** (lead-backend) checks the code; @Explore (read-only) searches it.
- Read **`cortex/agents/workflows/engineering/code-review.md`**, then `cortex/agents/roles/`.
- Then `workflows/engineering/code-review.md#rules`, `agents/roles/engineering/architect.md:3`.
- Ask @nexus, @backend, @cortex or `@Explore`; write to ops@example.com; install `@types/node` (@types/node).
- @Ghost (subagent), @general-purpose (a subagent), (@Explore) and (@Nobody) are no dispatch.
- Each service reads `{service}/agents/roles/{category}/{role}.md`, or `cortex/agents/roles/<category>/*.md`.
- A clone: `ai/cortex/agents/roles/ghost.md`, `../roles/ghost.md`, https://github.com/xod-labs/cortex/blob/main/agents/roles/ghost.md.
- Not checked: `cortex.toml`, `project-context.md`, `agents/project-context.md`, `docs/ghost.md`, `capabilities/ghost.md`.

```markdown
**Agent:** `product/ghost` — `roles/ghost/in-a-code-block.md` (@Ghost), `cortex/docs/ghost.md`
```

~~~
cortex/templates/ghost.md
~~~

<!--
  roles/ghost/in-a-comment.md (@Ghost)
-->
