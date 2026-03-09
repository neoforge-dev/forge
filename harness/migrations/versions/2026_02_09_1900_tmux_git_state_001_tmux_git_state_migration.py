"""Migrate tmux and git state to database

Revision ID: tmux_git_state_001
Revises: cc_initial_001
Create Date: 2026-02-09 19:00:00

Migrates:
- tmux sessions → cc_agents table
- git worktrees/branches → sessions table
- Active work → cc_tasks table (if available)

Based on:
- tmux list-sessions output
- git branch and worktree state
- Active tmux windows as agent context
"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = 'tmux_git_state_001'
down_revision: str = 'cc_initial_001'
branch_labels: list[str] | None = None
depends_on: list[str] | None = None


def parse_tmux_sessions() -> list[dict]:
    """Parse tmux sessions and extract agent information.

    Returns list of dicts with:
    - session_name: The tmux session name (e.g., "forge", "forge-brandfocus-ai")
    - windows: Number of windows in session
    - created: Creation time
    - attached: Whether session is currently attached
    """
    import subprocess

    sessions = []

    try:
        # Get tmux sessions info
        result = subprocess.run(
            ['tmux', 'list-sessions', '-F', '#{session_name} #{session_windows} #{session_created} #{?session_attached,attached,detached}'],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split()
                    if len(parts) >= 4:
                        session_name = parts[0]
                        windows = int(parts[1])
                        # session_created is a Unix timestamp
                        created_ts = int(parts[2])
                        attached = parts[3] == 'attached'
                        created = datetime.fromtimestamp(created_ts)

                        # Parse domain/project from session name
                        name_parts = session_name.split('-')
                        domain = None
                        project = None

                        if session_name == 'forge':
                            role = 'orchestrator'
                            name = 'Main Orchestrator'
                        elif session_name == 'dot':
                            role = 'orchestrator'
                            name = 'Dot Agent'
                        elif session_name.startswith('forge-'):
                            # e.g., "forge-brandfocus-ai"
                            domain = '-'.join(name_parts[1:]) if len(name_parts) > 1 else None
                            role = 'domain_orchestrator'
                            name = f"Domain Orchestrator ({domain})" if domain else session_name
                        else:
                            role = 'agent'
                            name = session_name

                        sessions.append({
                            'session_name': session_name,
                            'name': name,
                            'role': role,
                            'domain': domain,
                            'project': project,
                            'windows': windows,
                            'created': created,
                            'attached': attached,
                            'tmux_session': session_name,
                        })
    except Exception as e:
        print(f"Warning: Could not parse tmux sessions: {e}")
        # Return empty list if tmux not available
        pass

    return sessions


def parse_git_state() -> list[dict]:
    """Parse git state to identify active work.

    Returns list of dicts with:
    - branch: Current branch name
    - worktree: Whether this is a worktree
    - worktree_path: Path if worktree
    - last_commit: Last commit message
    - modified_files: List of modified files
    - untracked_files: List of untracked files
    """
    import subprocess

    work_items = []
    modified = []
    untracked = []

    try:
        # Get current branch
        result = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True,
            text=True,
            timeout=5
        )
        branch = result.stdout.strip() if result.returncode == 0 else 'unknown'

        # Get git status
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            status_lines = result.stdout.strip().split('\n')

            for line in status_lines:
                if line:
                    status = line[:2]
                    filename = line[3:]
                    if status.startswith('??'):
                        untracked.append(filename)
                    else:
                        modified.append(filename)

        # Get last commit
        result = subprocess.run(
            ['git', 'log', '-1', '--pretty=format:%H|%s|%an|%ai'],
            capture_output=True,
            text=True,
            timeout=5
        )

        last_commit = {}
        if result.returncode == 0:
            parts = result.stdout.strip().split('|')
            if len(parts) >= 4:
                last_commit = {
                    'hash': parts[0],
                    'message': parts[1],
                    'author': parts[2],
                    'timestamp': parts[3],
                }

        # Check for worktrees
        result = subprocess.run(
            ['git', 'worktree', 'list', '--porcelain'],
            capture_output=True,
            text=True,
            timeout=5
        )

        worktrees = []
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if line.startswith('worktree '):
                    path = line.replace('worktree ', '').strip()
                    worktrees.append(path)

        work_items.append({
            'branch': branch,
            'worktrees': worktrees,
            'modified_files': modified,
            'untracked_files': untracked,
            'last_commit': last_commit,
        })

    except Exception as e:
        print(f"Warning: Could not parse git state: {e}")
        # Return minimal state
        work_items.append({
            'branch': 'unknown',
            'worktrees': [],
            'modified_files': [],
            'untracked_files': [],
            'last_commit': {},
        })

    return work_items


def extract_active_tasks() -> list[dict]:
    """Extract active tasks from filesystem state.

    Looks for:
    - docs/PROMPT.md - Current task context
    - docs/PLAN.md - Sprint/iteration tasks
    - Task dispatch files
    """
    import os

    tasks = []

    # Look for PROMPT.md to extract current context
    prompt_paths = [
        '/Users/bogdan/work/FORGE/docs/PROMPT.md',
        '/Users/bogdan/work/FORGE/docs/PLAN.md',
    ]

    for prompt_path in prompt_paths:
        if os.path.exists(prompt_path):
            try:
                with open(prompt_path) as f:
                    content = f.read()

                # Try to extract task information from context
                # This is a simple heuristic - real extraction would use more sophisticated parsing
                if 'Current' in content or 'Working on' in content:
                    task_id = os.path.basename(prompt_path).replace('.md', '').lower()
                    tasks.append({
                        'id': f'task-{prompt_path.split("/")[-2]}-{task_id}',
                        'source': prompt_path,
                        'title': f"Continue work from {os.path.basename(prompt_path)}",
                        'description': 'Extracted from current session context',
                        'priority': 5,
                        'status': 'in_progress',
                        'context': content[:500],  # First 500 chars
                    })
            except Exception as e:
                print(f"Warning: Could not read {prompt_path}: {e}")

    return tasks


def upgrade() -> None:
    """Migrate tmux and git state to database.

    This migration:
    1. Parses tmux sessions and populates cc_agents
    2. Parses git state and populates sessions
    3. Extracts active tasks from context files
    4. Records the migration in sync_log
    """
    import json

    print("Starting tmux/git state migration...")

    # Use raw SQL since we're in Alembic context
    connection = op.get_bind()

    # Step 1: Parse and insert tmux sessions as agents
    tmux_sessions = parse_tmux_sessions()
    print(f"Found {len(tmux_sessions)} tmux sessions")

    for session in tmux_sessions:
        session_id = f"agent-{session['session_name']}"

        # Determine status
        if session['attached']:
            status = 'working'
        else:
            status = 'idle'

        # Insert or update agent
        connection.execute(sa.text("""
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

        # Create a session record for this agent
        session_record_id = f"session-{session['session_name']}-{datetime.utcnow().strftime('%Y%m%d')}"
        connection.execute(sa.text("""
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
            'context_summary': f" tmux session with {session['windows']} windows",
            'created_at': datetime.utcnow().isoformat(),
        })

        print(f"  - Created agent: {session['name']} ({status})")

    # Step 2: Parse git state and create session records
    git_state = parse_git_state()
    print(f"Git state: branch={git_state[0]['branch']}")

    if git_state[0]['modified_files'] or git_state[0]['untracked_files']:
        # Create a session for the main repo state
        main_agent_id = "agent-forge"  # Main orchestrator

        context_parts = []
        if git_state[0]['modified_files']:
            context_parts.append(f"{len(git_state[0]['modified_files'])} modified files")
        if git_state[0]['untracked_files']:
            context_parts.append(f"{len(git_state[0]['untracked_files'])} untracked files")

        connection.execute(sa.text("""
            INSERT OR REPLACE INTO sessions (
                id, agent_id, start_time, context_summary,
                files_modified, created_at, updated_at
            ) VALUES (
                :id, :agent_id, :start_time, :context_summary,
                :files_modified, :created_at, :created_at
            )
        """), {
            'id': f"session-git-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            'agent_id': main_agent_id,
            'start_time': datetime.utcnow().isoformat(),
            'context_summary': f"Git work on branch '{git_state[0]['branch']}' with {', '.join(context_parts)}",
            'files_modified': json.dumps(git_state[0]['modified_files']),
            'created_at': datetime.utcnow().isoformat(),
        })

        print(f"  - Created git session for branch: {git_state[0]['branch']}")

    # Step 3: Extract and insert active tasks
    active_tasks = extract_active_tasks()
    print(f"Found {len(active_tasks)} active task contexts")

    for task in active_tasks:
        connection.execute(sa.text("""
            INSERT OR IGNORE INTO cc_tasks (
                id, title, description, status, priority,
                created_at, updated_at
            ) VALUES (
                :id, :title, :description, :status, :priority,
                :created_at, :created_at
            )
        """), {
            'id': task['id'],
            'title': task['title'],
            'description': task.get('description', ''),
            'status': task.get('status', 'pending'),
            'priority': task.get('priority', 5),
            'created_at': datetime.utcnow().isoformat(),
        })

        # Assign to main orchestrator
        if task.get('status') == 'in_progress':
            connection.execute(sa.text("""
                UPDATE cc_tasks
                SET assigned_to = :agent_id, status = 'assigned'
                WHERE id = :task_id
            """), {
                'agent_id': 'agent-forge',
                'task_id': task['id'],
            })

        print(f"  - Created task: {task['title'][:50]}...")

    # Step 4: Record migration in sync_log
    migration_data = {
        'tmux_sessions_migrated': len(tmux_sessions),
        'git_branch': git_state[0]['branch'],
        'tasks_migrated': len(active_tasks),
        'migration_time': datetime.utcnow().isoformat(),
    }

    connection.execute(sa.text("""
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

    connection.commit()
    print(f"Migration complete! Migrated {len(tmux_sessions)} agents, {len(active_tasks)} tasks.")


def downgrade() -> None:
    """Rollback tmux/git state migration.

    Removes migrated data from cc_agents, sessions, and cc_tasks tables.
    Records the rollback in sync_log.
    """
    import json

    print("Rolling back tmux/git state migration...")

    connection = op.get_bind()

    # Step 1: Get migration record to capture data for rollback
    migration_record = connection.execute(sa.text("""
        SELECT new_value FROM sync_log
        WHERE record_id = 'tmux_git_migration'
        ORDER BY timestamp DESC
        LIMIT 1
    """)).fetchone()

    migration_data = {}
    if migration_record:
        try:
            migration_data = json.loads(migration_record[0])
        except Exception:
            pass

    # Step 2: Delete migrated agents (those created by this migration)
    # We identify them by their tmux_session pattern
    connection.execute(sa.text("""
        DELETE FROM cc_agents
        WHERE tmux_session IS NOT NULL
        AND id LIKE 'agent-%'
    """))

    # Step 3: Delete sessions created today (approximate rollback)
    connection.execute(sa.text("""
        DELETE FROM sessions
        WHERE id LIKE 'session-git-%'
        OR id LIKE 'session-tmux-%'
    """))

    # Step 4: Delete tasks migrated from PROMPT/PLAN
    connection.execute(sa.text("""
        DELETE FROM cc_tasks
        WHERE description LIKE '%Extracted from current session context%'
        OR source LIKE '%PROMPT.md%'
    """))

    # Step 5: Record rollback in sync_log
    connection.execute(sa.text("""
        INSERT INTO sync_log (
            table_name, record_id, operation, node_id,
            old_value, new_value, timestamp
        ) VALUES (
            :table_name, :record_id, :operation, :node_id,
            :old_value, :new_value, :timestamp
        )
    """), {
        'table_name': 'cc_agents',
        'record_id': 'tmux_git_migration_rollback',
        'operation': 'DELETE',
        'node_id': 'local',
        'old_value': json.dumps(migration_data),
        'new_value': None,
        'timestamp': datetime.utcnow().isoformat(),
    })

    connection.commit()
    print("Rollback complete! Removed migrated tmux/git state data.")
