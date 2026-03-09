#!/usr/bin/env python3
"""Run the tmux/git state migration as a standalone script.

This script populates the database with tmux session and git state data
without requiring Alembic's op context.
"""
import json
import subprocess
from datetime import datetime

from sqlalchemy import create_engine, text


def parse_tmux_sessions():
    """Parse tmux sessions."""
    sessions = []
    try:
        result = subprocess.run(
            ['tmux', 'list-sessions', '-F', '#{session_name} #{session_windows} #{session_created} #{?session_attached,attached,detached}'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split()
                    if len(parts) >= 4:
                        session_name = parts[0]
                        windows = int(parts[1])
                        created_ts = int(parts[2])
                        attached = parts[3] == 'attached'
                        created = datetime.fromtimestamp(created_ts)

                        if session_name == 'forge':
                            role = 'orchestrator'
                            name = 'Main Orchestrator'
                            domain = None
                        elif session_name == 'dot':
                            role = 'orchestrator'
                            name = 'Dot Agent'
                            domain = None
                        elif session_name.startswith('forge-'):
                            name_parts = session_name.split('-')
                            domain = '-'.join(name_parts[1:]) if len(name_parts) > 1 else None
                            role = 'domain_orchestrator'
                            name = f"Domain Orchestrator ({domain})" if domain else session_name
                        else:
                            role = 'agent'
                            name = session_name
                            domain = None

                        sessions.append({
                            'session_name': session_name,
                            'name': name,
                            'role': role,
                            'domain': domain,
                            'project': None,
                            'windows': windows,
                            'created': created,
                            'attached': attached,
                            'tmux_session': session_name,
                        })
    except Exception as e:
        print(f"Warning: Could not parse tmux sessions: {e}")
    return sessions


def parse_git_state():
    """Parse git state."""
    modified = []
    untracked = []
    try:
        # Get branch
        result = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], capture_output=True, text=True, timeout=5)
        branch = result.stdout.strip() if result.returncode == 0 else 'unknown'

        # Get status
        result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if line:
                    status = line[:2]
                    filename = line[3:]
                    if status.startswith('??'):
                        untracked.append(filename)
                    else:
                        modified.append(filename)

        # Get last commit
        result = subprocess.run(['git', 'log', '-1', '--pretty=format:%H|%s|%an|%ai'], capture_output=True, text=True, timeout=5)
        last_commit = {}
        if result.returncode == 0:
            parts = result.stdout.strip().split('|')
            if len(parts) >= 4:
                last_commit = {'hash': parts[0], 'message': parts[1], 'author': parts[2], 'timestamp': parts[3]}

        # Get worktrees
        result = subprocess.run(['git', 'worktree', 'list', '--porcelain'], capture_output=True, text=True, timeout=5)
        worktrees = []
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if line.startswith('worktree '):
                    path = line.replace('worktree ', '').strip()
                    worktrees.append(path)

        return [{
            'branch': branch,
            'worktrees': worktrees,
            'modified_files': modified,
            'untracked_files': untracked,
            'last_commit': last_commit,
        }]
    except Exception as e:
        print(f"Warning: Could not parse git state: {e}")
        return [{'branch': 'unknown', 'worktrees': [], 'modified_files': [], 'untracked_files': [], 'last_commit': {}}]


def run_migration():
    """Run the full migration."""
    print("Starting tmux/git state migration...")

    # Connect to database
    engine = create_engine("sqlite:///./forge_harness.db")

    with engine.connect() as conn:
        # Step 1: Parse and insert tmux sessions as agents
        tmux_sessions = parse_tmux_sessions()
        print(f"Found {len(tmux_sessions)} tmux sessions")

        for session in tmux_sessions:
            session_id = f"agent-{session['session_name']}"
            status = 'working' if session['attached'] else 'idle'

            conn.execute(text("""
                INSERT OR REPLACE INTO cc_agents (
                    id, name, role, status, domain, project,
                    tmux_session, last_activity, created_at, updated_at
                ) VALUES (
                    :id, :name, :role, :status, :domain, :project,
                    :tmux_session, :last_activity, :created_at, :created_at
                )
            """), {
                'id': session_id,
                'name': session['name'],
                'role': session['role'],
                'status': status,
                'domain': session['domain'],
                'project': session['project'],
                'tmux_session': session['session_name'],
                'last_activity': session['created'].isoformat(),
                'created_at': datetime.utcnow().isoformat(),
            })

            session_record_id = f"session-{session['session_name']}-{datetime.utcnow().strftime('%Y%m%d')}"
            conn.execute(text("""
                INSERT OR IGNORE INTO sessions (
                    id, agent_id, start_time, context_summary,
                    created_at, updated_at
                ) VALUES (
                    :id, :agent_id, :start_time, :context_summary,
                    :created_at, :created_at
                )
            """), {
                'id': session_record_id,
                'agent_id': session_id,
                'start_time': session['created'].isoformat(),
                'context_summary': f"tmux session with {session['windows']} windows",
                'created_at': datetime.utcnow().isoformat(),
            })

            print(f"  - Created agent: {session['name']} ({status})")

        # Step 2: Parse git state
        git_state = parse_git_state()
        print(f"Git state: branch={git_state[0]['branch']}")

        if git_state[0]['modified_files'] or git_state[0]['untracked_files']:
            context_parts = []
            if git_state[0]['modified_files']:
                context_parts.append(f"{len(git_state[0]['modified_files'])} modified files")
            if git_state[0]['untracked_files']:
                context_parts.append(f"{len(git_state[0]['untracked_files'])} untracked files")

            conn.execute(text("""
                INSERT OR REPLACE INTO sessions (
                    id, agent_id, start_time, context_summary,
                    files_modified, created_at, updated_at
                ) VALUES (
                    :id, :agent_id, :start_time, :context_summary,
                    :files_modified, :created_at, :created_at
                )
            """), {
                'id': f"session-git-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                'agent_id': 'agent-forge',
                'start_time': datetime.utcnow().isoformat(),
                'context_summary': f"Git work on branch '{git_state[0]['branch']}' with {', '.join(context_parts)}",
                'files_modified': json.dumps(git_state[0]['modified_files']),
                'created_at': datetime.utcnow().isoformat(),
            })
            print(f"  - Created git session for branch: {git_state[0]['branch']}")

        # Step 3: Record migration in sync_log
        migration_data = {
            'tmux_sessions_migrated': len(tmux_sessions),
            'git_branch': git_state[0]['branch'],
            'migration_time': datetime.utcnow().isoformat(),
        }

        conn.execute(text("""
            INSERT INTO sync_log (
                table_name, record_id, operation, node_id,
                new_value, timestamp
            ) VALUES (
                :table_name, :record_id, :operation, :node_id,
                :new_value, :timestamp
            )
        """), {
            'table_name': 'cc_agents',
            'record_id': 'tmux_git_migration',
            'operation': 'INSERT',
            'node_id': 'local',
            'new_value': json.dumps(migration_data),
            'timestamp': datetime.utcnow().isoformat(),
        })

        conn.commit()
        print(f"Migration complete! Migrated {len(tmux_sessions)} agents.")


if __name__ == "__main__":
    run_migration()
