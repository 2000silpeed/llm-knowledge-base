"""프로젝트 공통 상수

여러 모듈에서 공유되는 상수 값 모음.
"""

# MIME 타입 → 파일 확장자 (HTTP Content-Type 기반 이미지 저장 시 사용)
MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}

# 파일 확장자 → MIME 타입 (Vision API 호출 시 media_type 지정 용도)
EXT_TO_MIME: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/png",   # Vision API가 bmp 미지원 → png로 처리
}
