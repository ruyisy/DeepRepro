"""
Planning image analysis helpers.

These helpers are intentionally scoped to the planning phase only:
1. Copy user-supplied auxiliary images into the paper workspace
2. Run lightweight multimodal analysis on those images
3. Return a concise planning-oriented summary that can be injected into the planner
"""

import base64
import logging
import mimetypes
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from prompts.code_prompts import PLANNING_IMAGE_ANALYSIS_PROMPT
from utils.llm_utils import load_api_config


SUPPORTED_PLANNING_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
}
MAX_PLANNING_IMAGES = 6


def _create_default_logger() -> logging.Logger:
    logger = logging.getLogger(f"{__name__}.PlanningImageAgent")
    logger.setLevel(logging.INFO)
    return logger


def copy_planning_images_to_paper_dir(
    paper_dir: str,
    image_paths: Optional[List[str]],
    logger: Optional[logging.Logger] = None,
) -> List[str]:
    """
    Copy user-uploaded planning images into the paper workspace.

    Returns copied image paths under `<paper_dir>/planning_images/`.
    """
    logger = logger or _create_default_logger()

    if not image_paths:
        return []

    target_dir = os.path.join(paper_dir, "planning_images")
    os.makedirs(target_dir, exist_ok=True)

    copied_paths: List[str] = []
    seen_sources = set()

    for index, source_path in enumerate(image_paths, start=1):
        if not source_path or source_path in seen_sources:
            continue

        seen_sources.add(source_path)
        source = Path(source_path)

        if not source.exists() or not source.is_file():
            logger.warning(f"Skipping missing planning image: {source_path}")
            continue

        extension = source.suffix.lower()
        if extension not in SUPPORTED_PLANNING_IMAGE_EXTENSIONS:
            logger.warning(
                f"Skipping unsupported planning image type: {source_path} ({extension})"
            )
            continue

        safe_name = f"{index:02d}_{source.name}"
        destination = Path(target_dir) / safe_name

        try:
            shutil.copy2(source, destination)
            copied_paths.append(str(destination))
        except Exception as exc:
            logger.warning(f"Failed to copy planning image {source_path}: {exc}")

    logger.info(f"Copied {len(copied_paths)} planning images into {target_dir}")
    return copied_paths


async def analyze_planning_images(
    image_paths: Optional[List[str]],
    logger: Optional[logging.Logger] = None,
    config_path: str = "mcp_agent.secrets.yaml",
) -> Dict[str, Any]:
    """
    Analyze user-supplied planning images and return planning-oriented summaries.

    The analysis is intentionally best-effort:
    - If no valid image is available, return `skipped`
    - If the configured OpenAI-compatible endpoint cannot handle multimodal input,
      return `error` and let the planning phase continue without image context
    """
    logger = logger or _create_default_logger()

    valid_paths = _filter_valid_image_paths(image_paths or [])
    if not valid_paths:
        return {
            "status": "skipped",
            "message": "No valid planning images available.",
            "images": [],
            "combined_summary": "",
        }

    truncated = False
    if len(valid_paths) > MAX_PLANNING_IMAGES:
        valid_paths = valid_paths[:MAX_PLANNING_IMAGES]
        truncated = True

    client, model_name = _initialize_openai_vision_client(
        config_path=config_path,
        logger=logger,
    )
    if client is None or not model_name:
        return {
            "status": "skipped",
            "message": "No OpenAI-compatible multimodal configuration available.",
            "images": valid_paths,
            "combined_summary": "",
        }

    image_results: List[Dict[str, str]] = []
    failures: List[Dict[str, str]] = []

    for index, image_path in enumerate(valid_paths, start=1):
        try:
            summary = await _analyze_single_image(
                client=client,
                model_name=model_name,
                image_path=image_path,
                image_index=index,
            )
            image_results.append(
                {
                    "image_path": image_path,
                    "summary": summary.strip(),
                }
            )
        except Exception as exc:
            logger.warning(f"Planning image analysis failed for {image_path}: {exc}")
            failures.append({"image_path": image_path, "error": str(exc)})

    if not image_results:
        return {
            "status": "error",
            "message": "Planning image analysis failed for all provided images.",
            "images": valid_paths,
            "failures": failures,
            "combined_summary": "",
        }

    combined_summary = _build_combined_planning_image_summary(
        image_results=image_results,
        failures=failures,
        truncated=truncated,
    )

    return {
        "status": "success" if not failures else "partial",
        "message": f"Planning image analysis completed for {len(image_results)} images.",
        "images": valid_paths,
        "results": image_results,
        "failures": failures,
        "combined_summary": combined_summary,
        "model_name": model_name,
    }


