# Agent Packet 03: XNode Lead Messaging

Purpose: communicate across nodes through lead channels with strict delivery.

## 1. Preflight + Send

```bash
forge lead send \
  --to-node prya \
  --task-id TASK-123 \
  --summary "Need prya lead decision" \
  --priority high \
  --strict
```

`--strict` runs preflight and enforces `--requires-ack`, `--realtime`, and `--require-realtime-delivery`.

## 2. Ack + Tracking

```bash
forge lead ack \
  --message-id MSG_ID \
  --status ack \
  --note "received" \
  --realtime \
  --durable \
  --require-realtime-delivery
forge lead pending-acks --node prya --json
forge lead acks --node prya --limit 20 --json
```

## 3. Exception Relay

> ⚠️ **NOT YET IMPLEMENTED.** `forge xnode relay` does not exist in the current CLI.
> For urgent cross-node unblocks, use:
> ```bash
> forge lead send --to-node <node> --priority urgent --strict
> forge notify telegram "Urgent: check lead inbox on <node>"
> ```

For normal lead-to-lead communication:
```bash
forge lead send \
  --to-node prya \
  --task-id TASK-123 \
  --summary "Need prya lead decision" \
  --priority high \
  --strict
```

## 4. Policy

1. Default path is lead-to-lead.
2. Agent-to-agent cross-node only with `--exception`.
3. Cross-node send should use `--strict`; cross-node ack should use `--require-realtime-delivery`.
