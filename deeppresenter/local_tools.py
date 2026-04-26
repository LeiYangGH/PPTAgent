import base64
import csv
import math
import os
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

import httpx
from PIL import Image
from filelock import FileLock
from mcp.types import ImageContent
from pydantic import BaseModel

from deeppresenter.utils.config import DeepPresenterConfig
from deeppresenter.utils.log import debug, warning
from deeppresenter.utils.webview import PlaywrightConverter, convert_html_to_pptx
from pptagent.model_utils import _get_lid_model
from pptagent_pptx import Presentation


class Todo(BaseModel):
    id: str
    content: str
    status: Literal["pending", "in_progress", "completed", "skipped"]


def _rewrite_image_link(match: re.Match[str], md_dir: Path) -> str:
    """Rewrite markdown image link to absolute path with aspect ratio in alt text."""
    alt_text = match.group(1)
    target = match.group(2).strip()
    if not target:
        return match.group(0)
    parts = re.match(r"([^\s]+)(.*)", target)
    if not parts:
        return match.group(0)
    local_path = parts.group(1).strip("\"'")
    rest = parts.group(2)
    p = Path(local_path)
    if not p.is_absolute() and (md_dir / local_path).exists():
        p = md_dir / local_path
    if not p.exists():
        return match.group(0)

    updated_alt = alt_text
    try:
        with Image.open(p) as img:
            width, height = img.size
        if width > 0 and height > 0 and not re.search(r"\b\d+:\d+\b", updated_alt):
            factor = math.gcd(width, height)
            ratio = f"{width // factor}:{height // factor}"
            updated_alt = f"{updated_alt}, {ratio}" if updated_alt else ratio
    except Exception as e:
        warning(f"Failed to get image size for {p}: {e}")

    new_path = p.resolve().as_posix()
    return f"![{updated_alt}]({new_path}{rest})"


