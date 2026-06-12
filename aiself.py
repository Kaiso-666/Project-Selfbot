import os
import re
import json
import asyncio
import importlib
import datetime
from pathlib import Path
from typing import Optional, Tuple, Set
from collections import deque

import aiohttp
import discord

# Ensure data files exist before importing them
WHITELIST_FILE = Path("whitelist_table.py")
if not WHITELIST_FILE.exists():
    WHITELIST_FILE.write_text(
        "ALLOWED_USER_IDS = set()\nDISABLED_USER_IDS = set()\nMANAGER_USER_IDS = set()\n",
        encoding="utf-8"
    )

SYSPROMPT_FILE = Path("sysprompt.py")
if not SYSPROMPT_FILE.exists():
    default_prompt = (
        "MAX_CHAR_LIMIT = 512\n\n"
        "SYSTEM_PROMPT = (\n"
        '    "You are a Discord bot. Reply naturally, relevantly, and concisely based on the conversation history."\n'
        ")\n"
    )
    SYSPROMPT_FILE.write_text(default_prompt, encoding="utf-8")

import whitelist_table
import sysprompt

# --- API KEYS AND TOKENS ---
TOKEN = "x.y.z"
GROQ_API_KEY = "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"  #exampleapi1
apikey_2 = "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"  #exampleapi2

MEMORY_FILE = "memory.json"  
MAX_PER_CHANNEL = 50  

def parse_user_id(raw: str) -> Optional[int]:
    raw = raw.strip()
    match = re.fullmatch(r"<@!?(\d+)>", raw)
    if match:
        return int(match.group(1))
    if raw.isdigit():
        return int(raw)
    return None

