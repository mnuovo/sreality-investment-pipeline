import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class PipelineRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pipeline test ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.script = self.root / "run_pipeline.sh"
        shutil.copyfile(Path(__file__).resolve().parents[1] / "run_pipeline.sh", self.script)
        interpreter = self.root / ".venv" / "bin" / "python"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text(
            '#!/usr/bin/env bash\n'
            'printf "%s|%s\\n" "$PWD" "$*" >> "$TEST_LOG"\n'
            'printf "%s\\n" "$#" >> "$TEST_ARG_COUNTS"\n'
            'if [[ "$*" == "${FAIL_COMMAND:-not-a-command}" ]]; then exit 7; fi\n'
        )
        interpreter.chmod(0o755)
        self.log = self.root / "commands.log"

    def run_script(self, fail_command=None, arguments=()):
        environment = {**os.environ, "TEST_LOG": str(self.log), "TEST_ARG_COUNTS": str(self.root / "argument_counts.log")}
        if fail_command is not None:
            environment["FAIL_COMMAND"] = fail_command
        return subprocess.run(
            ["bash", str(self.script), *arguments], cwd=self.root.parent, env=environment,
            capture_output=True, text=True, timeout=10,
        )

    def commands(self):
        if not self.log.exists():
            return []
        lines = self.log.read_text().splitlines()
        self.assertTrue(all(line.startswith(str(self.root) + "|") for line in lines))
        return [line.split("|", 1)[1] for line in lines]

    def test_runs_all_stages_in_order_from_another_directory(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commands(), [
            "-", "-u -m sreality_investment_pipeline.scraper_harness",
            "-u -m sreality_investment_pipeline.math_filter_engine",
            "-u -m sreality_investment_pipeline.ai_analyzer_agent",
            "-u -m sreality_investment_pipeline.generate_dashboard",
        ])
        self.assertIn("Pipeline completed", result.stdout)

    def test_preflight_failure_stops_before_scraping(self):
        result = self.run_script("-")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(self.commands(), ["-"])
        self.assertNotIn("Pipeline completed", result.stdout)

    def test_search_urls_and_argument_boundaries_are_preserved(self):
        sale = "https://www.sreality.cz/hledani/prodej/byty?region=Praha&velikost=2%2Bkk"
        rental = "https://www.sreality.cz/hledani/pronajem/byty?region=Praha&vlastnictvi=osobni"
        arguments = ("--sale-url", sale, "--rental-url", rental, "--state-file", str(self.root / "private state.json"))
        result = self.run_script(arguments=arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        self.assertEqual(commands[0], "- " + " ".join(arguments))
        self.assertEqual(commands[1], "-u -m sreality_investment_pipeline.scraper_harness " + " ".join(arguments))
        self.assertEqual(commands[2:], [
            "-u -m sreality_investment_pipeline.math_filter_engine",
            "-u -m sreality_investment_pipeline.ai_analyzer_agent",
            "-u -m sreality_investment_pipeline.generate_dashboard",
        ])
        self.assertEqual((self.root / "argument_counts.log").read_text().splitlines(), ["7", "9", "3", "3", "3"])

    def test_help_skips_ollama_preflight_and_pipeline_stages(self):
        result = self.run_script(arguments=("--help",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commands(), ["-m sreality_investment_pipeline.scraper_harness --help"])

    def test_stage_failure_stops_before_analyzer_and_report(self):
        result = self.run_script("-u -m sreality_investment_pipeline.math_filter_engine")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(len(self.commands()), 3)
        self.assertIn("No later stages were run", result.stderr)

    def test_missing_virtual_environment_fails_clearly(self):
        (self.root / ".venv" / "bin" / "python").unlink()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("virtual environment not found", result.stderr)
        self.assertEqual(self.commands(), [])


if __name__ == "__main__":
    unittest.main()