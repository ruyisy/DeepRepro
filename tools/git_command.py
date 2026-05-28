#!/usr/bin/env python3
"""
GitHub Repository Downloader MCP Tool using FastMCP
"""

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import json
import urllib.parse
import urllib.request
import zipfile
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

from mcp.server import FastMCP

# Create FastMCP instance.
mcp = FastMCP("github-downloader")
MAX_REFERENCE_REPO_SIZE_KB = 200_000


class GitHubURLExtractor:
    """Extract GitHub URLs from model output."""

    @staticmethod
    def _strip_path_noise(value: str) -> str:
        return value.strip().strip('"\',. ')

    @staticmethod
    def extract_github_urls(text: str) -> List[str]:
        """Extract GitHub URLs from text."""
        selected_urls = GitHubURLExtractor.extract_selected_reference_urls(text)
        if selected_urls:
            return selected_urls

        text = GitHubURLExtractor.strip_verification_report(text)
        patterns = [
            # Standard HTTPS URL
            r"https?://github\.com/[\w\-\.]+/[\w\-\.]+(?:\.git)?",
            # SSH URL
            r"git@github\.com:[\w\-\.]+/[\w\-\.]+(?:\.git)?",
            # Short owner/repo format
            r"(?<!\S)(?<!/)(?<!\.)([\w\-\.]+/[\w\-\.]+)(?!/)(?!\S)",
        ]

        urls = []
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                # Handle tuple matches from regex groups.
                if isinstance(match, tuple):
                    match = match[0]

                # Normalize URL.
                if match.startswith("git@"):
                    url = match.replace("git@github.com:", "https://github.com/")
                elif match.startswith("http"):
                    url = match
                else:
                    # Convert short owner/repo form after basic validation.
                    if "/" in match and not any(
                        x in match for x in ["./", "../", "deeprepro_code", "tools"]
                    ):
                        parts = match.split("/")
                        if (
                            len(parts) == 2
                            and all(
                                part.replace("-", "").replace("_", "").isalnum()
                                for part in parts
                            )
                            and not any(part.startswith(".") for part in parts)
                        ):
                            url = f"https://github.com/{match}"
                        else:
                            continue
                    else:
                        continue

                # Normalize trailing .git and slash.
                if url.endswith(".git"):
                    url = url[:-4]
                url = url.rstrip("/")

                # Fix duplicated github.com segments.
                if "github.com/github.com/" in url:
                    url = url.replace("github.com/github.com/", "github.com/")

                urls.append(url)

        return list(dict.fromkeys(urls))  # keep order while deduplicating

    @staticmethod
    def extract_selected_reference_urls(text: str, max_urls: int = 5) -> List[str]:
        """Extract top selected_references repository URLs from analyzer JSON."""
        normalized = GitHubURLExtractor.strip_verification_report(text)
        decoder = json.JSONDecoder()
        candidates = []

        for match in re.finditer(r"\{", normalized):
            try:
                parsed, _ = decoder.raw_decode(normalized[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and isinstance(
                parsed.get("selected_references"), list
            ):
                candidates.append(parsed)

        if not candidates:
            return []

        selected = max(
            candidates,
            key=lambda item: len(item.get("selected_references", []) or []),
        )
        urls = []
        for reference in selected.get("selected_references", []):
            if not isinstance(reference, dict):
                continue
            github_info = reference.get("github_info", {})
            if not isinstance(github_info, dict):
                continue
            repo_url = str(github_info.get("repository_url", "")).strip()
            owner_repo = GitHubURLExtractor.parse_owner_repo(repo_url)
            if not owner_repo:
                continue
            normalized_url = _normalize_github_repo_url(repo_url)
            if normalized_url not in urls:
                urls.append(normalized_url)
            if len(urls) >= max_urls:
                break

        return urls

    @staticmethod
    def strip_verification_report(text: str) -> str:
        """Remove previously appended verification report before URL extraction."""
        normalized = text.replace("\r\n", "\n")
        marker = "\n\nGitHub URL verification:\n"
        if marker not in normalized:
            return text

        before, after = normalized.split(marker, 1)
        stop_markers = ["\n\nTARGET_DIR:", "\n\nTARGET_PATH:"]
        stop_positions = [after.find(marker) for marker in stop_markers if after.find(marker) >= 0]
        if stop_positions:
            return before + after[min(stop_positions) :]
        return before

    @staticmethod
    def extract_target_path(text: str) -> Optional[str]:
        """Extract target path from text."""
        explicit_match = re.search(
            r"(?:TARGET_PATH|TARGET_DIR|target_path|target_dir)\s*[:=]\s*([^\r\n]+)",
            text,
            re.IGNORECASE,
        )
        if explicit_match:
            explicit_path = GitHubURLExtractor._strip_path_noise(
                explicit_match.group(1)
            )
            if explicit_path:
                return explicit_path

        # Path indicator patterns
        patterns = [
            r'(?:to|into|in|at)\s+(?:folder|directory|path)?\s*["\']?([^\s"\']+)["\']?',
            r'(?:save|download|clone)\s+(?:to|into|at)\s+["\']?([^\s"\']+)["\']?',
            r'(?:到|在|保存到|下载到|克隆到)\s*["\']?([^\s"\']+)["\']?',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                path = GitHubURLExtractor._strip_path_noise(match.group(1))
                # Filter generic location words.
                if path and path.lower() not in [
                    "here",
                    "there",
                    "current",
                    "local",
                    "这里",
                    "当前",
                    "本地",
                ]:
                    return path

        return None

    @staticmethod
    def infer_repo_name(url: str) -> str:
        """Infer repository name from URL."""
        if url.endswith(".git"):
            url = url[:-4]
        if "github.com" in url:
            parts = url.split("/")
            if len(parts) >= 2:
                return parts[-1]
        return "repository"

    @staticmethod
    def parse_owner_repo(url: str) -> Optional[tuple[str, str]]:
        """Parse owner/repo from a normalized GitHub HTTPS URL."""
        normalized = url[:-4] if url.endswith(".git") else url
        match = re.match(r"https?://github\.com/([^/\s]+)/([^/\s]+)", normalized)
        if not match:
            return None
        owner = match.group(1)
        repo = match.group(2).rstrip("/").removesuffix(".git")
        if repo in {"tree", "blob", "commit", "releases", "issues", "pulls"}:
            return None
        return owner, repo


def _github_request_json(url: str, timeout: int = 15) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "DeepRepro"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _normalize_github_repo_url(url: str) -> str:
    owner_repo = GitHubURLExtractor.parse_owner_repo(url)
    if not owner_repo:
        return url.rstrip("/")
    owner, repo = owner_repo
    repo = repo.removesuffix(".git")
    return f"https://github.com/{owner}/{repo}"


def _repo_exists_sync(url: str) -> Tuple[bool, Optional[str]]:
    owner_repo = GitHubURLExtractor.parse_owner_repo(url)
    if not owner_repo:
        return False, "Not a GitHub repository URL"

    owner, repo = owner_repo
    api_url = f"https://api.github.com/repos/{owner}/{repo.removesuffix('.git')}"
    try:
        _github_request_json(api_url)
        return True, None
    except Exception as e:
        return False, str(e)


def _get_repo_metadata_sync(url: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    owner_repo = GitHubURLExtractor.parse_owner_repo(url)
    if not owner_repo:
        return None, "Not a GitHub repository URL"
    owner, repo = owner_repo
    try:
        return _github_request_json(f"https://api.github.com/repos/{owner}/{repo}"), None
    except Exception as e:
        return None, str(e)


def _is_plausible_replacement(
    item: Dict[str, Any], original_owner: str, original_repo: str
) -> bool:
    full_name = item.get("full_name", "").lower()
    repo_name = item.get("name", "").lower()
    description = (item.get("description") or "").lower()
    original_owner = original_owner.lower()
    original_repo = original_repo.lower()

    if len(original_repo) <= 3:
        return False

    if repo_name == original_repo:
        return True
    if original_repo in full_name:
        return True
    return original_repo in description


def _search_github_repo_sync(
    query: str, original_owner: str, original_repo: str
) -> Optional[str]:
    normalized_query = " ".join(query.split())
    if not normalized_query:
        return None

    search_url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {
            "q": f"{normalized_query} in:name",
            "sort": "stars",
            "order": "desc",
            "per_page": "5",
        }
    )
    try:
        data = _github_request_json(search_url)
    except Exception:
        return None

    for item in data.get("items", []):
        html_url = item.get("html_url", "")
        if not html_url:
            continue
        if _is_plausible_replacement(item, original_owner, original_repo):
            return _normalize_github_repo_url(html_url)

    return None


def _should_skip_large_repository_sync(url: str) -> Tuple[bool, str]:
    metadata, error = _get_repo_metadata_sync(url)
    if not metadata:
        return False, f"metadata unavailable: {error}"

    repo_size_kb = metadata.get("size")
    if isinstance(repo_size_kb, int) and repo_size_kb > MAX_REFERENCE_REPO_SIZE_KB:
        return (
            True,
            f"repository size {repo_size_kb} KB exceeds limit {MAX_REFERENCE_REPO_SIZE_KB} KB",
        )
    return False, ""


async def verify_and_correct_github_urls(text: str) -> Tuple[str, List[str]]:
    """Lightly verify GitHub URLs and correct obvious hallucinated repos."""
    extractor = GitHubURLExtractor()
    text_to_verify = extractor.strip_verification_report(text)
    urls = extractor.extract_github_urls(text_to_verify)
    if not urls:
        return text_to_verify, []

    corrected_text = text_to_verify
    report = []

    for url in urls:
        normalized_url = _normalize_github_repo_url(url)
        exists, error = await asyncio.to_thread(_repo_exists_sync, normalized_url)
        if exists:
            if normalized_url != url:
                corrected_text = corrected_text.replace(url, normalized_url)
            report.append(f"[verified] {normalized_url}")
            continue

        owner_repo = extractor.parse_owner_repo(normalized_url)
        repo_name = owner_repo[1] if owner_repo else extractor.infer_repo_name(url)
        owner_name = owner_repo[0] if owner_repo else ""
        replacement = await asyncio.to_thread(
            _search_github_repo_sync, repo_name, owner_name, repo_name
        )
        if replacement and replacement != normalized_url:
            corrected_text = corrected_text.replace(url, replacement)
            report.append(f"[corrected] {normalized_url} -> {replacement}")
        else:
            report.append(f"[unverified] {normalized_url} ({error or 'not found'})")

    return corrected_text, report


def _check_git_installed_sync() -> bool:
    """Synchronously check whether git is available."""
    git_executable = shutil.which("git")
    if not git_executable:
        return False

    try:
        completed = subprocess.run(
            [git_executable, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
        return completed.returncode == 0
    except Exception:
        return False


async def check_git_installed() -> bool:
    """Check whether git is available."""
    return await asyncio.to_thread(_check_git_installed_sync)


def _clone_repository_sync(repo_url: str, target_path: str) -> Dict[str, any]:
    """Synchronously run git clone."""
    try:
        cmd = [
            "git",
            "-c",
            "http.version=HTTP/1.1",
            "-c",
            "filter.lfs.smudge=",
            "-c",
            "filter.lfs.required=false",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            "--no-tags",
            repo_url,
            target_path,
        ]
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"},
            check=False,
        )
        return {
            "success": completed.returncode == 0,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _download_repository_zip_sync(repo_url: str, target_path: str) -> Dict[str, any]:
    """Download GitHub source zip as a fallback when git clone fails."""
    owner_repo = GitHubURLExtractor.parse_owner_repo(repo_url)
    if not owner_repo:
        return {"success": False, "error": "ZIP fallback only supports GitHub HTTPS URLs"}

    owner, repo = owner_repo
    parent_dir = os.path.dirname(target_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    errors = []
    branch_candidates = []
    metadata_url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        request = urllib.request.Request(
            metadata_url, headers={"User-Agent": "DeepRepro"}
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            metadata = json.load(response)
        default_branch = metadata.get("default_branch")
        if default_branch:
            branch_candidates.append(default_branch)
    except Exception as e:
        errors.append(f"metadata: {e}")

    for fallback_branch in ["main", "master"]:
        if fallback_branch not in branch_candidates:
            branch_candidates.append(fallback_branch)

    for branch in branch_candidates:
        zip_url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                zip_path = os.path.join(temp_dir, f"{repo}.zip")
                urllib.request.urlretrieve(zip_url, zip_path)

                extract_dir = os.path.join(temp_dir, "extract")
                with zipfile.ZipFile(zip_path) as archive:
                    archive.extractall(extract_dir)

                extracted_items = [
                    os.path.join(extract_dir, name) for name in os.listdir(extract_dir)
                ]
                if not extracted_items:
                    errors.append(f"{branch}: empty archive")
                    continue

                shutil.move(extracted_items[0], target_path)
                return {
                    "success": True,
                    "stdout": f"Downloaded GitHub source archive from {zip_url}",
                    "stderr": "",
                    "returncode": 0,
                    "method": "zip",
                }
        except Exception as e:
            errors.append(f"{branch}: {e}")

    return {"success": False, "error": "; ".join(errors), "method": "zip"}


async def clone_repository(repo_url: str, target_path: str) -> Dict[str, any]:
    """Clone a repository with git first and zip fallback."""
    git_result = await asyncio.to_thread(_clone_repository_sync, repo_url, target_path)
    if git_result.get("success"):
        git_result["method"] = "git"
        return git_result

    zip_result = await asyncio.to_thread(
        _download_repository_zip_sync, repo_url, target_path
    )
    if zip_result.get("success"):
        zip_result["git_error"] = git_result.get(
            "error", git_result.get("stderr", "Unknown git clone error")
        )
        return zip_result

    return {
        "success": False,
        "error": (
            f"git clone failed: {git_result.get('error', git_result.get('stderr', 'Unknown error'))}\n"
            f"zip fallback failed: {zip_result.get('error', 'Unknown error')}"
        ),
        "method": "git+zip",
    }



def resolve_clone_target_path(
    repo_url: str, target_path: Optional[str], multiple_repos: bool = False
) -> str:
    """Resolve the final clone path for one repository."""
    extractor = GitHubURLExtractor()
    repo_name = extractor.infer_repo_name(repo_url)

    if not target_path:
        return repo_name

    normalized_target = os.path.normpath(target_path)
    looks_like_directory = (
        multiple_repos
        or target_path.endswith("/")
        or target_path.endswith("\\")
        or os.path.isdir(normalized_target)
        or not os.path.splitext(os.path.basename(normalized_target))[1]
    )

    if looks_like_directory:
        return os.path.join(normalized_target, repo_name)

    return normalized_target

@mcp.tool()
async def download_github_repo(instruction: str) -> str:
    """
    Download GitHub repositories from natural language instructions.

    Args:
        instruction: Natural language text containing GitHub URLs and optional target paths

    Returns:
        Status message about the download operation

    Examples:
        - "Download https://github.com/openai/gpt-3"
        - "Clone microsoft/vscode to my-projects folder"
        - "Get https://github.com/facebook/react"
    """
    # Check whether Git is installed.
    if not await check_git_installed():
        return "[ERROR] Git is not installed or not in system PATH"

    extractor = GitHubURLExtractor()

    # Extract GitHub URLs.
    urls = extractor.extract_github_urls(instruction)
    if not urls:
        return "[ERROR] No GitHub URLs found in the instruction"

    corrected_instruction, verification_report = await verify_and_correct_github_urls(
        instruction
    )
    if corrected_instruction != instruction:
        instruction = corrected_instruction
        urls = extractor.extract_github_urls(instruction)

    # Extract target path.
    target_path = extractor.extract_target_path(instruction)

    # Download repositories.
    results = []
    for url in urls:
        try:
            should_skip, skip_reason = await asyncio.to_thread(
                _should_skip_large_repository_sync, url
            )
            if should_skip:
                results.append(
                    f"[SKIP] Skipped large repository: {url}\n"
                    f"   Reason: {skip_reason}"
                )
                continue

            final_path = resolve_clone_target_path(
                url, target_path, multiple_repos=len(urls) > 1
            )

            if not os.path.isabs(final_path):
                final_path = os.path.normpath(final_path)
                if final_path.startswith("/"):
                    final_path = final_path.lstrip("/")

            parent_dir = os.path.dirname(final_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            if os.path.exists(final_path):
                results.append(
                    f"[ERROR] Failed to download {url}: Target path already exists: {final_path}"
                )
                continue

            result = await clone_repository(url, final_path)

            if result["success"]:
                msg = f"[OK] Successfully downloaded: {url}\n"
                msg += f"   Location: {final_path}"
                if result.get("stdout"):
                    msg += f"\n   {result['stdout'].strip()}"
            else:
                msg = f"[ERROR] Failed to download: {url}\n"
                msg += f"   Error: {result.get('error', result.get('stderr', 'Unknown error'))}"

        except Exception as e:
            msg = f"[ERROR] Failed to download: {url}\n"
            msg += f"   Error: {str(e)}"

        results.append(msg)

    if verification_report:
        results.insert(
            0,
            "[INFO] GitHub URL verification:\n"
            + "\n".join(f"   {item}" for item in verification_report),
        )

    return "\n\n".join(results)


@mcp.tool()
async def parse_github_urls(text: str) -> str:
    """
    Extract GitHub URLs and target paths from text.

    Args:
        text: Text containing GitHub URLs

    Returns:
        Parsed GitHub URLs and target path information
    """
    extractor = GitHubURLExtractor()

    urls = extractor.extract_github_urls(text)
    target_path = extractor.extract_target_path(text)

    content = "[INFO] Parsed information:\n\n"

    if urls:
        content += "GitHub URLs found:\n"
        for url in urls:
            content += f"  - {url}\n"
    else:
        content += "No GitHub URLs found\n"

    if target_path:
        content += f"\nTarget path: {target_path}"
    else:
        content += "\nTarget path: Not specified (will use repository name)"

    return content


@mcp.tool()
async def git_clone(
    repo_url: str, target_path: Optional[str] = None, branch: Optional[str] = None
) -> str:
    """
    Clone a specific GitHub repository.

    Args:
        repo_url: GitHub repository URL
        target_path: Optional target directory path
        branch: Optional branch name to clone

    Returns:
        Status message about the clone operation
    """
    # Check whether Git is installed.
    if not await check_git_installed():
        return "[ERROR] Git is not installed or not in system PATH"

    # Prepare target path.
    if not target_path:
        extractor = GitHubURLExtractor()
        target_path = extractor.infer_repo_name(repo_url)

    # Convert to absolute path.
    if not os.path.isabs(target_path):
        target_path = str(Path.cwd() / target_path)

    # Check target path.
    if os.path.exists(target_path):
        return f"[ERROR] Target path already exists: {target_path}"

    # 鏋勫缓鍛戒护
    cmd = [
        "git",
        "-c",
        "http.version=HTTP/1.1",
        "-c",
        "filter.lfs.smudge=",
        "-c",
        "filter.lfs.required=false",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--no-tags",
    ]
    if branch:
        cmd.extend(["-b", branch])
    cmd.extend([repo_url, target_path])

    def _run_clone() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"},
            check=False,
        )

    try:
        proc = await asyncio.to_thread(_run_clone)

        if proc.returncode == 0:
            result = "[OK] Successfully cloned repository\n"
            result += f"Repository: {repo_url}\n"
            result += f"Location: {target_path}"
            if branch:
                result += f"\nBranch: {branch}"
            return result
        return f"[ERROR] Clone failed\nError: {proc.stderr}"

    except Exception as e:
        return f"[ERROR] Clone failed\nError: {str(e)}"


# Main entry point
if __name__ == "__main__":
    mcp.run()
