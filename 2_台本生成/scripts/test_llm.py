"""従量課金APIや実際のClaude CLIを呼ばず、LLM呼び出し層だけを確認する。"""
from subprocess import CompletedProcess
from unittest import TestCase, main
from unittest.mock import patch

import llm


class AskCliTest(TestCase):
    @patch("llm.subprocess.run")
    def test_disables_tools_for_noninteractive_generation(self, run) -> None:
        run.return_value = CompletedProcess([], 0, stdout='{"ok": true}\n', stderr="")

        result = llm._ask_cli("台本をJSONで返して")

        self.assertEqual('{"ok": true}\n', result)
        run.assert_called_once_with(
            [
                "claude",
                "-p",
                "--tools",
                "",
                "--no-session-persistence",
                "台本をJSONで返して",
            ],
            capture_output=True,
            text=True,
            timeout=llm.CLI_TIMEOUT,
        )

    @patch("llm.ask", return_value='```json\n{"ok": true}\n```')
    def test_ask_json_accepts_fenced_json(self, _ask) -> None:
        self.assertEqual({"ok": True}, llm.ask_json("台本を作って"))


if __name__ == "__main__":
    main()
