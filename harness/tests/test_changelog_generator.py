"""Tests for changelog generator."""

from pathlib import Path
from unittest.mock import Mock, patch

from forge_harness.iteration.changelog_generator import (
    ChangelogGenerator,
    ChangelogResult,
    ChangelogSection,
    CommitEntry,
    create_changelog_generator,
)


class TestCommitEntry:
    """Tests for CommitEntry dataclass."""

    def test_commit_entry_creation(self):
        """Test creating a commit entry."""
        commit = CommitEntry(
            hash="abc12345",
            type="feat",
            scope="api",
            description="add user endpoint",
            body="Implements user CRUD operations",
            breaking=False,
        )

        assert commit.hash == "abc12345"
        assert commit.type == "feat"
        assert commit.scope == "api"
        assert commit.description == "add user endpoint"
        assert commit.body == "Implements user CRUD operations"
        assert commit.breaking is False

    def test_commit_entry_defaults(self):
        """Test commit entry with default values."""
        commit = CommitEntry(
            hash="abc12345",
            type="fix",
            scope=None,
            description="fix bug",
        )

        assert commit.body == ""
        assert commit.breaking is False


class TestChangelogSection:
    """Tests for ChangelogSection dataclass."""

    def test_section_creation(self):
        """Test creating a changelog section."""
        section = ChangelogSection(
            title="Features",
            emoji="✨",
        )

        assert section.title == "Features"
        assert section.emoji == "✨"
        assert section.commits == []

    def test_section_with_commits(self):
        """Test section with commits."""
        commit = CommitEntry(
            hash="abc12345",
            type="feat",
            scope=None,
            description="add feature",
        )

        section = ChangelogSection(
            title="Features",
            emoji="✨",
            commits=[commit],
        )

        assert len(section.commits) == 1
        assert section.commits[0] == commit


class TestChangelogResult:
    """Tests for ChangelogResult dataclass."""

    def test_result_success(self):
        """Test successful changelog result."""
        result = ChangelogResult(
            success=True,
            version="v1.2.3",
            content="# v1.2.3\n\nChanges...",
        )

        assert result.success is True
        assert result.version == "v1.2.3"
        assert result.content == "# v1.2.3\n\nChanges..."
        assert result.sections == []
        assert result.error is None

    def test_result_failure(self):
        """Test failed changelog result."""
        result = ChangelogResult(
            success=False,
            error="Failed to parse commits",
        )

        assert result.success is False
        assert result.error == "Failed to parse commits"
        assert result.version is None
        assert result.content == ""


