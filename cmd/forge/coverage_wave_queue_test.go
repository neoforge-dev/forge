// coverage_wave_queue_test.go - Coverage wave for queue.go
package main

import (
	"testing"
	"time"

	"github.com/neoforge-dev/forge/internal"
)

func TestQueueFormatAge(t *testing.T) {
	tests := []struct {
		name     string
		duration time.Duration
		want     string
	}{
		{
			name:     "less than 1 hour",
			duration: 30 * time.Minute,
			want:     "< 1h",
		},
		{
			name:     "exactly 1 hour",
			duration: 1 * time.Hour,
			want:     "1h",
		},
		{
			name:     "5 hours",
			duration: 5 * time.Hour,
			want:     "5h",
		},
		{
			name:     "23 hours",
			duration: 23 * time.Hour,
			want:     "23h",
		},
		{
			name:     "exactly 1 day",
			duration: 24 * time.Hour,
			want:     "1d",
		},
		{
			name:     "3 days",
			duration: 72 * time.Hour,
			want:     "3d",
		},
		{
			name:     "6 days",
			duration: 144 * time.Hour,
			want:     "6d",
		},
		{
			name:     "exactly 1 week",
			duration: 168 * time.Hour,
			want:     "1w",
		},
		{
			name:     "2 weeks",
			duration: 336 * time.Hour,
			want:     "2w",
		},
		{
			name:     "negative duration",
			duration: -1 * time.Hour,
			want:     "just now",
		},
		{
			name:     "zero duration",
			duration: 0,
			want:     "< 1h",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := queueFormatAge(tt.duration)
			if got != tt.want {
				t.Errorf("queueFormatAge(%v) = %q, want %q", tt.duration, got, tt.want)
			}
		})
	}
}

func TestCalculateDepth(t *testing.T) {
	tests := []struct {
		name     string
		items    []internal.QueueItem
		expected *internal.QueueDepth
	}{
		{
			name:     "empty queue",
			items:    []internal.QueueItem{},
			expected: &internal.QueueDepth{},
		},
		{
			name: "all statuses",
			items: []internal.QueueItem{
				{Status: "QUEUED"},
				{Status: "DISPATCHED"},
				{Status: "RUNNING"},
				{Status: "BLOCKED"},
				{Status: "COMPLETED"},
				{Status: "FAILED"},
				{Status: "APPROVED"},
			},
			expected: &internal.QueueDepth{
				Queued:     1,
				Dispatched: 1,
				Running:    1,
				Blocked:    1,
				Completed:  1,
				Failed:     1,
				Approved:   1,
			},
		},
		{
			name: "multiple queued",
			items: []internal.QueueItem{
				{Status: "QUEUED"},
				{Status: "QUEUED"},
				{Status: "QUEUED"},
			},
			expected: &internal.QueueDepth{
				Queued: 3,
			},
		},
		{
			name: "case sensitive status",
			items: []internal.QueueItem{
				{Status: "queued"},
				{Status: "Queued"},
				{Status: "QUEUED"},
			},
			expected: &internal.QueueDepth{
				Queued: 1,
			},
		},
		{
			name: "unknown status ignored",
			items: []internal.QueueItem{
				{Status: "QUEUED"},
				{Status: "UNKNOWN"},
				{Status: "PENDING"},
			},
			expected: &internal.QueueDepth{
				Queued: 1,
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := calculateDepth(tt.items)
			if got.Queued != tt.expected.Queued {
				t.Errorf("Queued = %d, want %d", got.Queued, tt.expected.Queued)
			}
			if got.Dispatched != tt.expected.Dispatched {
				t.Errorf("Dispatched = %d, want %d", got.Dispatched, tt.expected.Dispatched)
			}
			if got.Running != tt.expected.Running {
				t.Errorf("Running = %d, want %d", got.Running, tt.expected.Running)
			}
			if got.Blocked != tt.expected.Blocked {
				t.Errorf("Blocked = %d, want %d", got.Blocked, tt.expected.Blocked)
			}
			if got.Completed != tt.expected.Completed {
				t.Errorf("Completed = %d, want %d", got.Completed, tt.expected.Completed)
			}
			if got.Failed != tt.expected.Failed {
				t.Errorf("Failed = %d, want %d", got.Failed, tt.expected.Failed)
			}
			if got.Approved != tt.expected.Approved {
				t.Errorf("Approved = %d, want %d", got.Approved, tt.expected.Approved)
			}
		})
	}
}
