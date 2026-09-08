"""Short-lived cross-process mutex for native workflow advancement, not UI control."""
from contextlib import contextmanager
import os

from jobagent.infra import state
from jobagent.infra.browser_work import BrowserWorkError


@contextmanager
def command_lock():
    directory = state.STATE_DIR / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "native-command.lock"
    if path.is_symlink():
        raise BrowserWorkError("native_command_lock_invalid", "Preserve the invalid native command lock for recovery.")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BrowserWorkError("native_command_busy", "Another native workflow command is advancing state; inspect status after it finishes.") from exc
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BrowserWorkError("native_command_busy", "Another native workflow command is advancing state; inspect status after it finishes.") from exc
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        # Never unlink a flock inode: waiters may still hold a descriptor to it.
