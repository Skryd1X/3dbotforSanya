import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message, InputMediaPhoto
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

OWNER_ID: Optional[int] = None
PHOTO_TTL = timedelta(minutes=10)
DELETE_ORIGINAL_PHOTO = True
DELETE_ARCHIVE = True

ALLOWED_EXTENSIONS = {
    "zip",
    "rar",
    "7z",
    "stl",
    "obj",
    "3mf",
    "step",
    "stp",
    "3ds",
    "fbx",
}


@dataclass(slots=True)
class PhotoGroup:
    media_group_id: Optional[str]
    file_ids: List[str]
    message_ids: List[int]
    created_at: datetime


class PhotoArchiveBot:
    def __init__(self, bot: Bot, dispatcher: Dispatcher) -> None:
        self.bot = bot
        self.dp = dispatcher
        self.groups: Dict[Tuple[int, Optional[int]], List[PhotoGroup]] = {}
        self._register_handlers()

    def _key(self, message: Message) -> Tuple[int, Optional[int]]:
        return message.chat.id, message.message_thread_id

    def _skip_user(self, message: Message) -> bool:
        if OWNER_ID is None:
            return False
        if message.from_user is None:
            return True
        return message.from_user.id != OWNER_ID

    def _queue(self, key: Tuple[int, Optional[int]]) -> List[PhotoGroup]:
        if key not in self.groups:
            self.groups[key] = []
        return self.groups[key]

    def _cleanup(self, key: Tuple[int, Optional[int]]) -> None:
        queue = self.groups.get(key)
        if not queue:
            return
        now = datetime.utcnow()
        while queue and (now - queue[0].created_at) > PHOTO_TTL:
            queue.pop(0)
        if not queue:
            self.groups.pop(key, None)

    async def _handle_photo(self, message: Message) -> None:
        if self._skip_user(message):
            return

        key = self._key(message)
        queue = self._queue(key)
        media_group_id = message.media_group_id
        file_id = message.photo[-1].file_id
        now = datetime.utcnow()

        if media_group_id and queue and queue[-1].media_group_id == media_group_id:
            group = queue[-1]
            group.file_ids.append(file_id)
            group.message_ids.append(message.message_id)
        else:
            queue.append(
                PhotoGroup(
                    media_group_id=media_group_id,
                    file_ids=[file_id],
                    message_ids=[message.message_id],
                    created_at=now,
                )
            )

        self._cleanup(key)

    async def _handle_document(self, message: Message) -> None:
        if self._skip_user(message):
            return

        doc = message.document
        if not doc or not doc.file_name:
            return

        filename = doc.file_name
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return

        key = self._key(message)
        queue = self.groups.get(key)
        if not queue:
            return

        self._cleanup(key)
        queue = self.groups.get(key)
        if not queue:
            return

        group = queue[0]
        if group.message_ids and group.message_ids[-1] >= message.message_id:
            return

        queue.pop(0)

        if len(group.file_ids) == 1:
            sent = await self.bot.send_photo(
                chat_id=message.chat.id,
                photo=group.file_ids[0],
                caption=filename,
                message_thread_id=message.message_thread_id,
            )
            new_ids = [sent.message_id]
        else:
            media = [InputMediaPhoto(media=fid) for fid in group.file_ids]
            media[0].caption = filename
            sent_messages = await self.bot.send_media_group(
                chat_id=message.chat.id,
                media=media,
                message_thread_id=message.message_thread_id,
            )
            new_ids = [m.message_id for m in sent_messages]

        if DELETE_ORIGINAL_PHOTO:
            for mid in group.message_ids:
                try:
                    await self.bot.delete_message(chat_id=message.chat.id, message_id=mid)
                except Exception:
                    pass

        if DELETE_ARCHIVE:
            try:
                await self.bot.delete_message(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                )
            except Exception:
                pass

        if key in self.groups and not self.groups[key]:
            self.groups.pop(key, None)

    def _register_handlers(self) -> None:
        self.dp.message.register(self._handle_photo, F.photo)
        self.dp.channel_post.register(self._handle_photo, F.photo)
        self.dp.message.register(self._handle_document, F.document)
        self.dp.channel_post.register(self._handle_document, F.document)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher()
    PhotoArchiveBot(bot, dp)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
