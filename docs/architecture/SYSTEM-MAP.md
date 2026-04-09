<!-- Owner: prya | Review-after: 2026-06-25 -->
<!-- Trigger: changes to cmd/forge/ or cmd/forged/ -->
<!-- See also: V3_ARCHITECTURE.md for prose details -->

# FORGE System Map

C4 Level 1 (Context) + Level 2 (Container) — how the system fits together.

## Context: Who uses FORGE and what it connects to

```mermaid
graph TB
    Human[👤 Human Operator<br/>Bogdan]

    subgraph FORGE["FORGE Platform"]
        CLI["forge CLI<br/>(Go binary)"]
        Daemon["forged daemon<br/>:8081 HTTP / :8082 WS"]
        DB[("SQLite<br/>forge-v3.db")]
        WS["WebSocket Hub<br/>Agent telemetry"]
        XNode["XNode<br/>Cross-node JSONL"]
    end

    subgraph Nodes["Fleet Nodes (Tailscale mesh)"]
        Prya["prya<br/>16GB · orchestrator hub"]
        Sati["sati<br/>64GB · heavy workloads"]
        Nova["nova<br/>48GB · iOS builds"]
        Vega["vega<br/>16GB · auxiliary"]
        Gaea["gaea<br/>16GB · M1 Pro laptop"]
    end

    subgraph Agents["Agent Pool"]
        Fleet["Fleet Agents<br/>kimi · gemini · minimax<br/>glm · pi · codex"]
        Worktree["Worktree Agents<br/>isolated git branches"]
    end

    subgraph External["External Services"]
        Stripe["Stripe<br/>Payments"]
        Railway["Railway<br/>Backend deploy"]
        CF["Cloudflare<br/>Pages + Workers"]
        Firebase["Firebase<br/>Auth + RTDB"]
        PostHog["PostHog<br/>Analytics"]
    end

    Human -->|"commands"| CLI
    CLI -->|"HTTP API"| Daemon
    Daemon -->|"ACID writes"| DB
    Daemon -->|"telemetry"| WS
    WS -->|"heartbeat"| Agents
    Daemon -->|"file sync"| XNode
    XNode -->|"Tailscale"| Nodes

    Agents -->|"dispatch results"| Daemon
    Fleet -->|"read tasks"| Daemon
    Worktree -->|"code changes"| CLI
```

## Container Details

| Container | Technology | Purpose | Port |
|-----------|-----------|---------|------|
| `forge` CLI | Go binary (`cmd/forge/`) | All fleet operations | — |
| `forged` daemon | Go binary (`cmd/forged/`) | HTTP API, task queue, patrols | :8081 |
| WebSocket Hub | Go (embedded in forged) | Agent telemetry, heartbeat | :8082 |
| SQLite | `forge-v3.db` | Task state, agent registry, events | — |
| XNode | JSONL files (`.forge/xnode/`) | Cross-node messaging | — |

## Related Docs

- **Prose architecture:** `docs/architecture/V3_ARCHITECTURE.md`
- **Agent orchestration:** `docs/architecture/MULTI_AGENT_ORCHESTRATION.md`
- **Agent FSM:** `docs/architecture/NODE-LEAD-FSM.md`
- **Task lifecycle:** `docs/architecture/TASK-FSM.md`
- **Dispatch flow:** `docs/architecture/DISPATCH-FLOW.md`
