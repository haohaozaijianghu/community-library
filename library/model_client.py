import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from library.dependencies import LibraryError


@dataclass(frozen=True)
class Provider:
    name: str
    url: str
    text: tuple
    embedding: tuple = ()
    model_labels: dict = field(default_factory=dict)


PROVIDERS = {
    "deepseek": Provider(
        "DeepSeek", "https://api.deepseek.com/v1", ("deepseek-flash", "deepseek-v4-pro"),
        model_labels={"deepseek-flash": "DeepSeek-V4.1-Flash-1017", "deepseek-v4-pro": "DeepSeek-V4-Pro-0813"},
    ),
    "openai": Provider("OpenAI", "https://api.openai.com/v1", ("gpt-4.1-mini",), ("text-embedding-3-small",)),
}


def validate_url(value):
    try:
        address = urlsplit(value)
        if (address.scheme in {"https", "http"} and address.hostname and not any((
            address.username, address.password, address.query, address.fragment,
        ))):
            return value.rstrip("/")
    except ValueError:
        pass
    raise LibraryError(400, "invalid_model_url", "模型地址格式无效")


class Gateway:
    def __init__(self, settings):
        self.settings = settings
        self.http = httpx.Client(timeout=settings.model_timeout, trust_env=False, follow_redirects=False)

    def close(self):
        self.http.close()

    def send(self, model, path, payload):
        secret = model.get("encrypted_key")
        headers = {"Authorization": "Bearer " + self.settings.cipher().decrypt(secret).decode()} if secret else {}
        try:
            response = self.http.post(validate_url(model["base_url"]) + path, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as error:
            raise LibraryError(502, "model_timeout", "模型连接超时") from error
        except (httpx.HTTPError, ValueError) as error:
            raise LibraryError(502, "model_unreachable", "无法读取模型返回") from error

    def json_with_usage(self, model, instruction, data):
        response = self.send(model, "/chat/completions", {
            "model": model["model_name"],
            "messages": [{"role": "system", "content": instruction},
                         {"role": "user", "content": json.dumps(data, ensure_ascii=False)}],
            "response_format": {"type": "json_object"},
        })
        return json.loads(response["choices"][0]["message"]["content"]), response.get("usage")

    def probe(self, model):
        try:
            if model["kind"] == "embedding":
                data = self.send(model, "/embeddings", {"model": model["model_name"], "input": ["hello"]})
                vector = data["data"][0]["embedding"]
                if not vector or not all(type(item) in (int, float) for item in vector):
                    raise ValueError("invalid embedding")
                return len(vector)
            answer, _ = self.json_with_usage(model, '只返回 JSON 对象 {"ok":true}。', {})
            if not isinstance(answer, dict):
                raise ValueError("invalid response")
            return None
        except (TypeError, ValueError, KeyError, IndexError) as error:
            raise LibraryError(502, "invalid_model_response", "模型返回格式无效") from error