class GroqSelfBot(discord.Client):
    def __init__(self):
        super().__init__()
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.allowed_ids: Set[int] = set()
        self.disabled_ids: Set[int] = set()
        self.manager_ids: Set[int] = set()
        self.memory = {}

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()

    async def close(self):
        if self.http_session is not None:
            await self.http_session.close()
        await super().close()

    def load_memory(self):  
        if not os.path.isfile(MEMORY_FILE):  
            self.memory = {}  
            return  
        try:  
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:  
                raw = json.load(f)  
            for cid, msgs in raw.items():  
                self.memory[int(cid)] = deque(msgs, maxlen=MAX_PER_CHANNEL)  
        except:  
            self.memory = {}  
  
    async def persist_memory_periodically(self, interval=60):  
        await self.wait_until_ready()  
        while not self.is_closed():  
            try:  
                to_save = {}  
                for cid, dq in self.memory.items():  
                    to_save[str(cid)] = list(dq)  
                tmp = MEMORY_FILE + ".tmp"  
                with open(tmp, "w", encoding="utf-8") as f:  
                    json.dump(to_save, f, ensure_ascii=False, indent=2)  
                os.replace(tmp, MEMORY_FILE)  
            except:  
                pass  
            await asyncio.sleep(interval)  

    def add_to_memory(self, channel_id, message_record):  
        if channel_id not in self.memory:  
            self.memory[channel_id] = deque(maxlen=MAX_PER_CHANNEL)  
        self.memory[channel_id].append(message_record)  

    async def on_ready(self):
        self.load_memory()
        self.loop.create_task(self.persist_memory_periodically())
        print(f"Logged in as selfbot user: {self.user} ({self.user.id})")

    def reload_configs(self):
        """Reloads both configuration files dynamically on every message."""
        global whitelist_table, sysprompt
        try:
            whitelist_table = importlib.reload(whitelist_table)
        except Exception:
            pass
        try:
            sysprompt = importlib.reload(sysprompt)
        except Exception:
            pass
        self.allowed_ids = set(getattr(whitelist_table, "ALLOWED_USER_IDS", set()))
        self.disabled_ids = set(getattr(whitelist_table, "DISABLED_USER_IDS", set()))
        self.manager_ids = set(getattr(whitelist_table, "MANAGER_USER_IDS", set()))

    async def write_whitelist_file(self):
        """Writes the whitelist file, resolving user IDs to names for easy manual editing."""
        allowed_sorted = sorted({int(x) for x in self.allowed_ids})
        disabled_sorted = sorted({int(x) for x in self.disabled_ids})
        managers_sorted = sorted({int(x) for x in self.manager_ids})

        async def build_set_string(id_list):
            if not id_list:
                return "set()"
            lines = "{\n"
            for uid in id_list:
                user = self.get_user(uid)
                if not user:
                    try:
                        user = await self.fetch_user(uid)
                    except (discord.NotFound, discord.HTTPException):
                        user = None
                name = getattr(user, 'name', 'Unknown User')
                lines += f"    {uid}, # {name}\n"
            lines += "}"
            return lines

        allowed_str = await build_set_string(allowed_sorted)
        disabled_str = await build_set_string(disabled_sorted)
        manager_str = await build_set_string(managers_sorted)
        content = (
            "# Auto-generated by the bot.\n"
            "# Edit manually if you want, or use commands:\n"
            "# #addwhitelist / #removewhitelist (Managers Only)\n"
            "# #enableai / #disableai (Self-service for Whitelisted Users)\n\n"
            f"ALLOWED_USER_IDS = {allowed_str}\n\n"
            f"DISABLED_USER_IDS = {disabled_str}\n\n"
            f"MANAGER_USER_IDS = {manager_str}\n"
        )
        WHITELIST_FILE.write_text(content, encoding="utf-8")

    async def fetch_groq_response(self, channel_id: int, prompt: str, username: str) -> str:
        """Fetches response from Groq API utilizing conversation history."""
        global GROQ_API_KEY
        
        system_ctx = getattr(sysprompt, "SYSTEM_PROMPT", "You're an AI assistant.")
        history = self.memory.get(channel_id, deque())
        
        messages = [{"role": "system", "content": system_ctx}]  
        for m in list(history)[-MAX_PER_CHANNEL:]:  
            role = "assistant" if m["author_id"] == self.user.id else "user"  
            messages.append({"role": role, "content": f"{m['author_name']}: {m['content']}"})  
        messages.append({"role": "user", "content": f"{username}: {prompt}"})  
        
        async def send_request(api_key):  
            url = "https://api.groq.com/openai/v1/chat/completions"  
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}  
            payload = {"model": "llama-3.3-70b-versatile", "messages": messages}
            
            assert self.http_session is not None
            async with self.http_session.post(
                url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15)
            ) as r:  
                text = await r.text()  
                if r.status != 200:  
                    raise Exception(f"HTTP {r.status}: {text}")  
                data = await r.json()  
                return data["choices"][0]["message"]["content"]  
  
        try:  
            return await send_request(GROQ_API_KEY)  
        except Exception as e1:  
            try:  
                GROQ_API_KEY = apikey_2  
                return await send_request(GROQ_API_KEY)  
            except Exception as e2:  
                return f"Error switching API keys: {e1} / {e2}"  

    async def send_ai_reply(self, message: discord.Message, prompt: str, max_chars: int = 2000):
        """Sends a placeholder message and overwrites it with the formatted response once it returns."""
        reply_msg = await message.reply("> ✨ Generating Response...", mention_author=False)
        
        try:
            full_text = await self.fetch_groq_response(message.channel.id, prompt, message.author.display_name)
            raw_text = full_text.strip()
            if not raw_text:
                raw_text = "I could not generate a response."
            
            # Prepend "> " to the start of the message and after every newline
            quoted_text = "\n".join(f"> {line}" for line in raw_text.splitlines())
            
            # Completed informational footer using Discord small text layout
            footer = "\n\n-# ⓘ This message is AI Generated (Groq), Mistakes may occur. Please verify critical information."
            
            display_text = f"{quoted_text}{footer}"
            edited_msg = await reply_msg.edit(content=display_text[:max_chars+len(footer)+2])
            
            # Save Assistant output to memory
            self.add_to_memory(message.channel.id, {  
                "id": edited_msg.id,  
                "author_id": self.user.id,  
                "author_name": self.user.name,  
                "content": raw_text,  
                "created_at": edited_msg.created_at.isoformat(),  
                "attachments": []  
            })
                
        except Exception as e:
            try:
                await reply_msg.edit(content=f"Groq request failed: `{e}`")
            except discord.HTTPException:
                pass

    async def get_referenced_message(self, message: discord.Message):
        if not message.reference or not message.reference.message_id:
            return None
        if message.reference.resolved and isinstance(message.reference.resolved, discord.Message):
            return message.reference.resolved
        try:
            return await message.channel.fetch_message(message.reference.message_id)
        except Exception:
            return None

    async def on_message(self, message: discord.Message):
        if self.user is None or message.author.bot:
            return

        # Fetch latest configurations
        self.reload_configs()
        content = message.content.strip()
        lower = content.lower()
        is_owner = (message.author.id == self.user.id)

        # --- USER PREFERENCE TOGGLES ---
        if lower == "#enableai":
            if message.author.id not in self.allowed_ids and not is_owner:
                await message.reply("You are not on the bot's whitelist. Contact a manager.", mention_author=False)
                return
            if message.author.id in self.disabled_ids:
                self.disabled_ids.discard(message.author.id)
                await self.write_whitelist_file()
                await message.reply("AI replies are now **enabled**.", mention_author=False)
            else:
                await message.reply("AI is already enabled.", mention_author=False)
            return

        if lower == "#disableai":
            if message.author.id not in self.allowed_ids and not is_owner:
                await message.reply("You are not on the bot's whitelist.", mention_author=False)
                return
            if message.author.id not in self.disabled_ids:
                self.disabled_ids.add(message.author.id)
                await self.write_whitelist_file()
                await message.reply("AI replies are now **disabled**.", mention_author=False)
            else:
                await message.reply("AI is already disabled.", mention_author=False)
            return

        # --- MANAGER COMMANDS ---
        if lower.startswith("#addwhitelist"):
            if message.author.id not in self.manager_ids and not is_owner:
                return
            arg = content[len("#addwhitelist"):].strip()
            target_id = parse_user_id(arg)
            if target_id is None:
                await message.reply("Usage: `#addwhitelist <mention-or-id>`", mention_author=False)
                return
            self.allowed_ids.add(target_id)
            await self.write_whitelist_file()
            await message.reply(f"Added `{target_id}` to the whitelist.", mention_author=False)
            return

        if lower.startswith("#removewhitelist"):
            if message.author.id not in self.manager_ids and not is_owner:
                return
            arg = content[len("#removewhitelist"):].strip()
            target_id = parse_user_id(arg)
            if target_id is None:
                await message.reply("Usage: `#removewhitelist <mention-or-id>`", mention_author=False)
                return
            self.allowed_ids.discard(target_id)
            self.disabled_ids.discard(target_id)
            await self.write_whitelist_file()
            await message.reply(f"Removed `{target_id}` from the whitelist.", mention_author=False)
            return

        # --- SELFBOT/OWNER ONLY COMMANDS ---
        if lower.startswith("#askai"):
            if not is_owner:
                return
            raw_args = content[len("#askai"):].strip()
            parts = raw_args.split(maxsplit=1)
            if len(parts) < 2:
                await message.reply("Usage: `#askai <max_chars> <your prompt>`", mention_author=False)
                return
            length_str, ai_question = parts[0], parts[1]
            if not length_str.isdigit():
                await message.reply("Error: Character length limit must be a valid number.", mention_author=False)
                return
            max_chars = int(length_str)
            ai_prompt = (
                f"{ai_question}\n\n"
                f"[System Directive: Please ensure your overall response length is "
                f"strictly under {max_chars} characters.]"
            )
            
            # Save Owner tracking history turn before executing
            self.add_to_memory(message.channel.id, {
                "id": message.id,
                "author_id": message.author.id,
                "author_name": message.author.display_name,
                "content": ai_question,
                "created_at": message.created_at.isoformat(),
                "attachments": [a.url for a in message.attachments]
            })
            await self.send_ai_reply(message, ai_prompt, max_chars=max_chars)
            return

        if lower.startswith("#purge"):
            if not is_owner:
                return

            raw_args = content[len("#purge"):].strip()
            target_datetime = None

            if raw_args:
                match = re.match(r"-t\s+(\d{1,2}):(\d{2})", raw_args, re.IGNORECASE)
                if match:
                    try:
                        h = int(match.group(1))
                        m = int(match.group(2))
                        if not (0 <= h <= 23 and 0 <= m <= 59):
                            await message.reply("Invalid time layout. Use HH:MM (00:00 to 23:59).", mention_author=False)
                            return
                        
                        target_datetime = message.created_at.replace(hour=h, minute=m, second=0, microsecond=0)
                        if target_datetime > message.created_at:
                            target_datetime -= datetime.timedelta(days=1)
                    except Exception:
                        await message.reply("Usage: `#purge` or `#purge -t hh:mm`", mention_author=False)
                        return
                else:
                    await message.reply("Usage: `#purge` or `#purge -t hh:mm`", mention_author=False)
                    return

            try:
                await message.delete()
            except Exception:
                pass

            try:
                async for msg in message.channel.history(limit=None):
                    if target_datetime and msg.created_at < target_datetime:
                        break
                    if msg.author.id == self.user.id:
                        try:
                            await msg.delete()
                        except Exception:
                            pass
            except Exception as e:
                print(f"Error during execution of purge command: {e}")
            return

        # --- AI REPLY GATEKEEPING ---
        if is_owner:
            return
        referenced = await self.get_referenced_message(message)
        if referenced is None or referenced.author.id != self.user.id:
            return
        if message.author.id not in self.allowed_ids:
            return
        if message.author.id in self.disabled_ids:
            return

        user_text = content or "[no text]"
        char_limit = getattr(sysprompt, "MAX_CHAR_LIMIT", 512)
        if len(user_text) > char_limit:
            user_text = user_text[:char_limit]

        # Save incoming authorized user turn to memory
        self.add_to_memory(message.channel.id, {
            "id": message.id,
            "author_id": message.author.id,
            "author_name": message.author.display_name,
            "content": user_text,
            "created_at": message.created_at.isoformat(),
            "attachments": [a.url for a in message.attachments]
        })
        
        await self.send_ai_reply(message, user_text, max_chars=2000)

def main():
    if not TOKEN or TOKEN == "YOUR_DISCORD_USER_TOKEN_HERE":
        raise RuntimeError("DISCORD_BOT_TOKEN is not set properly.")
    bot = GroqSelfBot()
    bot.run(TOKEN)

if __name__ == "__main__":
    main()
