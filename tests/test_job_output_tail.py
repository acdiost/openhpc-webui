import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openhpc_webui.services.slurm_manager import SlurmManager


class JobOutputTailTests(unittest.TestCase):
    def setUp(self):
        self.manager = SlurmManager()

    def _read(self, content: bytes, **kwargs):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "job.out"
            output.write_bytes(content)
            return self.manager.read_job_output(
                "123",
                "stdout",
                allowed_roots=[temp_dir],
                job_detail={"StdOut": str(output)},
                **kwargs,
            )

    def test_returns_only_the_last_requested_lines(self):
        result = self._read(b"one\ntwo\nthree\nfour\n", max_lines=2, max_bytes=1024)

        self.assertTrue(result["success"])
        self.assertEqual(result["content"], "three\nfour\n")
        self.assertEqual(result["lines_read"], 2)
        self.assertTrue(result["truncated"])
        self.assertTrue(result["truncated_by_lines"])
        self.assertFalse(result["truncated_by_bytes"])

    def test_stops_after_a_tail_chunk_when_the_line_limit_is_reached(self):
        content = b"".join((b"x" * 99) + b"\n" for _ in range(2000))

        result = self._read(content, max_lines=2, max_bytes=len(content))

        self.assertEqual(result["content"], ("x" * 99 + "\n") * 2)
        self.assertLess(result["bytes_read"], len(content))
        self.assertTrue(result["truncated_by_lines"])
        self.assertFalse(result["truncated_by_bytes"])

    def test_giant_single_line_is_bounded_by_bytes(self):
        result = self._read(b"x" * 4096, max_lines=1000, max_bytes=128)

        self.assertTrue(result["success"])
        self.assertEqual(result["content"], "x" * 128)
        self.assertEqual(result["bytes_read"], 128)
        self.assertEqual(result["max_bytes"], 128)
        self.assertTrue(result["truncated"])
        self.assertFalse(result["truncated_by_lines"])
        self.assertTrue(result["truncated_by_bytes"])

    def test_byte_truncation_discards_an_incomplete_leading_line(self):
        result = self._read(
            b"prefix that should be incomplete\nlast line\n",
            max_lines=1000,
            max_bytes=20,
        )

        self.assertEqual(result["content"], "last line\n")
        self.assertTrue(result["truncated_by_bytes"])

    def test_utf8_tail_is_decoded_without_raising(self):
        result = self._read(
            "第一行\n第二行\n第三行\n".encode("utf-8"),
            max_lines=2,
            max_bytes=1024,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["content"], "第二行\n第三行\n")

    def test_empty_file_reports_no_truncation(self):
        result = self._read(b"", max_lines=1000, max_bytes=128)

        self.assertEqual(result["content"], "")
        self.assertEqual(result["bytes_read"], 0)
        self.assertFalse(result["truncated"])
        self.assertFalse(result["truncated_by_lines"])
        self.assertFalse(result["truncated_by_bytes"])

    def test_environment_configures_the_default_byte_limit(self):
        with patch.dict("os.environ", {"JOB_OUTPUT_MAX_BYTES": "2048"}):
            result = self._read(b"x" * 4096)

        self.assertEqual(result["max_bytes"], 2048)
        self.assertEqual(result["bytes_read"], 2048)
        self.assertTrue(result["truncated_by_bytes"])

    def test_frontend_distinguishes_line_and_byte_truncation(self):
        template = (
            Path(__file__).parents[1] / "templates" / "jobs.html"
        ).read_text(encoding="utf-8")

        self.assertIn("truncated_by_lines", template)
        self.assertIn("truncated_by_bytes", template)


if __name__ == "__main__":
    unittest.main()