def make_local_tools(workspace: Path, config: DeepPresenterConfig) -> dict[str, Any]:
    """Factory that creates workspace-bound local tool callables.

    These tools replace the corresponding MCP servers (task, deeppresenter,
    and conditionally tool_agents) to eliminate stdio transport overhead.
    """
    todo_csv_path = workspace / "todo.csv"
    todo_lock_path = workspace / ".todo.csv.lock"

    # Try loading language detection model, but tolerate failure
    try:
        lid_model = _get_lid_model()
    except Exception:
        lid_model = None

    reflective_design = config.design_agent.is_multimodal and config.heavy_reflect

    # ------------------------------------------------------------------
    # Task tools
    # ------------------------------------------------------------------

    def _load_todos() -> list[Todo]:
        if not todo_csv_path.exists():
            return []
        lock = FileLock(todo_lock_path)
        with lock:
            with open(todo_csv_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                return [Todo(**row) for row in reader]

    def _save_todos(todos: list[Todo]) -> None:
        lock = FileLock(todo_lock_path)
        with lock:
            with open(todo_csv_path, "w", encoding="utf-8", newline="") as f:
                if todos:
                    fieldnames = ["id", "content", "status"]
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    for todo in todos:
                        writer.writerow(todo.model_dump())

    def todo_create(todo_content: str) -> str:
        """Create a new todo item and add it to the todo list.

        Args:
            todo_content: The content/description of the todo item

        Returns:
            Confirmation message with the created todo's ID
        """
        todos = _load_todos()
        new_id = str(len(todos))
        new_todo = Todo(id=new_id, content=todo_content, status="pending")
        todos.append(new_todo)
        _save_todos(todos)
        return f"Todo {new_id} created"

    def todo_update(
        idx: int,
        todo_content: str | None = None,
        status: Literal["completed", "in_progress", "skipped"] | None = None,
    ) -> str:
        """Update an existing todo item's content or status.

        Args:
            idx: The index of the todo item to update
            todo_content: New content for the todo item
            status: New status for the todo item

        Returns:
            Confirmation message with the updated todo's ID
        """
        todos = _load_todos()
        assert 0 <= idx < len(todos), f"Invalid todo index: {idx}"
        if todo_content is not None:
            todos[idx].content = todo_content
        if status is not None:
            todos[idx].status = status
        _save_todos(todos)
        return "Todo updated successfully"

    def todo_list() -> str | list[Todo]:
        """Get the current todo list or check if all todos are completed.

        Returns:
            Either a completion message if all todos are done/skipped,
            or the current list of todo items
        """
        todos = _load_todos()
        if not todos or all(todo.status in ["completed", "skipped"] for todo in todos):
            todo_csv_path.unlink(missing_ok=True)
            return "All todos completed"
        else:
            return todos

    def thinking(thought: str) -> str:
        """This tool is for explicitly reasoning about the current task state and next actions."""
        debug(f"Thought: {thought}")
        return thought

    def finalize(outcome: str, agent_name: str = "") -> str:
        """When all tasks are finished, call this function to finalize the loop.

        Args:
            outcome: The path to the final outcome file or directory.
        """
        path = Path(outcome)
        assert path.exists(), f"Outcome {outcome} does not exist"

        if agent_name == "Planner":
            assert path.suffix == ".json", (
                f"Outline file should be a JSON file, got {path.suffix}"
            )
        elif agent_name == "Research":
            md_dir = path.parent
            assert path.suffix == ".md", (
                f"Outcome file should be a markdown file, got {path.suffix}"
            )
            with open(path, encoding="utf-8") as f:
                content = f.read()
            try:
                content = re.sub(
                    r"!\[(.*?)\]\((.*?)\)",
                    lambda match: _rewrite_image_link(match, md_dir),
                    content,
                )
                shutil.copyfile(path, md_dir / ("." + path.name))
                path.write_text(content, encoding="utf-8")
            except Exception as e:
                warning(f"Failed to rewrite image links: {e}")
        elif agent_name == "PPTAgent":
            assert path.is_file() and path.suffix == ".pptx", (
                f"Outcome file should be a pptx file, got {path.suffix}"
            )
            prs = Presentation(str(path))
            if len(prs.slides) <= 0:
                return "PPTX file should contain at least one slide"
        elif agent_name == "Design":
            html_files = list(path.glob("*.html"))
            if len(html_files) <= 0:
                return "Outcome path should be a directory containing HTML files"
            if not all(f.stem.startswith("slide_") for f in html_files):
                return "All HTML files should start with 'slide_'"
        elif path.is_file() and agent_name:
            if path.stat().st_size == 0:
                return f"Outcome file for {agent_name} is empty"

        todo_csv_path.unlink(missing_ok=True)
        todo_lock_path.unlink(missing_ok=True)

        debug(f"Agent {agent_name} finalized the outcome: {outcome}")
        return outcome

    # ------------------------------------------------------------------
    # Reflect tools
    # ------------------------------------------------------------------

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
            # Try resolving relative to the HTML file's parent workspace
            for candidate in [
                html_path.parent.parent / manuscript_file,
                workspace / manuscript_file,
            ]:
                if candidate.exists():
                    md_path = candidate
                    break
            else:
                return []

        if not md_path.exists():
            return []

        # Determine the slide page index from the filename (e.g. slide_03 -> 3)
        slide_num_match = re.search(r"(\d+)", html_path.stem)
        if slide_num_match is None:
            return []
        slide_idx = int(slide_num_match.group(1)) - 1  # 0-based

        with open(md_path, encoding="utf-8") as f:
            markdown = f.read()

        pages = [p for p in markdown.split("\n---\n") if p.strip()]
        if slide_idx < 0 or slide_idx >= len(pages):
            return []

        page_content = pages[slide_idx]

        # Extract image paths from the manuscript page
        md_images: list[str] = []
        for match in re.finditer(r"!?\[(.*?)\]\((.*?)\)", page_content):
            img_path = match.group(2).split()[0].strip("\"'")
            if re.match(r"https?://", img_path):
                continue  # skip external URLs
            md_images.append(img_path)

        if not md_images:
            return []

        # Extract image src paths from the HTML
        with open(html_path, encoding="utf-8") as f:
            html_content = f.read()

        html_images: set[str] = set()
        for match in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html_content):
            html_images.add(match.group(1))

        # Check coverage: each manuscript image should appear (by filename) in an HTML img tag
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

    async def inspect_slide(
        html_file: str,
        aspect_ratio: Literal["16:9", "4:3", "A1", "A2", "A3", "A4"] = "16:9",
        manuscript_file: str | None = None,
    ) -> ImageContent | str:
        """Validate the HTML slide file. If validation passes, proceed to the next slide;
        if it fails, fix the reported issues and re-validate.

        Args:
            html_file: Path to the HTML slide file to validate.
            aspect_ratio: Slide aspect ratio (default 16:9).
            manuscript_file: Path to the manuscript markdown file. If provided, checks that
                all images referenced in the corresponding manuscript page appear in the HTML.

        Returns:
            The slide as an image content (reflective mode only)
            or a validation result message
        """
        html_path = Path(html_file).absolute()
        assert html_path.is_file() and html_path.suffix == ".html", (
            f"HTML path {html_path} does not exist or is not an HTML file"
        )

        # Image coverage check (runs before PPTX conversion, fast and cheap)
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

        if reflective_design:
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
            return (
                f"Validation PASSED for {html_path.name}. This slide is valid. "
                f"Proceed to generate the next slide (slide_{int(slide_num)+1:02d}.html). "
                f"Do NOT rewrite this slide again."
            )

    def inspect_manuscript(md_file: str) -> dict[str, Any]:
        """Inspect the markdown manuscript for general statistics and image asset validation.

        Args:
            md_file: The path to the markdown file
        """
        md_path = Path(md_file)
        assert md_path.exists(), f"file does not exist: {md_file}"
        assert md_file.lower().endswith(".md"), f"file is not a markdown file: {md_file}"

        with open(md_file, encoding="utf-8") as f:
            markdown = f.read()

        pages = [p for p in markdown.split("\n---\n") if p.strip()]
        result = defaultdict(list)
        result["num_pages"] = len(pages)
        if lid_model is not None:
            label = lid_model.predict(markdown[:1000].replace("\n", " "))
            result["language"] = label[0][0].replace("__label__", "")
        else:
            result["language"] = "unknown"

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

    # ------------------------------------------------------------------
    # Tool agents (conditionally included)
    # ------------------------------------------------------------------

    tools: dict[str, Any] = {
        "todo_create": todo_create,
        "todo_update": todo_update,
        "todo_list": todo_list,
        "thinking": thinking,
        "finalize": finalize,
        "inspect_slide": inspect_slide,
        "inspect_manuscript": inspect_manuscript,
    }

    if config.t2i_model is not None:

        async def image_generation(prompt: str, width: int, height: int, path: str) -> str:
            """Generate an image and save it to the specified path.

            Args:
                prompt: Text description of the image to generate
                width: Width of the image in pixels
                height: Height of the image in pixels
                path: Full path where the image should be saved
            """
            response = await config.t2i_model.generate_image(
                prompt=prompt, width=width, height=height
            )

            image_b64 = response.data[0].b64_json
            image_url = response.data[0].url

            Path(path).parent.mkdir(parents=True, exist_ok=True)

            if image_b64:
                image_bytes = base64.b64decode(image_b64)
            elif image_url:
                async with httpx.AsyncClient() as client:
                    response = await client.get(image_url)
                    response.raise_for_status()
                    image_bytes = response.content
            else:
                raise ValueError("Empty Response")

            with open(path, "wb") as file:
                file.write(image_bytes)

            debug(
                f"Image generated: prompt='{prompt}', size=({width}x{height}), saved to '{path}'"
            )
            return "Image generated successfully, saved to " + path

        tools["image_generation"] = image_generation

    if config.vision_model is not None:
        _CAPTION_SYSTEM = """\
You are a helpful assistant that can describe the main content of the image in less than 50 words, avoiding unnecessary details or comments.
Additionally, classify the image as 'Table', 'Chart', 'Landscape', 'Diagram', 'Banner', 'Background', 'Icon', 'Logo', etc. or 'Picture' if it cannot be classified as one of the above.
Give your answer in the following format:
<type>:<description>
Example Output:
Chart: Bar graph showing quarterly revenue growth over five years. Color-coded bars represent different product lines. Notable spike in Q4 of the most recent year, with a dotted line indicating industry average for comparison
Now give your answer in one sentence only, without line breaks:
"""

        async def image_caption(image_path: str) -> dict:
            """Generate a caption for the image, including its type and a brief description.

            Args:
                image_path: The path to the image to caption.

            Returns:
                The caption and size for the image
            """
            assert Path(image_path).is_file(), f"Image path {image_path} does not exist"
            with Image.open(image_path) as img:
                img.verify()
                size = img.size
            with open(image_path, "rb") as f:
                image_b64 = (
                    f"data:image/jpeg;base64,{base64.b64encode(f.read()).decode('utf-8')}"
                )
            response = await config.vision_model.run(
                messages=[
                    {"role": "system", "content": _CAPTION_SYSTEM},
                    {
                        "role": "user",
                        "content": [{"type": "image_url", "image_url": {"url": image_b64}}],
                    },
                ],
            )

            debug(
                f"Image captioned: path='{image_path}', caption='{response.choices[0].message.content}'"
            )
            return {
                "size": size,
                "caption": response.choices[0].message.content,
            }

        tools["image_caption"] = image_caption

    return tools
