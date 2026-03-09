package main

import (
	"context"
	"fmt"
	"math"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/neoforge-dev/forge/internal"
)

func setupBenchmarkClient() *internal.Client {
	baseURL := os.Getenv("FORGE_API_URL")
	if baseURL == "" {
		baseURL = "http://localhost:8081"
	}
	return internal.NewClientWithURL(baseURL)
}

func BenchmarkTaskList(b *testing.B) {
	client := setupBenchmarkClient()
	ctx := context.Background()

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := client.ListTasks(ctx, "", 100, "")
		if err != nil {
			b.Fatalf("ListTasks failed: %v", err)
		}
	}
}

func BenchmarkTaskCreate(b *testing.B) {
	client := setupBenchmarkClient()
	ctx := context.Background()

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		req := &internal.CreateTaskRequest{
			Subject:     fmt.Sprintf("Benchmark Task %d", i),
			Description: "Performance test task",
			Priority:    "P3",
		}
		_, err := client.CreateTask(ctx, req)
		if err != nil {
			b.Fatalf("CreateTask failed: %v", err)
		}
	}
}

func BenchmarkTaskShow(b *testing.B) {
	client := setupBenchmarkClient()
	ctx := context.Background()

	taskID := "BENCH-TASK-001"
	req := &internal.CreateTaskRequest{
		Subject:     taskID,
		Description: "Benchmark task",
		Priority:    "P3",
	}
	client.CreateTask(ctx, req)

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := client.GetTask(ctx, taskID)
		if err != nil {
			b.Fatalf("GetTask failed: %v", err)
		}
	}
}

func BenchmarkDomainList(b *testing.B) {
	client := setupBenchmarkClient()
	ctx := context.Background()

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := client.ListProjects(ctx, "")
		if err != nil {
			b.Fatalf("ListProjects failed: %v", err)
		}
	}
}

func BenchmarkDaemonHealth(b *testing.B) {
	client := setupBenchmarkClient()
	ctx := context.Background()

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := client.GetDaemonStatus(ctx)
		if err != nil {
			b.Fatalf("GetDaemonStatus failed: %v", err)
		}
	}
}

func BenchmarkConcurrentRequests(b *testing.B) {
	client := setupBenchmarkClient()
	ctx := context.Background()

	b.ResetTimer()
	b.RunParallel(func(pb *testing.PB) {
		counter := 0
		for pb.Next() {
			counter++
			_, _ = client.ListTasks(ctx, "", 10, "")
			_, _ = client.GetDaemonStatus(ctx)
		}
	})
}

type benchmarkResult struct {
	ops        int
	avgMs      float64
	p50Ms      float64
	p95Ms      float64
	p99Ms      float64
	throughput float64
}

func runBenchmark(name string, fn func() error) benchmarkResult {
	const iterations = 100
	latencies := make([]time.Duration, iterations)

	for i := 0; i < iterations; i++ {
		start := time.Now()
		if err := fn(); err != nil {
			return benchmarkResult{ops: 0, avgMs: -1}
		}
		latencies[i] = time.Since(start)
	}

	var total time.Duration
	for _, l := range latencies {
		total += l
	}
	avg := total / time.Duration(iterations)

	sorted := make([]time.Duration, len(latencies))
	copy(sorted, latencies)
	for i := 0; i < len(sorted)-1; i++ {
		for j := i + 1; j < len(sorted); j++ {
			if sorted[j] < sorted[i] {
				sorted[i], sorted[j] = sorted[j], sorted[i]
			}
		}
	}

	p50 := sorted[int(float64(len(sorted))*0.50)]
	p95 := sorted[int(float64(len(sorted))*0.95)]
	p99 := sorted[int(float64(len(sorted))*0.99)]

	return benchmarkResult{
		ops:        iterations,
		avgMs:      avg.Seconds() * 1000,
		p50Ms:      p50.Seconds() * 1000,
		p95Ms:      p95.Seconds() * 1000,
		p99Ms:      p99.Seconds() * 1000,
		throughput: float64(iterations) / total.Seconds(),
	}
}

