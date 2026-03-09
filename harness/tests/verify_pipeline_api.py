"""
Verify Pipeline API

Quick verification that /api/pipelines endpoint returns data correctly.
"""

from pathlib import Path


def verify_pipeline_data():
    """Verify pipeline data can be read from checkpoint files."""
    from forge_harness.pipeline_data import create_pipeline_reader

    print("=== Verifying Pipeline Data ===\n")

    reader = create_pipeline_reader(forge_root=Path("/Users/bogdan/work/FORGE"))

    # Get all pipelines
    pipelines = reader.get_recent_pipelines(limit=50)
    print(f"Total pipelines found: {len(pipelines)}\n")

    if len(pipelines) == 0:
        print("⚠️ No pipelines found. Expected at least 4 sample checkpoints.")
        return False

    # Display pipelines
    print("Pipeline Details:")
    print("-" * 80)
    for i, p in enumerate(pipelines, 1):
        print(f"\n{i}. {p.name}")
        print(f"   Type: {p.type}")
        print(f"   Status: {p.status}")
        print(f"   Progress: {p.current_step}/{p.total_steps}")
        if p.domain:
            print(f"   Domain: {p.domain}")
        if p.project:
            print(f"   Project: {p.project}")
        print(f"   Updated: {p.updated_at}")

    # Get stats
    print("\n" + "=" * 80)
    stats = reader.get_pipeline_stats()
    print("\nPipeline Statistics:")
    print(f"  Total: {stats['total']}")
    print(f"  By Status: {stats['by_status']}")
    print(f"  By Type: {stats['by_type']}")

    # Verify data structure
    print("\n" + "=" * 80)
    print("\nData Structure Verification:")

    sample = pipelines[0]
    required_fields = [
        "id",
        "name",
        "type",
        "status",
        "current_step",
        "total_steps",
        "started_at",
        "updated_at",
        "checkpoint_path",
        "metadata",
    ]

    all_present = True
    for field in required_fields:
        has_field = hasattr(sample, field)
        status = "✓" if has_field else "✗"
        print(f"  {status} {field}")
        if not has_field:
            all_present = False

    if all_present:
        print("\n✓ All required fields present")
    else:
        print("\n✗ Missing required fields")
        return False

    # Test to_dict() method
    print("\n" + "=" * 80)
    print("\nTesting to_dict() serialization:")
    try:
        sample_dict = sample.to_dict()
        print(f"  ✓ Serialized to dict with {len(sample_dict)} fields")

        # Verify JSON serializable
        import json

        json_str = json.dumps(sample_dict, indent=2)
        print(f"  ✓ JSON serializable ({len(json_str)} bytes)")
    except Exception as e:
        print(f"  ✗ Serialization failed: {e}")
        return False

    print("\n" + "=" * 80)
    print("\n✓ All verifications passed!")
    print(f"\nThe /api/pipelines endpoint should return {len(pipelines)} pipelines.")
    return True


if __name__ == "__main__":
    success = verify_pipeline_data()
    exit(0 if success else 1)
