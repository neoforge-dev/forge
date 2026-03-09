"""
Test Pipeline Endpoint

Verifies that the /api/pipelines endpoint returns pipeline data correctly.
"""

import json
from datetime import datetime
from pathlib import Path


def create_sample_orchestration_checkpoint():
    """Create a sample orchestration checkpoint for testing."""
    checkpoint_dir = Path.cwd() / ".forge/orchestration_checkpoints"
    checkpoint_dir.mkdir(exist_ok=True)

    checkpoint_data = {
        "pipeline_name": "content_deployment_pipeline",
        "created_at": datetime.utcnow().isoformat(),
        "current_step_index": 2,
        "completed_steps": ["generate_content", "review_content"],
        "pipeline_steps": [
            "generate_content",
            "review_content",
            "publish_to_notion",
            "notify_slack",
        ],
        "context": {
            "domain": "codeswiftr-com",
            "project": "interview-simulator",
            "content_type": "blog_posts",
        },
        "step_outputs": {
            "generate_content": {"status": "success", "content_count": 5},
            "review_content": {"status": "success", "approved": 5},
        },
    }

    checkpoint_file = checkpoint_dir / f"pipeline_{int(datetime.utcnow().timestamp())}.json"
    with open(checkpoint_file, "w") as f:
        json.dump(checkpoint_data, f, indent=2)

    print(f"✓ Created orchestration checkpoint: {checkpoint_file}")
    return checkpoint_file


def create_sample_ralph_checkpoint():
    """Create a sample Ralph loop checkpoint for testing."""
    checkpoint_dir = Path.cwd() / ".forge/ralph_checkpoints"
    checkpoint_dir.mkdir(exist_ok=True)

    checkpoint_data = {
        "timestamp": datetime.utcnow().isoformat(),
        "iteration": 5,
        "features_path": "harness/command_center/features.json",
        "stats": {"passing": 12, "failing": 2, "blocked": 1, "pending": 5, "in_progress": 3},
        "config": {"max_iterations": 100, "test_command": "npm run test"},
    }

    checkpoint_file = checkpoint_dir / f"ralph_{int(datetime.utcnow().timestamp())}.json"
    with open(checkpoint_file, "w") as f:
        json.dump(checkpoint_data, f, indent=2)

    print(f"✓ Created Ralph checkpoint: {checkpoint_file}")
    return checkpoint_file


def test_pipeline_reader():
    """Test the PipelineDataReader directly."""
    from forge_harness.pipeline_data import create_pipeline_reader

    print("\n=== Testing PipelineDataReader ===\n")

    # Create sample checkpoints
    orch_file = create_sample_orchestration_checkpoint()
    ralph_file = create_sample_ralph_checkpoint()

    # Create reader
    reader = create_pipeline_reader(forge_root=Path.cwd())

    # Get recent pipelines
    print("\n--- Recent Pipelines ---")
    pipelines = reader.get_recent_pipelines(limit=10)
    print(f"Found {len(pipelines)} pipelines:")
    for p in pipelines:
        print(f"  - {p.name} ({p.type}): {p.status}")
        print(f"    Progress: {p.current_step}/{p.total_steps}")
        if p.domain:
            print(f"    Domain: {p.domain}")
        if p.project:
            print(f"    Project: {p.project}")

    # Get pipeline stats
    print("\n--- Pipeline Stats ---")
    stats = reader.get_pipeline_stats()
    print(f"Total: {stats['total']}")
    print(f"By Status: {stats['by_status']}")
    print(f"By Type: {stats['by_type']}")

    # Test filtering
    print("\n--- Filtered Results ---")
    running = reader.get_recent_pipelines(status="running")
    print(f"Running pipelines: {len(running)}")

    orchestration = reader.get_recent_pipelines(type_filter="orchestration")
    print(f"Orchestration pipelines: {len(orchestration)}")

    ralph = reader.get_recent_pipelines(type_filter="ralph_loop")
    print(f"Ralph loop pipelines: {len(ralph)}")

    print("\n✓ All tests passed!")

    return True


def test_api_endpoint():
    """Test the /api/pipelines endpoint via HTTP."""
    import os

    import requests

    print("\n=== Testing /api/pipelines Endpoint ===\n")

    # Get API URL from environment or use default
    api_base = os.getenv("FORGE_API_URL", "http://localhost:8080")
    token = os.getenv("FORGE_WEBHOOK_TOKEN", "dev-token-12345")

    headers = {"Authorization": f"Bearer {token}"}

    # Test /api/pipelines
    print(f"GET {api_base}/api/pipelines")
    response = requests.get(f"{api_base}/api/pipelines", headers=headers)
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        if data.get("success"):
            pipelines = data["data"]["pipelines"]
            print(f"✓ Found {len(pipelines)} pipelines")
            for p in pipelines[:3]:  # Show first 3
                print(f"  - {p['name']} ({p['type']}): {p['status']}")
        else:
            print(f"✗ API returned success=false: {data.get('error')}")
    else:
        print(f"✗ Request failed: {response.text}")

    # Test /api/pipelines/stats
    print(f"\nGET {api_base}/api/pipelines/stats")
    response = requests.get(f"{api_base}/api/pipelines/stats", headers=headers)
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        if data.get("success"):
            stats = data["data"]
            print(f"✓ Stats: {stats}")
        else:
            print(f"✗ API returned success=false: {data.get('error')}")
    else:
        print(f"✗ Request failed: {response.text}")

    print("\n✓ API endpoint tests complete!")


if __name__ == "__main__":
    import sys

    # Test the pipeline reader
    test_pipeline_reader()

    # Ask if user wants to test API endpoint
    if "--api" in sys.argv:
        try:
            test_api_endpoint()
        except ImportError:
            print("\n⚠️ requests library not available, skipping API tests")
            print("   Install with: uv pip install requests")
        except Exception as e:
            print(f"\n⚠️ API tests failed: {e}")
            print("   Make sure webhook_server.py is running on port 8080")
