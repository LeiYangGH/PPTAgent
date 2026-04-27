import base64
import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP
from mcp.types import ImageContent

from deeppresenter.utils.config import DeepPresenterConfig
from deeppresenter.utils.log import info, set_logger
from deeppresenter.utils.webview import PlaywrightConverter, convert_html_to_pptx
from pptagent.model_utils import _get_lid_model

mcp = FastMCP("DeepPresenter")
CONFIG = DeepPresenterConfig.load_from_file(os.getenv("CONFIG_FILE"))
try:
    LID_MODEL = _get_lid_model()
except Exception:
    LID_MODEL = None
REFLECTIVE_DESIGN = CONFIG.design_agent.is_multimodal and CONFIG.heavy_reflect
WORKSPACE = Path(os.getenv("WORKSPACE", "."))


def _check_image_coverage(
    html_path: Path, manuscript_file: str | None
) -> list[str]:
    """Check that images referenced in the manuscript page are present in the HTML.

    Returns a list of warning messages; empty list means all images are covered.
    """
    if manuscript_file is None:
        return []

    md_path = Path(manuscript_file)
    if not md_path.exists():
        for candidate in [
            html_path.parent.parent / manuscript_file,
            WORKSPACE / manuscript_file,
        ]:
            if candidate.exists():
                md_path = candidate
                break
        else:
            return []

    if not md_path.exists():
        return []

    slide_num_match = re.search(r"(\d+)", html_path.stem)
    if slide_num_match is None:
        return []
    slide_idx = int(slide_num_match.group(1)) - 1

    with open(md_path, encoding="utf-8") as f:
        markdown = f.read()

    pages = [p for p in markdown.split("\n---\n") if p.strip()]
    if slide_idx < 0 or slide_idx >= len(pages):
        return []

    page_content = pages[slide_idx]

    md_images: list[str] = []
    for match in re.finditer(r"!?\[(.*?)\]\((.*?)\)", page_content):
        img_path = match.group(2).split()[0].strip("\"'")
        if re.match(r"https?://", img_path):
            continue
        md_images.append(img_path)

    if not md_images:
        return []

    with open(html_path, encoding="utf-8") as f:
        html_content = f.read()

    html_images: set[str] = set()
    for match in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html_content):
        html_images.add(match.group(1))

    warnings: list[str] = []
    for img_path in md_images:
        img_filename = Path(img_path).name
        found = any(
            img_filename in html_src or img_path in html_src
            for html_src in html_images
        )
        if not found:
            warnings.append(
                f"Image from manuscript NOT found in HTML: {img_path}. "
                f"You MUST include this image using <img src=\"{img_path}\">. "
                f"Do NOT replace it with a matplotlib/code-generated chart."
            )

    return warnings


@mcp.tool()
async def inspect_slide(
    html_file: str,
    aspect_ratio: Literal["16:9", "4:3", "A1", "A2", "A3", "A4"] = "16:9",
    manuscript_file: str | None = None,
) -> ImageContent | str:
    """
    Validate the HTML slide file. If validation passes, proceed to the next slide; if it fails, fix the reported issues and re-validate.

    Args:
        html_file: Path to the HTML slide file to validate.
        aspect_ratio: Slide aspect ratio (default 16:9).
        manuscript_file: Path to the manuscript markdown file. If provided, checks that
            all images referenced in the corresponding manuscript page appear in the HTML.

    Returns:
        ImageContent: The slide as an image content (reflective mode only)
        str: Validation result message
    """
    html_path = Path(html_file).absolute()
    assert html_path.is_file() and html_path.suffix == ".html", (
        f"HTML path {html_path} does not exist or is not an HTML file"
    )

    image_warnings = _check_image_coverage(html_path, manuscript_file)

    try:
        await convert_html_to_pptx(html_path, aspect_ratio=aspect_ratio)
    except Exception as e:
        error_msg = f"Validation FAILED for {html_path.name}: {e}. Fix the reported issues and call inspect_slide again."
        if image_warnings:
            error_msg += "\n\nImage coverage issues:\n" + "\n".join(image_warnings)
        return error_msg

    if image_warnings:
        return (
            f"Validation FAILED for {html_path.name} due to missing images:\n"
            + "\n".join(image_warnings)
            + "\n\nFix these issues by adding the missing <img> tags and call inspect_slide again."
        )

    if REFLECTIVE_DESIGN:
        pdf_path = Path(tempfile.mkdtemp()) / "slide.pdf"
        async with PlaywrightConverter() as converter:
            image_dir = await converter.convert_to_pdf(
                [html_path], pdf_path, aspect_ratio
            )
        image_path = image_dir / "slide_01.jpg"
        image_data = image_path.read_bytes()
        base64_data = (
            f"data:image/jpeg;base64,{base64.b64encode(image_data).decode('utf-8')}"
        )
        return ImageContent(
            type="image",
            data=base64_data,
            mimeType="image/jpeg",
        )
    else:
        slide_num = html_path.stem.split("_")[-1] if "_" in html_path.stem else html_path.stem
        return f"Validation PASSED for {html_path.name}. This slide is valid. Proceed to generate the next slide (slide_{int(slide_num)+1:02d}.html). Do NOT rewrite this slide again."


