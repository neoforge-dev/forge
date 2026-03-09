-- Keep legacy task status and task state fields synchronized.
CREATE TRIGGER IF NOT EXISTS tasks_sync_state_from_status
AFTER UPDATE OF status ON tasks
WHEN NEW.status != OLD.status
  AND CASE
        WHEN NEW.status IN ('requested', 'planned', 'queued') THEN 'QUEUED'
        WHEN NEW.status = 'assigned' THEN 'DISPATCHED'
        WHEN NEW.status = 'executing' THEN 'RUNNING'
        WHEN NEW.status = 'paused' THEN 'BLOCKED'
        WHEN NEW.status = 'completed' THEN 'COMPLETED'
        WHEN NEW.status IN ('failed', 'abandoned') THEN 'FAILED'
        ELSE NEW.state
      END != NEW.state
BEGIN
  UPDATE tasks
  SET state = CASE
                WHEN NEW.status IN ('requested', 'planned', 'queued') THEN 'QUEUED'
                WHEN NEW.status = 'assigned' THEN 'DISPATCHED'
                WHEN NEW.status = 'executing' THEN 'RUNNING'
                WHEN NEW.status = 'paused' THEN 'BLOCKED'
                WHEN NEW.status = 'completed' THEN 'COMPLETED'
                WHEN NEW.status IN ('failed', 'abandoned') THEN 'FAILED'
                ELSE state
              END
  WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS tasks_sync_status_from_state
AFTER UPDATE OF state ON tasks
WHEN NEW.state != OLD.state
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
DROP TRIGGER IF EXISTS tasks_sync_state_from_status;
