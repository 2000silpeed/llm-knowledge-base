"""통합 LLM 호출 모듈

settings.yaml 의 llm.provider 에 따라 Anthropic 또는 Ollama를 투명하게 전환합니다.
모든 스크립트는 이 모듈의 call_llm() 만 사용합니다.

지원 provider:
  anthropic — Claude API (기본값)
  ollama    — 로컬 Ollama (Gemma, Llama, Mistral 등 OpenAI 호환 엔드포인트 사용)

settings.yaml 예시:

  # Anthropic (기본)
  llm:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_env: ANTHROPIC_API_KEY
    context_limit: 200000
    output_reserved: 8000
    temperature: 0.3

  # Ollama (로컬)
  llm:
    provider: ollama
    model: gemma3:4b          # ollama pull gemma3:4b
    base_url: http://localhost:11434
    context_limit: 128000
    output_reserved: 4000
    temperature: 0.3

사용 예:
    from scripts.llm import call_llm
    from scripts.token_counter import load_settings

    settings = load_settings()
    response = call_llm("시스템 프롬프트", "유저 프롬프트", settings)

    # 캐시 사용
    from scripts.cache import make_cache_from_settings
    cache = make_cache_from_settings(settings)
    response = call_llm("시스템", "유저", settings, cache=cache)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.cache import CacheStore

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────

def call_llm(
    system_prompt: str,
    user_prompt: str,
    settings: dict,
    cache: "CacheStore | None" = None,
) -> str:
    """통합 LLM 호출.

    settings["llm"]["provider"] 값에 따라 백엔드를 선택합니다.

    지원 provider:
      anthropic — Claude API
      ollama    — 로컬 Ollama (vision은 네이티브 /api/generate 사용)
      openai    — OpenAI 호환 API (Z.ai, Groq, Together AI, 01.AI 등)

    Args:
        system_prompt: 시스템 프롬프트 텍스트
        user_prompt:   유저 메시지 텍스트
        settings:      load_settings() 결과
        cache:         CacheStore 인스턴스 (None 이면 캐싱 비활성)

    Returns:
        LLM 응답 텍스트

    Raises:
        ValueError:       지원하지 않는 provider
        EnvironmentError: API 키 미설정
        RuntimeError:     서버 연결 실패
    """
    llm_cfg = settings["llm"]
    model = llm_cfg["model"]
    provider = llm_cfg.get("provider", "anthropic")

    # ── 캐시 조회 ──
    if cache is not None:
        cached = cache.get(model, system_prompt, user_prompt)
        if cached is not None:
            logger.debug("[%s] 캐시 히트", provider)
            return cached

    # ── 실제 호출 ──
    if provider == "anthropic":
        result = _call_anthropic(system_prompt, user_prompt, llm_cfg)
    elif provider == "ollama":
        result = _call_ollama(system_prompt, user_prompt, llm_cfg)
    elif provider == "openai":
        result = _call_openai_compatible(system_prompt, user_prompt, llm_cfg)
    else:
        raise ValueError(
            f"지원하지 않는 provider: '{provider}'. "
            f"settings.yaml 의 llm.provider 를 'anthropic', 'ollama', 'openai' 중 하나로 설정하세요."
        )

    # ── 캐시 저장 ──
    if cache is not None:
        cache.put(model, system_prompt, user_prompt, result)

    return result


# ──────────────────────────────────────────────
# Anthropic
# ──────────────────────────────────────────────

def _call_anthropic(system_prompt: str, user_prompt: str, llm_cfg: dict) -> str:
    """Claude API 호출."""
    import anthropic

    api_key_env = llm_cfg.get("api_key_env", "ANTHROPIC_API_KEY")
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise EnvironmentError(
            f"API 키 환경변수 '{api_key_env}'가 설정되지 않았습니다.\n"
            f"export {api_key_env}='sk-ant-...' 를 실행하거나 .env 파일을 확인하세요."
        )

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=llm_cfg["model"],
        max_tokens=llm_cfg["output_reserved"],
        temperature=llm_cfg.get("temperature", 0.3),
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text


# ──────────────────────────────────────────────
# Ollama
# ──────────────────────────────────────────────

def _call_ollama(system_prompt: str, user_prompt: str, llm_cfg: dict) -> str:
    """Ollama OpenAI 호환 엔드포인트 호출.

    Ollama 가 실행 중이어야 합니다: ollama serve
    모델 사전 다운로드 필요: ollama pull <model>
    """
    import requests

    base_url = llm_cfg.get("base_url", "http://localhost:11434")
    url = f"{base_url.rstrip('/')}/v1/chat/completions"

    payload = {
        "model": llm_cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": llm_cfg.get("temperature", 0.3),
        "stream": False,
    }

    # max_tokens 가 설정된 경우 전달 (Ollama 는 선택적)
    if "output_reserved" in llm_cfg:
        payload["max_tokens"] = llm_cfg["output_reserved"]

    try:
        resp = requests.post(url, json=payload, timeout=300)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Ollama 서버에 연결할 수 없습니다: {base_url}\n"
            f"'ollama serve' 가 실행 중인지 확인하세요."
        )
    except requests.exceptions.HTTPError as e:
        body = ""
        try:
            body = resp.json().get("error", resp.text)
        except Exception:
            body = resp.text
        raise RuntimeError(f"Ollama API 오류 ({resp.status_code}): {body}") from e

    return resp.json()["choices"][0]["message"]["content"]


# ──────────────────────────────────────────────
# OpenAI 호환 (Z.ai, Groq, Together AI, 01.AI 등)
# ──────────────────────────────────────────────

def _call_openai_compatible(system_prompt: str, user_prompt: str, llm_cfg: dict) -> str:
    """OpenAI 호환 API 호출.

    Z.ai, Groq, Together AI, 01.AI, Fireworks 등 /v1/chat/completions 를 제공하는
    모든 서비스에서 동작합니다.

    settings.yaml 필수 필드:
      base_url:    API 기본 URL (예: https://chat.z.ai/api)
      api_key_env: API 키 환경변수명 (예: Z_AI_API_KEY)
      model:       모델명 (예: glm-4.5-air)
    """
    import requests

    base_url = llm_cfg.get("base_url", "")
    if not base_url:
        raise ValueError(
            "openai provider 사용 시 settings.yaml 에 llm.base_url 을 설정해야 합니다.\n"
            "예) base_url: https://chat.z.ai/api"
        )

    api_key_env = llm_cfg.get("api_key_env", "OPENAI_API_KEY")
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise EnvironmentError(
            f"API 키 환경변수 '{api_key_env}'가 설정되지 않았습니다.\n"
            f"export {api_key_env}='your-key' 를 실행하세요."
        )

    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": llm_cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": llm_cfg.get("temperature", 0.3),
        "stream": False,
    }
    if "output_reserved" in llm_cfg:
        payload["max_tokens"] = llm_cfg["output_reserved"]

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=300)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"API 서버에 연결할 수 없습니다: {base_url}")
    except requests.exceptions.HTTPError as e:
        body = ""
        try:
            body = resp.json().get("error", {}).get("message", resp.text)
        except Exception:
            body = resp.text
        raise RuntimeError(f"API 오류 ({resp.status_code}): {body}") from e

    return resp.json()["choices"][0]["message"]["content"]


# ──────────────────────────────────────────────
# Vision (이미지 → 텍스트 캡션)
# ──────────────────────────────────────────────

def call_vision(
    image_bytes: bytes,
    media_type: str,
    prompt: str,
    settings: dict,
) -> str:
    """이미지를 LLM에 전달해 설명 텍스트를 생성합니다.

    Anthropic: Claude Vision API 사용
    Ollama:    vision 지원 모델(llava, gemma3 등)이면 동일 엔드포인트 사용.
               미지원 모델이면 "(이미지 캡션 생략 — 모델이 vision 미지원)" 반환.

    Args:
        image_bytes: 이미지 바이너리
        media_type:  "image/png" | "image/jpeg" | "image/gif" | "image/webp"
        prompt:      캡션 요청 프롬프트
        settings:    load_settings() 결과

    Returns:
        이미지 설명 텍스트
    """
    import base64

    llm_cfg = settings["llm"]
    provider = llm_cfg.get("provider", "anthropic")
    b64 = base64.standard_b64encode(image_bytes).decode()

    if provider == "anthropic":
        return _vision_anthropic(b64, media_type, prompt, llm_cfg)
    elif provider == "ollama":
        return _vision_ollama(b64, media_type, prompt, llm_cfg)
    elif provider == "openai":
        return _vision_openai_compatible(b64, media_type, prompt, llm_cfg)
    else:
        raise ValueError(f"지원하지 않는 provider: '{provider}'")


def _vision_anthropic(b64: str, media_type: str, prompt: str, llm_cfg: dict) -> str:
    import anthropic

    api_key = os.environ.get(llm_cfg.get("api_key_env", "ANTHROPIC_API_KEY"))
    if not api_key:
        return "(이미지 캡션 생략 — API 키 없음)"

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=llm_cfg["model"],
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return response.content[0].text


def _vision_ollama(b64: str, media_type: str, prompt: str, llm_cfg: dict) -> str:
    """Ollama vision 호출 (llava, gemma3 등 멀티모달 모델 전용)."""
    import requests

    base_url = llm_cfg.get("base_url", "http://localhost:11434")
    # Ollama 네이티브 /api/generate 엔드포인트 사용 (vision 이미지 전달)
    url = f"{base_url.rstrip('/')}/api/generate"

    payload = {
        "model": llm_cfg["model"],
        "prompt": prompt,
        "images": [b64],
        "stream": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=300)
        resp.raise_for_status()
        return resp.json().get("response", "")
    except Exception as e:
        logger.warning("Ollama vision 호출 실패 (%s) — 캡션 생략: %s", llm_cfg["model"], e)
        return f"(이미지 캡션 생략 — Ollama vision 오류: {e})"


def _vision_openai_compatible(b64: str, media_type: str, prompt: str, llm_cfg: dict) -> str:
    """OpenAI 호환 vision 호출 (base64 이미지 URL 방식)."""
    import requests

    base_url = llm_cfg.get("base_url", "")
    if not base_url:
        return "(이미지 캡션 생략 — base_url 미설정)"

    api_key_env = llm_cfg.get("api_key_env", "OPENAI_API_KEY")
    api_key = os.environ.get(api_key_env, "")

    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": llm_cfg["model"],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": 512,
        "stream": False,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("OpenAI 호환 vision 호출 실패 (%s) — 캡션 생략: %s", llm_cfg["model"], e)
        return f"(이미지 캡션 생략 — vision 오류: {e})"
