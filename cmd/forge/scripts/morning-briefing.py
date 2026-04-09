#!/usr/bin/env python3
"""
Morning Briefing Script — Scan overnight results, format 5-min summary for human review.
"""

import subprocess
import json
from datetime import datetime, timedelta
from pathlib import Path

def run_forge(args):
    """Run forge CLI and return parsed output."""
    cmd = ["./forge"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, cwd="/Users/bogdan/work/forge-mono/cmd/forge")
    return result.stdout, result.stderr, result.returncode

def get_recent_tasks(hours=12):
    """Get tasks completed in last N hours."""
    stdout, _, _ = run_forge(["task", "list", "--format", "json"])
    try:
        tasks = json.loads(stdout) if stdout.strip() else []
    except:
        tasks = []
    
    # Filter for recent completions
    cutoff = datetime.now() - timedelta(hours=hours)
    recent = []
    for task in tasks:
        updated = task.get("updated", "")
        status = task.get("status", "")
        if status in ["completed", "failed"]:
            try:
                updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                if updated_dt > cutoff:
                    recent.append(task)
            except:
                pass
    return recent

def get_heartbeat_status():
    """Get heartbeat status."""
    stdout, _, _ = run_forge(["heartbeat", "status"])
    return stdout

def get_fleet_status():
    """Get fleet status."""
    stdout, _, _ = run_forge(["status"])
    return stdout

def main():
    print("=" * 60)
    print(f"MORNING BRIEFING — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    # Fleet status
    print("\n🤖 FLEET STATUS")
    print("-" * 40)
    fleet_status, _, _ = run_forge(["status"])
    # Extract key lines
    for line in fleet_status.split('\n')[:15]:
        if line.strip():
            print(line)
    
    # Recent task activity
    print("\n📋 OVERNIGHT TASK ACTIVITY (last 12h)")
    print("-" * 40)
    recent = get_recent_tasks(12)
    completed = [t for t in recent if t.get("status") == "completed"]
    failed = [t for t in recent if t.get("status") == "failed"]
    
    print(f"Completed: {len(completed)}")
    print(f"Failed: {len(failed)}")
    
    if completed:
        print("\n✅ Recent completions:")
        for t in completed[:5]:
            print(f"  • {t.get('title', 'Unknown')[:50]}...")
    
    if failed:
        print("\n❌ Recent failures:")
        for t in failed[:3]:
            print(f"  • {t.get('title', 'Unknown')[:50]}...")
    
    # Queue depth
    print("\n📊 QUEUE STATUS")
    print("-" * 40)
    queue_stdout, _, _ = run_forge(["queue", "status"])
    print(queue_stdout[:500] if len(queue_stdout) > 500 else queue_stdout)
    
    # Human gates
    print("\n🔓 HUMAN GATES (Blocking)")
    print("-" * 40)
    print("• GATE-VC: Railway deploy + API keys (15 min)")
    print("• GATE-C: App Store Connect setup (30 min)")
    print("• GATE-CONTENT: Post 5 LinkedIn posts")
    
    # Recommendations
    print("\n💡 RECOMMENDED ACTIONS")
    print("-" * 40)
    if len(failed) > 0:
        print("• Review failed tasks and reassign")
    if len(completed) > 5:
        print("• High activity overnight — review outputs for quality")
    print("• Run 'forge task list' for full queue")
    print("• Check .forge/heartbeat/results/ for detailed reports")
    
    print("\n" + "=" * 60)
    print("Briefing complete. Est. read time: 2-3 min")
    print("=" * 60)

if __name__ == "__main__":
    main()