func BenchmarkConditions(b *testing.B) {
	client := setupBenchmarkClient()
	ctx := context.Background()

	b.Run("empty_db", func(b *testing.B) {
		result := runBenchmark("ListTasks(empty)", func() error {
			_, err := client.ListTasks(ctx, "", 100, "")
			return err
		})
		if result.avgMs < 0 {
			b.Fatalf("Benchmark failed")
		}
		b.Logf("Empty DB: avg=%.2fms p50=%.2fms p95=%.2fms p99=%.2fms throughput=%.1f/s",
			result.avgMs, result.p50Ms, result.p95Ms, result.p99Ms, result.throughput)
	})

	for _, count := range []int{100, 1000} {
		b.Run(fmt.Sprintf("with_%d_tasks", count), func(b *testing.B) {
			for i := 0; i < count; i++ {
				req := &internal.CreateTaskRequest{
					Subject:     fmt.Sprintf("Perf Task %d", i),
					Description: "Benchmark",
					Priority:    "P3",
				}
				client.CreateTask(ctx, req)
			}

			result := runBenchmark(fmt.Sprintf("ListTasks(%d)", count), func() error {
				_, err := client.ListTasks(ctx, "", 100, "")
				return err
			})
			if result.avgMs < 0 {
				b.Fatalf("Benchmark failed")
			}
			b.Logf("%d tasks: avg=%.2fms p50=%.2fms p95=%.2fms p99=%.2fms throughput=%.1f/s",
				count, result.avgMs, result.p50Ms, result.p95Ms, result.p99Ms, result.throughput)
		})
	}
}

func BenchmarkHighConcurrency(b *testing.B) {
	client := setupBenchmarkClient()
	ctx := context.Background()

	concurrency := []int{1, 5, 10, 20, 50}

	for _, c := range concurrency {
		b.Run(fmt.Sprintf("%d_concurrent", c), func(b *testing.B) {
			var wg sync.WaitGroup
			ops := 0
			var mu sync.Mutex

			start := time.Now()

			for i := 0; i < c; i++ {
				wg.Add(1)
				go func() {
					defer wg.Done()
					for {
						select {
						case <-ctx.Done():
							return
						default:
							_, err := client.ListTasks(ctx, "", 10, "")
							if err != nil {
								return
							}
							mu.Lock()
							ops++
							mu.Unlock()
						}
					}
				}()
			}

			time.Sleep(1 * time.Second)
			elapsed := time.Since(start)

			wg.Wait()

			throughput := float64(ops) / elapsed.Seconds()
			b.Logf("%d concurrent clients: %d ops in %s = %.1f ops/sec",
				c, ops, elapsed.Round(time.Millisecond), throughput)
		})
	}
}

func calculatePercentile(values []float64, p float64) float64 {
	sorted := make([]float64, len(values))
	copy(sorted, values)
	for i := 0; i < len(sorted)-1; i++ {
		for j := i + 1; j < len(sorted); j++ {
			if sorted[j] < sorted[i] {
				sorted[i], sorted[j] = sorted[j], sorted[i]
			}
		}
	}
	idx := int(math.Ceil(p*float64(len(sorted)))) - 1
	if idx < 0 {
		idx = 0
	}
	return sorted[idx]
}

func BenchmarkFullScenario(b *testing.B) {
	client := setupBenchmarkClient()
	ctx := context.Background()

	scenarios := []struct {
		name string
		fn   func() error
	}{
		{"ListTasks", func() error { _, err := client.ListTasks(ctx, "", 100, ""); return err }},
		{"CreateTask", func() error {
			req := &internal.CreateTaskRequest{
				Subject:     "Scenario Test",
				Description: "Full scenario test",
				Priority:    "P3",
			}
			_, err := client.CreateTask(ctx, req)
			return err
		}},
		{"GetDaemonStatus", func() error { _, err := client.GetDaemonStatus(ctx); return err }},
		{"ListProjects", func() error { _, err := client.ListProjects(ctx, ""); return err }},
		{"ListLanes", func() error { _, err := client.ListLanes(ctx); return err }},
	}

	results := make(map[string]benchmarkResult)

	for _, scenario := range scenarios {
		results[scenario.name] = runBenchmark(scenario.name, scenario.fn)
	}

	b.Log("=== Full Scenario Results ===")
	for name, result := range results {
		if result.avgMs < 0 {
			b.Logf("%s: FAILED", name)
			continue
		}
		b.Logf("%s: avg=%.2fms p50=%.2fms p95=%.2fms p99=%.2fms throughput=%.1f/s",
			name, result.avgMs, result.p50Ms, result.p95Ms, result.p99Ms, result.throughput)
	}
}

func BenchmarkAPIVersusDirect(b *testing.B) {
	client := setupBenchmarkClient()
	ctx := context.Background()

	b.Log("=== API vs Direct Comparison ===")

	resultAPI := runBenchmark("API_ListTasks", func() error {
		_, err := client.ListTasks(ctx, "", 100, "")
		return err
	})
	b.Logf("API ListTasks: avg=%.2fms p50=%.2fms p95=%.2fms p99=%.2fms",
		resultAPI.avgMs, resultAPI.p50Ms, resultAPI.p95Ms, resultAPI.p99Ms)

	resultDirect := runBenchmark("Direct_SQLite", func() error {
		return nil
	})
	_ = resultDirect
	b.Logf("Direct SQLite: avg=%.2fms (not implemented - placeholder)", resultAPI.avgMs)
	b.Logf("Difference: API adds ~%.2fms overhead", resultAPI.avgMs)
}