class TestChangelogGenerator:
    """Tests for ChangelogGenerator class."""

    def test_initialization(self):
        """Test generator initialization."""
        generator = ChangelogGenerator()
        assert generator.repo_path == Path.cwd()

    def test_initialization_with_path(self):
        """Test generator initialization with custom path."""
        custom_path = Path("/tmp/test-repo")
        generator = ChangelogGenerator(custom_path)
        assert generator.repo_path == custom_path

    def test_type_mapping(self):
        """Test commit type mappings."""
        assert ChangelogGenerator.TYPE_MAPPING["feat"] == ("Features", "✨")
        assert ChangelogGenerator.TYPE_MAPPING["fix"] == ("Bug Fixes", "🐛")
        assert ChangelogGenerator.TYPE_MAPPING["docs"] == ("Documentation", "📚")
        assert ChangelogGenerator.TYPE_MAPPING["refactor"] == ("Code Refactoring", "♻️")
        assert ChangelogGenerator.TYPE_MAPPING["test"] == ("Tests", "✅")
        assert ChangelogGenerator.TYPE_MAPPING["chore"] == ("Maintenance", "🔧")
        assert ChangelogGenerator.TYPE_MAPPING["perf"] == ("Performance", "⚡")
        assert ChangelogGenerator.TYPE_MAPPING["style"] == ("Styling", "💄")
        assert ChangelogGenerator.TYPE_MAPPING["ci"] == ("CI/CD", "👷")
        assert ChangelogGenerator.TYPE_MAPPING["build"] == ("Build", "🏗️")

    def test_parse_commit_message_feat(self):
        """Test parsing a feature commit."""
        generator = ChangelogGenerator()
        commit = generator._parse_commit_message(
            "abc123456789",
            "feat(api): add user endpoint",
            "Implements user CRUD operations",
        )

        assert commit is not None
        assert commit.hash == "abc12345"
        assert commit.type == "feat"
        assert commit.scope == "api"
        assert commit.description == "add user endpoint"
        assert commit.body == "Implements user CRUD operations"
        assert commit.breaking is False

    def test_parse_commit_message_fix(self):
        """Test parsing a fix commit."""
        generator = ChangelogGenerator()
        commit = generator._parse_commit_message(
            "def456789012",
            "fix: correct validation logic",
            "",
        )

        assert commit is not None
        assert commit.hash == "def45678"
        assert commit.type == "fix"
        assert commit.scope is None
        assert commit.description == "correct validation logic"
        assert commit.breaking is False

    def test_parse_commit_message_breaking_exclamation(self):
        """Test parsing a breaking change with ! suffix."""
        generator = ChangelogGenerator()
        commit = generator._parse_commit_message(
            "abc123456789",
            "feat(api)!: change response format",
            "Changed API response structure",
        )

        assert commit is not None
        assert commit.type == "feat"
        assert commit.breaking is True

    def test_parse_commit_message_breaking_body(self):
        """Test parsing a breaking change in body."""
        generator = ChangelogGenerator()
        commit = generator._parse_commit_message(
            "abc123456789",
            "feat(api): change response format",
            "BREAKING CHANGE: Changed API response structure",
        )

        assert commit is not None
        assert commit.type == "feat"
        assert commit.breaking is True

    def test_parse_commit_message_invalid(self):
        """Test parsing an invalid commit message."""
        generator = ChangelogGenerator()
        commit = generator._parse_commit_message(
            "abc123456789",
            "not a conventional commit",
            "",
        )

        assert commit is None

    def test_determine_version_bump_patch(self):
        """Test version bump for patch (fix only)."""
        generator = ChangelogGenerator()
        commits = [
            CommitEntry(
                hash="abc12345",
                type="fix",
                scope=None,
                description="fix bug",
            )
        ]

        version = generator.determine_version_bump(commits, "v1.2.3")
        assert version == "v1.2.4"

    def test_determine_version_bump_minor(self):
        """Test version bump for minor (feature)."""
        generator = ChangelogGenerator()
        commits = [
            CommitEntry(
                hash="abc12345",
                type="feat",
                scope=None,
                description="add feature",
            )
        ]

        version = generator.determine_version_bump(commits, "v1.2.3")
        assert version == "v1.3.0"

    def test_determine_version_bump_major(self):
        """Test version bump for major (breaking change)."""
        generator = ChangelogGenerator()
        commits = [
            CommitEntry(
                hash="abc12345",
                type="feat",
                scope=None,
                description="breaking change",
                breaking=True,
            )
        ]

        version = generator.determine_version_bump(commits, "v1.2.3")
        assert version == "v2.0.0"

    def test_determine_version_bump_mixed(self):
        """Test version bump with mixed commits (breaking takes precedence)."""
        generator = ChangelogGenerator()
        commits = [
            CommitEntry(
                hash="abc12345",
                type="feat",
                scope=None,
                description="add feature",
            ),
            CommitEntry(
                hash="def45678",
                type="fix",
                scope=None,
                description="fix bug",
            ),
            CommitEntry(
                hash="ghi78901",
                type="feat",
                scope=None,
                description="breaking change",
                breaking=True,
            ),
        ]

        version = generator.determine_version_bump(commits, "v1.2.3")
        assert version == "v2.0.0"

    def test_determine_version_bump_from_zero(self):
        """Test version bump from 0.0.0."""
        generator = ChangelogGenerator()
        commits = [
            CommitEntry(
                hash="abc12345",
                type="feat",
                scope=None,
                description="initial feature",
            )
        ]

        version = generator.determine_version_bump(commits, "0.0.0")
        assert version == "v0.1.0"

    def test_determine_version_bump_without_v_prefix(self):
        """Test version bump with version without v prefix."""
        generator = ChangelogGenerator()
        commits = [
            CommitEntry(
                hash="abc12345",
                type="fix",
                scope=None,
                description="fix bug",
            )
        ]

        version = generator.determine_version_bump(commits, "1.2.3")
        assert version == "v1.2.4"

    @patch("forge_harness.iteration.changelog_generator.subprocess.run")
    def test_get_commits_since_tag(self, mock_run):
        """Test getting commits since a tag."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="abc123456789|feat(api): add endpoint|Body text|||def456789012|fix: bug fix||||",
        )

        generator = ChangelogGenerator()
        commits = generator.get_commits_since_tag("v1.0.0")

        assert len(commits) == 2
        assert commits[0].type == "feat"
        assert commits[0].description == "add endpoint"
        assert commits[1].type == "fix"
        assert commits[1].description == "bug fix"

        mock_run.assert_called_once()
        assert "v1.0.0..HEAD" in mock_run.call_args[0][0]

    @patch("forge_harness.iteration.changelog_generator.subprocess.run")
    def test_get_commits_all(self, mock_run):
        """Test getting all commits when no tag specified."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="abc123456789|feat: feature|Body|||",
        )

        generator = ChangelogGenerator()
        commits = generator.get_commits_since_tag(None)

        assert len(commits) == 1
        mock_run.assert_called_once()
        assert "..HEAD" not in mock_run.call_args[0][0][2]

    @patch("forge_harness.iteration.changelog_generator.subprocess.run")
    def test_get_commits_error(self, mock_run):
        """Test handling git command error."""
        mock_run.return_value = Mock(returncode=1)

        generator = ChangelogGenerator()
        commits = generator.get_commits_since_tag()

        assert commits == []

    @patch("forge_harness.iteration.changelog_generator.subprocess.run")
    def test_get_latest_tag(self, mock_run):
        """Test getting the latest tag."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="v1.2.3\n",
        )

        generator = ChangelogGenerator()
        tag = generator.get_latest_tag()

        assert tag == "v1.2.3"

    @patch("forge_harness.iteration.changelog_generator.subprocess.run")
    def test_get_latest_tag_none(self, mock_run):
        """Test getting latest tag when none exists."""
        mock_run.return_value = Mock(returncode=1)

        generator = ChangelogGenerator()
        tag = generator.get_latest_tag()

        assert tag is None

    @patch("forge_harness.iteration.changelog_generator.subprocess.run")
    def test_generate_changelog(self, mock_run):
        """Test generating changelog."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout=(
                "abc123456789|feat(api): add user endpoint|Body text|||"
                "def456789012|fix: correct validation|Body|||"
                "ghi789012345|docs: update readme|Body|||"
            ),
        )

        generator = ChangelogGenerator()
        result = generator.generate()

        assert result.success is True
        assert result.version == "v0.1.0"
        assert "# v0.1.0" in result.content
        assert "✨ Features" in result.content
        assert "🐛 Bug Fixes" in result.content
        assert "📚 Documentation" in result.content
        assert "add user endpoint" in result.content
        assert "correct validation" in result.content
        assert "update readme" in result.content

    @patch("forge_harness.iteration.changelog_generator.subprocess.run")
    def test_generate_changelog_with_breaking(self, mock_run):
        """Test generating changelog with breaking changes."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="abc123456789|feat(api)!: change response format|BREAKING CHANGE: Changed structure|||",
        )

        generator = ChangelogGenerator()
        result = generator.generate()

        assert result.success is True
        assert result.version == "v1.0.0"
        assert "⚠️ Breaking Changes" in result.content
        assert "change response format" in result.content

    @patch("forge_harness.iteration.changelog_generator.subprocess.run")
    def test_generate_changelog_no_commits(self, mock_run):
        """Test generating changelog with no commits."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="",
        )

        generator = ChangelogGenerator()
        result = generator.generate()

        assert result.success is True
        assert result.content == "No changes to document."
        assert result.error == "No conventional commits found"

    @patch("forge_harness.iteration.changelog_generator.subprocess.run")
    def test_generate_changelog_with_version(self, mock_run):
        """Test generating changelog with explicit version."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="abc123456789|feat: add feature|Body|||",
        )

        generator = ChangelogGenerator()
        result = generator.generate(version="v2.0.0")

        assert result.success is True
        assert result.version == "v2.0.0"
        assert "# v2.0.0" in result.content

    @patch("forge_harness.iteration.changelog_generator.subprocess.run")
    def test_generate_changelog_with_scope(self, mock_run):
        """Test generating changelog with scoped commits."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="abc123456789|feat(api): add endpoint|Body|||",
        )

        generator = ChangelogGenerator()
        result = generator.generate()

        assert result.success is True
        assert "**api:** add endpoint" in result.content

    @patch("forge_harness.iteration.changelog_generator.subprocess.run")
    def test_write_changelog_new_file(self, mock_run, tmp_path):
        """Test writing changelog to new file."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="abc123456789|feat: add feature|Body|||",
        )

        output_path = tmp_path / "CHANGELOG.md"
        generator = ChangelogGenerator(tmp_path)
        result = generator.write_changelog(output_path, prepend=False)

        assert result.success is True
        assert output_path.exists()
        content = output_path.read_text()
        assert "# v0.1.0" in content
        assert "✨ Features" in content

    @patch("forge_harness.iteration.changelog_generator.subprocess.run")
    def test_write_changelog_prepend(self, mock_run, tmp_path):
        """Test prepending changelog to existing file."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="abc123456789|feat: new feature|Body|||",
        )

        output_path = tmp_path / "CHANGELOG.md"
        output_path.write_text("# v1.0.0\n\nOld content")

        generator = ChangelogGenerator(tmp_path)
        result = generator.write_changelog(output_path, prepend=True)

        assert result.success is True
        content = output_path.read_text()
        assert content.index("# v0.1.0") < content.index("# v1.0.0")
        assert "---" in content
        assert "Old content" in content

    @patch("forge_harness.iteration.changelog_generator.subprocess.run")
    def test_write_changelog_default_path(self, mock_run, tmp_path):
        """Test writing changelog to default path."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="abc123456789|feat: add feature|Body|||",
        )

        generator = ChangelogGenerator(tmp_path)
        result = generator.write_changelog()

        assert result.success is True
        default_path = tmp_path / "CHANGELOG.md"
        assert default_path.exists()


class TestFactoryFunction:
    """Tests for factory function."""

    def test_create_changelog_generator(self):
        """Test factory function creates generator."""
        generator = create_changelog_generator()
        assert isinstance(generator, ChangelogGenerator)
        assert generator.repo_path == Path.cwd()

    def test_create_changelog_generator_with_path(self):
        """Test factory function with custom path."""
        custom_path = Path("/tmp/test")
        generator = create_changelog_generator(custom_path)
        assert isinstance(generator, ChangelogGenerator)
        assert generator.repo_path == custom_path
