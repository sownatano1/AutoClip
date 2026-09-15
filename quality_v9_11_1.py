from __future__ import annotations

import autoclip
import quality_v9_9 as q99
import quality_v9_11 as q11


_BASE_V99_LOCAL_CAPTION = q99._local_caption
_BASE_V911_CAPTION = q11._local_caption_v9_11


def _local_caption_v9_11_1(plan, source: dict, language: str) -> str:
    """Run the v9.11 caption guard while exposing the original v9.9 quiz fallback.

    v9.11 monkeypatches q99._local_caption during the pipeline. The v9.11 caption
    function intentionally reuses v9.9's good quiz templates, so temporarily restoring
    that original function prevents recursive self-calls.
    """
    current = q99._local_caption
    try:
        q99._local_caption = _BASE_V99_LOCAL_CAPTION
        return _BASE_V911_CAPTION(plan, source, language)
    finally:
        q99._local_caption = current


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original = q11._local_caption_v9_11
    try:
        q11._local_caption_v9_11 = _local_caption_v9_11_1
        autoclip.log("Quality v9.11.1: Local-First Editor · fallback de caption local protegido")
        q11.run(url, clips_count, min_seconds, max_seconds, whisper_model)
    finally:
        q11._local_caption_v9_11 = original
