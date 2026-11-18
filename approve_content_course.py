"""Local operator command to approve one course for Canvas content authoring."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from canvas_mcp.server import ConfigurationError, MAX_WRITE_POLICY_BYTES, write_policy


def approve(path: Path, course_id: int) -> bool:
    if type(course_id) is not int or course_id <= 0:
        raise ValueError("Course ID must be a positive integer.")
    path = path.expanduser().absolute()
    # Serialize concurrent invocations even when the target inode is replaced.
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        return update_policy(path, course_id)
    finally:
        os.close(directory)


def update_policy(path: Path, course_id: int) -> bool:
    before = path.lstat()
    existing = write_policy(path)
    if not existing["enabled"]:
        raise ConfigurationError("Content writing is disabled in this policy.")
    if course_id in existing["approved_course_ids"]:
        return False
    # Preserve the on-disk schema. The connector normalizes older schemas on read.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_ctime_ns) != (before.st_dev, before.st_ino, before.st_ctime_ns):
            raise ConfigurationError("The policy changed before it could be updated.")
        raw = json.loads(stream.read(MAX_WRITE_POLICY_BYTES + 1))
    raw["approved_course_ids"].append(course_id)
    staged = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    temporary = Path(staged.name)
    try:
        with staged:
            json.dump(raw, staged, indent=2)
            staged.write("\n")
            staged.flush()
            os.fsync(staged.fileno())
        expected = dict(existing, approved_course_ids=[*existing["approved_course_ids"], course_id])
        if write_policy(temporary) != expected:
            raise ConfigurationError("Staged policy failed verification.")
        now = path.lstat()
        if (now.st_ino, now.st_ctime_ns) != (before.st_ino, before.st_ctime_ns):
            raise ConfigurationError("The policy changed during the update. Inspect it before retrying.")
        os.replace(temporary, path)
        if write_policy(path) != expected:
            raise ConfigurationError("Saved policy failed verification.")
        return True
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True, help="Existing private Canvas write policy")
    parser.add_argument("course_id", type=int)
    args = parser.parse_args()
    try:
        changed = approve(args.policy, args.course_id)
    except (OSError, ValueError, ConfigurationError) as exc:
        parser.exit(1, f"Policy update failed: {exc}\n")
    status = "Added" if changed else "Already approved"
    print(f"{status}: course {args.course_id}. Verified {args.policy}.")


if __name__ == "__main__":
    main()