def save_planning_image_analysis(
    paper_dir: str,
    analysis_result: Dict[str, Any],
    logger: Optional[logging.Logger] = None,
) -> Optional[str]:
    """Persist the planning image analysis result for inspection."""
    logger = logger or _create_default_logger()

    combined_summary = (analysis_result or {}).get("combined_summary", "").strip()
    if not combined_summary:
        return None

    output_path = os.path.join(paper_dir, "planning_image_analysis.md")
    try:
        with open(output_path, "w", encoding="utf-8") as file:
            file.write("# Planning Image Analysis\n\n")
            file.write(
                "This file is generated from user-supplied auxiliary images and is only used during planning.\n\n"
            )
            file.write(combined_summary)
            file.write("\n")
        logger.info(f"Planning image analysis saved to {output_path}")
        return output_path
    except Exception as exc:
        logger.warning(f"Failed to save planning image analysis: {exc}")
        return None


def _filter_valid_image_paths(image_paths: List[str]) -> List[str]:
    valid_paths: List[str] = []
    seen = set()

    for image_path in image_paths:
        if not image_path or image_path in seen:
            continue

        seen.add(image_path)
        path = Path(image_path)
        if not path.exists() or not path.is_file():
            continue

        if path.suffix.lower() not in SUPPORTED_PLANNING_IMAGE_EXTENSIONS:
            continue

        valid_paths.append(str(path))

    return valid_paths


def _initialize_openai_vision_client(
    config_path: str,
    logger: logging.Logger,
):
    api_config = load_api_config(config_path)
    openai_config = api_config.get("openai", {}) or {}
    openai_key = openai_config.get("api_key", "").strip()
    if not openai_key:
        logger.info("Planning image analysis skipped: no openai-compatible api key.")
        return None, None

    main_config_path = os.path.join(
        os.path.dirname(os.path.abspath(config_path)),
        "mcp_agent.config.yaml",
    )
    main_config = {}
    if os.path.exists(main_config_path):
        try:
            with open(main_config_path, "r", encoding="utf-8") as file:
                main_config = yaml.safe_load(file) or {}
        except Exception as exc:
            logger.warning(f"Failed to load main config for planning image analysis: {exc}")

    openai_main_config = main_config.get("openai", {}) or {}
    model_name = (
        openai_main_config.get("vision_model")
        or openai_main_config.get("planning_model")
        or openai_main_config.get("default_model")
    )
    if not model_name:
        logger.info("Planning image analysis skipped: no usable openai model configured.")
        return None, None

    from openai import AsyncOpenAI

    base_url = openai_config.get("base_url")
    if base_url:
        client = AsyncOpenAI(api_key=openai_key, base_url=base_url)
    else:
        client = AsyncOpenAI(api_key=openai_key)

    logger.info(f"Planning image analysis enabled with model: {model_name}")
    return client, model_name


async def _analyze_single_image(
    client,
    model_name: str,
    image_path: str,
    image_index: int,
) -> str:
    image_data_url = _encode_image_as_data_url(image_path)
    prompt = PLANNING_IMAGE_ANALYSIS_PROMPT.format(
        image_index=image_index,
        image_name=os.path.basename(image_path),
    )

    message_content = [
        {"type": "text", "text": prompt},
        {
            "type": "image_url",
            "image_url": {
                "url": image_data_url,
            },
        },
    ]

    try:
        response = await client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": message_content}],
            max_tokens=900,
        )
    except Exception as exc:
        if "max_tokens" in str(exc) and "max_completion_tokens" in str(exc):
            response = await client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": message_content}],
                max_completion_tokens=900,
            )
        else:
            raise

    content = response.choices[0].message.content if response.choices else ""
    if not content:
        raise ValueError(f"Empty planning image analysis response for {image_path}")

    return content.strip()


def _encode_image_as_data_url(image_path: str) -> str:
    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type:
        mime_type = "image/png"

    with open(image_path, "rb") as image_file:
        encoded = base64.b64encode(image_file.read()).decode("utf-8")

    return f"data:{mime_type};base64,{encoded}"


def _build_combined_planning_image_summary(
    image_results: List[Dict[str, str]],
    failures: List[Dict[str, str]],
    truncated: bool,
) -> str:
    sections: List[str] = []

    if truncated:
        sections.append(
            f"> Note: Only the first {MAX_PLANNING_IMAGES} user-supplied planning images were analyzed.\n"
        )

    for index, result in enumerate(image_results, start=1):
        sections.append(
            f"## User Figure {index}: `{os.path.basename(result['image_path'])}`\n\n{result['summary']}".strip()
        )

    if failures:
        failure_lines = [
            f"- `{os.path.basename(item['image_path'])}`: {item['error']}"
            for item in failures
        ]
        sections.append("## Unanalyzed Images\n" + "\n".join(failure_lines))

    return "\n\n".join(section for section in sections if section).strip()
