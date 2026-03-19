from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

import httpx


class OpenAIResponsesClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 90.0,
        app_name: str = "AT V6.0",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._app_name = app_name

    async def complete_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        image_path: str | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        user_content: list[dict[str, Any]] = [{"type": "input_text", "text": user_prompt}]
        if image_path is not None:
            data_url = self._image_data_url(image_path)
            if data_url is not None:
                user_content.append({"type": "input_image", "image_url": data_url})

        payload: dict[str, Any] = {
            "model": model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": user_content},
            ],
        }
        if reasoning_effort is not None:
            payload["reasoning"] = {"effort": reasoning_effort}

        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "X-Title": self._app_name,
            },
            timeout=self._timeout_seconds,
        ) as client:
            response = await client.post("/responses", json=payload)
            response.raise_for_status()
            body = response.json()
        content = self._extract_output_text(body)
        if not content:
            raise RuntimeError("OpenAI response did not contain output text.")
        return content

    def _image_data_url(self, image_path: str) -> str | None:
        path = Path(image_path).expanduser()
        if not path.exists() or not path.is_file():
            return None
        mime_type, _ = mimetypes.guess_type(path.name)
        safe_mime = mime_type or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{safe_mime};base64,{encoded}"

    def _extract_output_text(self, payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        parts: list[str] = []
        for item in payload.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
                elif isinstance(text, dict):
                    value = text.get("value")
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
        return "\n\n".join(parts)
