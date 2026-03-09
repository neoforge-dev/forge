import unittest

from scripts.check_public_release_boundary import scan_text


class PublicReleaseBoundaryTest(unittest.TestCase):
    def test_scan_text_flags_private_markers(self) -> None:
        failures = scan_text("prya codeswiftr-com interview-simulator /Users/test/FORGE")
        self.assertGreaterEqual(len(failures), 4)

    def test_scan_text_accepts_generic_public_text(self) -> None:
        failures = scan_text(
            "Use forge-control-plane with sample-domain and demo-saas in public docs."
        )
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
