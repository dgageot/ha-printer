import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Supervisor's RE_DOCKER_IMAGE_BUILD excludes digest references.
BUILD_IMAGE = re.compile(
    r"^([a-zA-Z\-\.:\d{}]+/)*?([\-\w{}]+)/([\-\w{}]+)(:[\.\-\w{}]+)?$"
)


class BuildConfigTests(unittest.TestCase):
    def test_base_image_is_accepted_by_supervisor(self):
        build = (ROOT / "build.yaml").read_text()
        base = re.search(r"^  aarch64: (.+)$", build, re.MULTILINE).group(1)
        self.assertRegex(base, BUILD_IMAGE)
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn(f"ARG BUILD_FROM={base}\n", dockerfile)


if __name__ == "__main__":
    unittest.main()
