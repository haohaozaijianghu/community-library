from pathlib import Path


def test_prompt_directory_copy_matches_runtime_prompt():
    root = Path(__file__).resolve().parents[1]
    assert (root / "prompt" / "system-prompt.md").read_bytes() == (root / "prompt.md").read_bytes()
