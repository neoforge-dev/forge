package interfaces

// LaneManager manages Dark Factory lanes
type LaneManager interface {
	CreateLane(name string, config LaneConfig) error
	GetLane(name string) (Lane, error)
	ProcessTask(taskID string, laneName string) error
	PromoteTask(taskID string, fromLane, toLane string) error
	GetLaneStatus(name string) (LaneStatus, error)
}

// GateExecutor executes quality gates
type GateExecutor interface {
	ExecuteGate(gate Gate, taskID string) (GateResult, error)
	GetGateStatus(taskID string) (GateStatus, error)
}

// LaneConfig configuration for a lane
type LaneConfig struct {
	EntryCriteria []string // Criteria to enter this lane
	Gates         []Gate  // Quality gates to pass
	OnSuccess     string  // Lane to promote to on success
	OnFailure     string  // Lane to move to on failure
	Priority      int     // Lane priority (lower = faster)
}

// Lane represents a Dark Factory lane
type Lane interface {
	GetName() string
	GetStatus() LaneStatus
	GetConfig() LaneConfig
}

// Gate represents a quality gate
type Gate struct {
	Name     string // Gate name
	Type     string // "command", "http", "approval", "custom"
	Command  string // Command to execute (for command type)
	URL      string // URL to check (for http type)
	Required bool   // Whether gate failure blocks promotion
	Timeout  int    // Timeout in seconds
}

// GateResult result of gate execution
type GateResult struct {
	Passed  bool   // Whether gate passed
	Output string // Gate output/logs
	Duration int  // Execution time in ms
	Error   string // Error message if failed
}

// GateStatus current status of gates for a task
type GateStatus struct {
	TaskID      string            // Task ID
	CurrentGate string            // Current gate being executed
	Status      string            // "pending", "running", "passed", "failed"
	GateResults []GateResult     // Results of each gate
	StartedAt   int64            // Unix timestamp when started
	CompletedAt *int64           // Unix timestamp when completed
}

// LaneStatus current status of a lane
type LaneStatus struct {
	Name      string // Lane name
	TaskCount int    // Number of tasks in lane
	Status    string // "active", "paused", "draining"
}

// LaneTask represents a task within a Dark Factory lane
type LaneTask struct {
	ID          string
	Domain      string
	Project     string
	Type        string
	Priority    int
	Status      string
	Lane        string
	AssignedTo  string
}