@mcp.tool()
def inspect_manuscript(md_file: str) -> dict:
    """
    Inspect the markdown manuscript for general statistics, content density, and image asset validation.
    Args:
        md_file (str): The path to the markdown file
    """
    md_path = Path(md_file)
    assert md_path.exists(), f"file does not exist: {md_file}"
    assert md_file.lower().endswith(".md"), f"file is not a markdown file: {md_file}"

    with open(md_file, encoding="utf-8") as f:
        markdown = f.read()

    pages = [p for p in markdown.split("\n---\n") if p.strip()]
    result = defaultdict(list)
    result["num_pages"] = len(pages)
    if LID_MODEL is not None:
        label = LID_MODEL.predict(markdown[:1000].replace("\n", " "))
        result["language"] = label[0][0].replace("__label__", "")
    else:
        result["language"] = "unknown"

    # ── Per-page content density checks ────────────────────────────
    MAX_CHARS_PER_PAGE = 120
    MAX_BULLETS_PER_PAGE = 6
    for i, page in enumerate(pages, 1):
        # Strip image references and headings for body-text measurement
        body_lines: list[str] = []
        bullet_count = 0
        for line in page.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("!") and "](" in stripped:
                continue
            if stripped.startswith("#"):
                continue
            if stripped.startswith("- ") or stripped.startswith("* ") or re.match(r"\d+\.\s", stripped):
                bullet_count += 1
            body_lines.append(stripped)
        body_text = " ".join(body_lines)
        char_count = len(body_text)

        if char_count > MAX_CHARS_PER_PAGE:
            result["warnings"].append(
                f"Page {i}: body text is {char_count} chars (max {MAX_CHARS_PER_PAGE}). "
                "Reduce to short bullet points."
            )
        if bullet_count > MAX_BULLETS_PER_PAGE:
            result["warnings"].append(
                f"Page {i}: {bullet_count} bullet points (max {MAX_BULLETS_PER_PAGE}). "
                "Merge or remove points."
            )
        # Detect prose paragraphs (3+ consecutive non-bullet, non-heading lines)
        consecutive_prose = 0
        for line in page.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("!"):
                consecutive_prose = 0
                continue
            if stripped.startswith("- ") or stripped.startswith("* ") or re.match(r"\d+\.\s", stripped):
                consecutive_prose = 0
                continue
            consecutive_prose += 1
            if consecutive_prose >= 3:
                result["warnings"].append(
                    f"Page {i}: contains prose paragraphs (3+ consecutive non-bullet lines). "
                    "Convert to concise bullet points."
                )
                break

    # ── Image asset validation ──────────────────────────────────────
    seen_images = set()
    for match in re.finditer(r"!\[(.*?)\]\((.*?)\)", markdown):
        label, path = match.group(1), match.group(2)
        path = path.split()[0].strip("\"'")

        if path in seen_images:
            continue
        seen_images.add(path)

        if re.match(r"https?://", path):
            result["warnings"].append(
                f"External link detected: {match.group(0)}, consider downloading to local storage."
            )
            continue

        if not (md_path.parent / path).exists() and not Path(path).exists():
            result["warnings"].append(f"Image file does not exist: {path}")

        if not label.strip():
            result["warnings"].append(f"Image {path} is missing alt text.")

        count = markdown.count(path)
        if count > 1:
            result["warnings"].append(
                f"Image {path} used {count} times in the whole presentation manuscript."
            )

    if len(result["warnings"]) == 0:
        result["success"].append(
            "Image asset validation passed: all referenced images exist."
        )

    return result


if __name__ == "__main__":
    work_dir = Path(os.environ["WORKSPACE"])
    assert work_dir.exists(), f"Workspace {work_dir} does not exist."
    os.chdir(work_dir)
    set_logger(f"task-{work_dir.stem}", work_dir / ".history" / "task.log")

    if REFLECTIVE_DESIGN:
        info("Reflective Design is enabled.")

    mcp.run(show_banner=False)
