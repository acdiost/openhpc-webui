import unittest
from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/python-publish.yml"


class PublishWorkflowTests(unittest.TestCase):
    def test_release_build_runs_all_node_tests(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("actions/setup-node@", workflow)
        self.assertIn('node-version: "22.17.1"', workflow)
        self.assertIn("node --test tests/*.js", workflow)
        self.assertLess(
            workflow.index("node --test tests/*.js"),
            workflow.index("python -m build"),
        )


if __name__ == "__main__":
    unittest.main()
