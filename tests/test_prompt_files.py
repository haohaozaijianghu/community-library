from pathlib import Path

import pytest

from scripts.check_prompt import completion_endpoint


def test_prompt_directory_copy_matches_runtime_prompt():
    root = Path(__file__).resolve().parents[1]
    assert (root / "prompt" / "system-prompt.md").read_bytes() == (root / "prompt.md").read_bytes()


@pytest.mark.parametrize("base_url", [
    "https://model.example/v1/",
    "http://localhost:8080/v1/",
    "http://127.0.0.1:8080/v1/",
    "http://[::1]:8080/v1/",
])
def test_prompt_evaluation_preserves_https_and_loopback_endpoints(base_url):
    assert str(completion_endpoint(base_url)) == base_url + "chat/completions"


@pytest.mark.parametrize("base_url", [
    "http://model.example/v1",
    "http://localhost.model.example/v1",
    "https://user:password@model.example/v1",
    "https://model.example/v1?other=1",
])
def test_prompt_evaluation_rejects_unsafe_endpoints(base_url):
    with pytest.raises(ValueError):
        completion_endpoint(base_url)
