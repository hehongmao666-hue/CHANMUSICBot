# ==============================================================================
# _queue.py - Queue Manager
# ==============================================================================
# In-memory queues for all chats.
#
# Scaling optimizations:
# - Normal dict instead of defaultdict: read-only lookups never create entries.
# - Empty queues are removed completely.
# - Peek only copies the small requested window.
# ==============================================================================

from collections import deque
from typing import Union

from ._dataclass import Media, Track

MediaItem = Union[Media, Track]


class Queue:
    def __init__(self):
        self.queues: dict[int, deque[MediaItem]] = {}

    def add(self, chat_id: int, item: MediaItem) -> int:
        q = self.queues.setdefault(chat_id, deque())
        q.append(item)
        return len(q) - 1

    def check_item(
        self,
        chat_id: int,
        item_id: str
    ) -> tuple[int, MediaItem | None]:
        q = self.queues.get(chat_id)
        if not q:
            return -1, None

        for i, track in enumerate(q):
            if track.id == item_id:
                return i, track

        return -1, None

    def force_add(
        self,
        chat_id: int,
        item: MediaItem,
        remove: int | bool = False
    ) -> None:
        self.remove_current(chat_id)
        q = self.queues.setdefault(chat_id, deque())
        q.appendleft(item)

        if remove:
            q.rotate(-remove)
            q.popleft()
            q.rotate(remove)

        if not q:
            self.queues.pop(chat_id, None)

    def get_current(self, chat_id: int) -> MediaItem | None:
        q = self.queues.get(chat_id)
        return q[0] if q else None

    def get_next(
        self,
        chat_id: int,
        check: bool = False
    ) -> MediaItem | None:
        q = self.queues.get(chat_id)

        if not q:
            return None

        if check:
            return q[1] if len(q) > 1 else None

        q.popleft()

        if not q:
            self.queues.pop(chat_id, None)
            return None

        return q[0]

    def get_queue(self, chat_id: int) -> list[MediaItem]:
        q = self.queues.get(chat_id)
        return list(q) if q else []

    def get_all(self, chat_id: int) -> list[MediaItem]:
        return self.get_queue(chat_id)

    def remove_current(self, chat_id: int) -> None:
        q = self.queues.get(chat_id)
        if not q:
            return

        q.popleft()

        if not q:
            self.queues.pop(chat_id, None)

    def clear(self, chat_id: int) -> None:
        self.queues.pop(chat_id, None)

    def peek_next(
        self,
        chat_id: int,
        count: int = 2
    ) -> list[MediaItem]:
        q = self.queues.get(chat_id)

        if not q or len(q) <= 1:
            return []

        # Only the small requested window is copied.
        end = min(len(q), count + 1)
        return list(q)[:end][1:]

    @staticmethod
    def is_downloaded(item: MediaItem) -> bool:
        return bool(getattr(item, "file_path", None))
