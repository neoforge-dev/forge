# Node Bootstrap Checklist

This runbook describes the steps to onboard a new node (nova, sati, vega, etc.) into the FORGE fleet.

## Prerequisites

- [ ] **Tailscale Active**: The node must be connected to the Tailscale mesh network.
- [ ] **Git Repository**: The `FORGE` repository must be cloned to the node.
- [ ] **Python/uv**: `uv` must be installed on the node.
- [ ] **FORGE_WEBHOOK_TOKEN**: You must have the shared secret token used for authentication with the forged daemon.
- [ ] **forged daemon URL**: The URL of the lead node's forged daemon (e.g., `http://prya.ts.net:8081`).

## Bootstrap Procedure

1. **Navigate to the repository root**:
   ```bash
   cd ~/FORGE
   ```

2. **Run the bootstrap script**:
   Replace `<TOKEN>` with the `FORGE_WEBHOOK_TOKEN` and `<NODE_ID>` with the node name (e.g., `nova`).
   ```bash
   bash harness/scripts/xnode-bootstrap.sh \
       --hub-url http://prya.ts.net:8081 \
       --token <TOKEN> \
       --node-id <NODE_ID>
   ```

3. **Verify the installation**:
   The script will provide a verification summary. Ensure all critical steps show `[OK]`.

## Post-Bootstrap Verification

- [ ] **Check Listener Service**:
  ```bash
  # Linux
  systemctl --user status forge-xnode-listener.service
  # macOS
  launchctl list | grep com.forge.xnode.listener
  ```
- [ ] **Monitor Logs**:
  ```bash
  tail -f .forge/logs/xnode-listener.log
  ```
- [ ] **Verify Node Presence**:
  On the **lead node (prya)** or any active node:
  ```bash
  forge nodes list
  ```
  Ensure the new node appears in the list with a recent heartbeat.

- [ ] **Test Cross-Node Messaging**:
  From the **lead node (prya)**:
  ```bash
  forge lead send --to <NODE_ID> --subject "Bootstrap Verification" --body "Hello from prya"
  ```
  Check the new node's inbox or logs to confirm receipt.

## Troubleshooting

### Node not reachable / Hub connectivity failed
- Verify Tailscale is running: `tailscale status`.
- Ping the lead node: `ping prya.ts.net`.
- Ensure the forged daemon is running on the lead node.

### Initial heartbeat failed
- Check the `heartbeat.log`: `tail -n 20 .forge/logs/heartbeat.log`.
- Verify the `FORGE_WEBHOOK_TOKEN` is correct.

### Services not starting (Linux)
- Ensure your user has lingering enabled: `loginctl enable-linger $USER`.
- Check systemd logs: `journalctl --user -u forge-xnode-listener.service`.
