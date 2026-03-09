import unittest

from scripts.check_active_surface_policy import scan_text


class ActiveSurfacePolicyTest(unittest.TestCase):
    # --- banned pattern: deprecated forge-harness CLI invocation (ADR-040) ---

    def test_flags_forge_harness_invocation(self) -> None:
        failures = scan_text("Run: forge-harness dispatch to send a task")
        self.assertTrue(
            any("forge-harness" in f for f in failures),
            f"Expected forge-harness violation, got: {failures}",
        )

    # --- banned pattern: deprecated cli_v2 reference (ADR-040) ---

    def test_flags_cli_v2(self) -> None:
        failures = scan_text("Use cli_v2 for all fleet operations.")
        self.assertTrue(
            any("cli_v2" in f for f in failures),
            f"Expected cli_v2 violation, got: {failures}",
        )

    # --- banned pattern: raw tmux dispatch ---

    def test_flags_tmux_dispatch(self) -> None:
        failures = scan_text("tmux send-keys -t forge:kimi 'dispatch work'")
        self.assertTrue(
            any("tmux" in f for f in failures),
            f"Expected tmux dispatch violation, got: {failures}",
        )

    # --- banned pattern: deleted scripts ---

    def test_flags_deleted_script_forge_sprint(self) -> None:
        failures = scan_text("Start the fleet with forge-sprint.")
        self.assertTrue(
            any("forge-sprint" in f for f in failures),
            f"Expected forge-sprint violation, got: {failures}",
        )

    def test_flags_deleted_script_forge_fleet(self) -> None:
        failures = scan_text("See forge-fleet for fleet management.")
        self.assertTrue(
            any("forge-fleet" in f for f in failures),
            f"Expected forge-fleet violation, got: {failures}",
        )

    def test_flags_deleted_script_forge_start(self) -> None:
        failures = scan_text("Use forge-start to boot agents.")
        self.assertTrue(
            any("forge-start" in f for f in failures),
            f"Expected forge-start violation, got: {failures}",
        )

    # --- banned pattern: nonexistent Go CLI command ---

    def test_flags_forge_trinity(self) -> None:
        failures = scan_text("Run forge trinity to launch all nodes.")
        self.assertTrue(
            any("trinity" in f for f in failures),
            f"Expected forge trinity violation, got: {failures}",
        )

    # --- banned pattern: old GitHub slugs (ADR-032) ---

    def test_flags_old_github_slug(self) -> None:
        failures = scan_text("Fork at https://github.com/bogdan-velscu/FORGE")
        self.assertTrue(
            any("ADR-032" in f for f in failures),
            f"Expected bogdan-velscu/FORGE violation, got: {failures}",
        )

    def test_flags_old_github_org(self) -> None:
        failures = scan_text("See openclaw/FORGE for source.")
        self.assertTrue(
            any("ADR-032" in f for f in failures),
            f"Expected openclaw/FORGE violation, got: {failures}",
        )

    # --- banned pattern: absolute private home paths ---

    def test_flags_private_home_bogdan(self) -> None:
        failures = scan_text("Clone to /home/bogdan/FORGE")
        self.assertTrue(
            any("absolute private home path" in f for f in failures),
            f"Expected /home/bogdan/ violation, got: {failures}",
        )

    def test_flags_private_home_openclaw(self) -> None:
        # /home/openclaw/ also matches openclaw/FORGE — at least one must be "absolute private home path"
        failures = scan_text("Repo lives at /home/openclaw/project.")
        self.assertTrue(
            any("absolute private home path" in f for f in failures),
            f"Expected /home/openclaw/ violation, got: {failures}",
        )

    def test_flags_hardcoded_forge_root(self) -> None:
        failures = scan_text("export FORGE_ROOT=$HOME/work/FORGE")
        self.assertTrue(
            any("hardcoded FORGE_ROOT" in f for f in failures),
            f"Expected $HOME/work/FORGE violation, got: {failures}",
        )

    # --- banned pattern: direct private URL ---

    def test_flags_private_control_plane_url(self) -> None:
        failures = scan_text("API endpoint: http://prya:8081/health")
        self.assertTrue(
            any("DefaultControlPlaneURL" in f for f in failures),
            f"Expected http://prya:8081 violation, got: {failures}",
        )

    # --- clean text should pass ---

    def test_clean_text_passes(self) -> None:
        failures = scan_text(
            "Use `forge task list` to view the queue. "
            "See https://github.com/your-org/forge for the source. "
            "The control plane URL is configured via FORGE_API_URL."
        )
        self.assertEqual(failures, [], f"Expected no failures, got: {failures}")

    def test_clean_text_with_forge_commands_passes(self) -> None:
        failures = scan_text(
            "Run `forge daemon start` and `forge agent list`. "
            "Dispatch via `forge dispatch send forge:kimi msg.md`."
        )
        self.assertEqual(failures, [], f"Expected no failures, got: {failures}")

    # --- line number reporting ---

    def test_reports_correct_line_number(self) -> None:
        text = "Line one\nLine two with cli_v2 usage\nLine three"
        failures = scan_text(text)
        self.assertTrue(
            any("L2" in f for f in failures),
            f"Expected violation on L2, got: {failures}",
        )


if __name__ == "__main__":
    unittest.main()
