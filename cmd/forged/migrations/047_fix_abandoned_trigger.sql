-- Fix: the reverse trigger tasks_sync_status_from_state must not overwrite
-- "abandoned" status when state transitions to FAILED (abandoned is terminal
-- and should be preserved over the generic "failed" mapping).

DROP TRIGGER IF EXISTS tasks_sync_status_from_state;

CREATE TRIGGER IF NOT EXISTS tasks_sync_status_from_state
AFTER UPDATE OF state ON tasks
WHEN NEW.state != OLD.state
  AND NEW.status != 'abandoned'
  AND CASE
        WHEN NEW.state = 'QUEUED' THEN 'queued'
        WHEN NEW.state = 'DISPATCHED' THEN 'assigned'
        WHEN NEW.state = 'RUNNING' THEN 'executing'
        WHEN NEW.state = 'BLOCKED' THEN 'paused'
        WHEN NEW.state IN ('COMPLETED', 'APPROVED') THEN 'completed'
        WHEN NEW.state = 'FAILED' THEN 'failed'
        ELSE NEW.status
      END != NEW.status
BEGIN
  UPDATE tasks
  SET status = CASE
                 WHEN NEW.state = 'QUEUED' THEN 'queued'
                 WHEN NEW.state = 'DISPATCHED' THEN 'assigned'
                 WHEN NEW.state = 'RUNNING' THEN 'executing'
                 WHEN NEW.state = 'BLOCKED' THEN 'paused'
                 WHEN NEW.state IN ('COMPLETED', 'APPROVED') THEN 'completed'
                 WHEN NEW.state = 'FAILED' THEN 'failed'
                 ELSE status
               END
  WHERE id = NEW.id;
END;

-- +migrate Down
DROP TRIGGER IF EXISTS tasks_sync_status_from_state;
