"""Full cumulative Git patches and a bounded read-only reviewer MCP tool."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from blacksmith.commands import run_command
from blacksmith.receipts import OperationKey, ReceiptError, digest, encoded
from blacksmith.review_receipts import ReviewPatch
from blacksmith.verification import _git, candidate_identity

INLINE_PATCH_BYTES = 128 * 1024
DIFF_READ_BYTES = 32 * 1024
DIFF_SERVER = "blacksmith-review"
DIFF_TOOL = "mcp__blacksmith-review__read_diff"


def hash_file(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            sha.update(block)
    return sha.hexdigest()


def verify_patch(patch, *, root=None):
    path = Path(patch.path)
    if (not path.is_absolute() or path.resolve() != path or path.is_symlink()
            or (root is not None and not path.is_relative_to(root))
            or not path.is_file() or path.stat().st_size != patch.bytes
            or hash_file(path) != patch.sha256):
        raise ReceiptError("missing/changed/foreign cumulative review patch")
    return path


def capture_patch(state, *, context=None):
    workspace = state["worktree_path"]
    candidate, tree, dirty = candidate_identity(workspace)
    base = state.get("unit_base_commit") or state.get("pre_implement_ref")
    if not base or dirty:
        raise ValueError("review requires a saved unit base and a clean committed candidate")
    base = _git(workspace, "rev-parse", "--verify", base + "^{commit}").strip()
    _git(workspace, "merge-base", "--is-ancestor", base, candidate)
    unit = state["selected_unit"]
    identity = digest(encoded([unit.id, base, candidate]))
    folder = context.directory / "reviews" / "patches" if context else Path(
        tempfile.mkdtemp(prefix="blacksmith-review-")).resolve()
    if folder.resolve() != folder or folder.is_symlink():
        raise ReceiptError("foreign cumulative review patch directory")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / (identity + ".patch")
    journal = context.journal() if context else None
    key = OperationKey(context.run_id, unit.id, identity, "review.patch") if context else None
    if journal:
        prior = journal.intent(key, {"base_commit": base, "candidate_commit": candidate,
                                     "patch_path": str(path)})
        if prior.status == "completed":
            patch = ReviewPatch.model_validate(prior.outcome["patch"])
            verify_patch(patch, root=context.directory / "reviews" / "patches")
            if patch.base_commit != base or patch.candidate_commit != candidate:
                raise ReceiptError("cumulative patch differs from critical intent")
            return patch
        if prior.status != "intent":
            raise ReceiptError("incomplete patch requires inspection")
    # Git writes the complete patch directly to a private artifact; stdout/stderr
    # still use the bounded command service. The patch is not a checkpoint/log tail.
    fd, temporary = tempfile.mkstemp(prefix=".patch-", dir=folder)
    os.close(fd)
    temporary = Path(temporary)
    run_command(["git", "-C", str(workspace), "diff", "--binary", "--full-index",
                 "--find-renames", "--no-ext-diff", "--no-textconv", "--no-color",
                 "--output=" + str(temporary), base + ".." + candidate, "--"],
                check=True).require_complete_output()
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    patch = ReviewPatch(base_commit=base, candidate_commit=candidate, path=str(path),
                        sha256=hash_file(temporary), bytes=temporary.stat().st_size)
    if path.exists() or path.is_symlink():
        verify_patch(patch)
        temporary.unlink()
    else:
        os.replace(temporary, path)
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    if candidate_identity(workspace) != (candidate, tree, ""):
        raise ValueError("review candidate changed while capturing patch")
    if journal:
        journal.finish(key, "completed", {"patch": patch.model_dump(mode="json")})
    return patch


def prompt_patch(patch):
    manifest = json.dumps(patch.model_dump(mode="json"), sort_keys=True)
    header = ("CUMULATIVE PATCH MANIFEST (unit base through exact gated candidate; "
              "includes deletions/renames/binary changes):\n" + manifest + "\n")
    if patch.bytes <= INLINE_PATCH_BYTES:
        return header + "FULL CUMULATIVE PATCH:\n" + verify_patch(patch).read_bytes().decode(
            "utf-8", errors="backslashreplace")
    return (header + "The full patch exceeds 128 KiB and is retained at the manifest path. "
            "Use read_diff with byte offsets and max_bytes <= 32768 until complete=true; "
            "follow next_offset. Also inspect relevant original deletions and renames. "
            "This prompt contains a manifest, not a truncated patch. Never claim a full "
            "review from the diffstat or current files alone.\n")


def make_read_diff_tool(patch):
    async def read_diff(args):
        try:
            offset, maximum = args.get("offset", 0), args.get("max_bytes", DIFF_READ_BYTES)
            if (type(offset) is not int or type(maximum) is not int
                    or not 0 <= offset <= patch.bytes or not 1 <= maximum <= DIFF_READ_BYTES):
                raise ValueError("offset must be within the patch; max_bytes must be 1..32768")
            path = verify_patch(patch)
            with path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read(maximum)
            result = {"sha256": patch.sha256, "total_bytes": patch.bytes,
                      "offset": offset, "next_offset": offset + len(data),
                      "complete": offset + len(data) == patch.bytes,
                      "encoding": "utf-8 with invalid bytes escaped",
                      "patch": data.decode("utf-8", errors="backslashreplace")}
            return {"content": [{"type": "text", "text": json.dumps(result)}]}
        except (OSError, ValueError, ReceiptError) as exc:
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}

    return tool("read_diff", "Read bounded bytes from the exact cumulative patch; "
                "follow next_offset until complete=true.",
                {"offset": int, "max_bytes": int})(read_diff)


def diff_server(patch):
    return create_sdk_mcp_server(name=DIFF_SERVER, tools=[make_read_diff_tool(patch)])
