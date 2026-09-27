"""LLM provider client with multi-token rotation, fallback, and usage tracking."""
import json
import os
import time
from typing import Any, Dict, List, Optional
import httpx


class LLMResponse:
    """Standardized response from LLM call."""
    def __init__(
        self,
        content: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        model_name: str,
        api_url: str,
        retries: int = 0,
    ):
        self.content = content
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.latency_ms = latency_ms
        self.model_name = model_name
        self.api_url = api_url
        self.retries = retries


class TokenRotator:
    """Manages rotation across multiple API tokens for a provider."""

    def __init__(self, api_keys: List[str]):
        # Filter empty strings and whitespace
        self.api_keys = [k.strip() for k in api_keys if k and k.strip()]
        self.current_index = 0

    def get_current_key(self) -> str:
        if not self.api_keys:
            return ""
        return self.api_keys[self.current_index % len(self.api_keys)]

    def rotate(self) -> str:
        """Rotate to next key."""
        if not self.api_keys:
            return ""
        self.current_index = (self.current_index + 1) % len(self.api_keys)
        return self.get_current_key()

    def has_keys(self) -> bool:
        return len(self.api_keys) > 0


def _approx_token_count(text: str) -> int:
    """Estimate token count when not returned by provider (approx 4 chars/token)."""
    return max(1, len(text) // 4)


class LLMClient:
    """Multi-provider LLM client with automatic key rotation, retries, and token accounting."""

    def __init__(
        self,
        model_name: str = "qwen/qwen-2.5-coder-32b-instruct",
        provider_url: str = "https://openrouter.ai/api/v1",
        api_keys: Optional[List[str]] = None,
        max_retries: int = 3,
        timeout: float = 60.0,
    ):
        self.model_name = model_name
        self.provider_url = provider_url.rstrip("/")
        self.max_retries = max_retries
        self.timeout = timeout

        # Load API keys from argument or environment
        loaded_keys: List[str] = []
        if api_keys:
            loaded_keys.extend(api_keys)
        else:
            # Check environment variables
            env_keys = os.environ.get("OPENROUTER_API_KEYS") or os.environ.get("OPENROUTER_API_KEY")
            if not env_keys:
                env_keys = os.environ.get("GROQ_API_KEYS") or os.environ.get("GROQ_API_KEY")
            if not env_keys:
                env_keys = os.environ.get("LLM_API_KEYS") or os.environ.get("LLM_API_KEY")

            if env_keys:
                loaded_keys.extend([k.strip() for k in env_keys.split(",") if k.strip()])

        self.rotator = TokenRotator(loaded_keys)

    def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        stop_sequences: Optional[List[str]] = None,
        mock_response: Optional[str] = None,
    ) -> LLMResponse:
        """Send chat completion request to the LLM endpoint with retries and rotation."""
        # 1. Mock / offline path if explicitly requested or mock provider
        if self.provider_url == "mock" or mock_response is not None or os.environ.get("AGENT_MOCK_LLM") == "1":
            simulated_text = mock_response or "Thought: Solved.\n```python\nfinal_answer('done')\n```"
            in_tokens = _approx_token_count(system_prompt + " " + json.dumps(messages))
            out_tokens = _approx_token_count(simulated_text)
            return LLMResponse(
                content=simulated_text,
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                latency_ms=10.0,
                model_name=self.model_name,
                api_url=self.provider_url,
                retries=0,
            )

        payload_messages = [{"role": "system", "content": system_prompt}] + messages
        payload = {
            "model": self.model_name,
            "messages": payload_messages,
            "temperature": 0.0,
        }
        if stop_sequences:
            payload["stop"] = stop_sequences

        endpoint = f"{self.provider_url}/chat/completions"
        retries = 0
        last_error = None

        for attempt in range(self.max_retries + 1):
            key = self.rotator.get_current_key()
            headers = {
                "Content-Type": "application/json",
            }
            if key:
                headers["Authorization"] = f"Bearer {key}"

            start_t = time.perf_counter()
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(endpoint, json=payload, headers=headers)
                    elapsed_ms = (time.perf_counter() - start_t) * 1000.0

                    if resp.status_code == 200:
                        data = resp.json()
                        choices = data.get("choices", [])
                        if not choices:
                            raise ValueError(f"Empty choices in response: {data}")

                        content = choices[0].get("message", {}).get("content", "")
                        usage = data.get("usage", {})
                        in_tokens = usage.get("prompt_tokens") or _approx_token_count(system_prompt + str(messages))
                        out_tokens = usage.get("completion_tokens") or _approx_token_count(content)

                        return LLMResponse(
                            content=content,
                            input_tokens=int(in_tokens),
                            output_tokens=int(out_tokens),
                            latency_ms=elapsed_ms,
                            model_name=self.model_name,
                            api_url=self.provider_url,
                            retries=retries,
                        )

                    elif resp.status_code in (429, 402, 403, 502, 503, 504):
                        # Rate limit or quota exhaustion -> rotate token and retry
                        retries += 1
                        last_error = f"HTTP {resp.status_code}: {resp.text}"
                        self.rotator.rotate()
                        time.sleep(min(2 ** attempt, 8))
                    else:
                        retries += 1
                        last_error = f"HTTP {resp.status_code}: {resp.text}"
                        self.rotator.rotate()
                        time.sleep(1)

            except Exception as e:
                retries += 1
                last_error = str(e)
                self.rotator.rotate()
                time.sleep(1)

        raise RuntimeError(f"Failed to get LLM response after {retries} retries. Last error: {last_error}")
