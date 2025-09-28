import os
import json
import asyncio
import datetime

CACHE = {}
INIT = False

def _now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"

def _safe_load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _safe_save(path, obj):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass

async def _handle_raw_delete(payload, logs_path):
    mid = payload.message_id
    chan = payload.channel_id
    entry = None
    cached = CACHE.pop(mid, None)
    if cached:
        entry = {
            "message_id": mid,
            "channel_id": chan,
            "guild_id": getattr(payload, "guild_id", None),
            "author_name": cached.get("author_name"),
            "author_id": cached.get("author_id"),
            "content": cached.get("content"),
            "created_at": cached.get("created_at"),
            "is_reply": bool(cached.get("reply_to")),
            "reply_to": cached.get("reply_to"),
            "deleted_at": _now_iso()
        }
    else:
        entry = {
            "message_id": mid,
            "channel_id": chan,
            "guild_id": getattr(payload, "guild_id", None),
            "author_name": None,
            "author_id": None,
            "content": None,
            "created_at": None,
            "is_reply": False,
            "reply_to": None,
            "deleted_at": _now_iso()
        }
    logs = _safe_load(logs_path)
    ch_key = str(chan)
    lst = logs.get(ch_key, [])
    lst.append(entry)
    logs[ch_key] = lst
    _safe_save(logs_path, logs)

async def _ensure_listeners(bot, plugins_dir):
    global INIT
    if INIT:
        return
    INIT = True
    logs_path = os.path.join(plugins_dir or ".", "delogs.json")
    async def on_raw_message_delete(payload):
        await _handle_raw_delete(payload, logs_path)
    bot.add_listener(on_raw_message_delete, "on_raw_message_delete")

async def on_message(message, api):
    await _ensure_listeners(api.get("bot"), api.get("plugins_dir"))
    try:
        mid = getattr(message, "id", None)
        if mid:
            CACHE[mid] = {
                "author_name": getattr(message.author, "display_name", None) or getattr(message.author, "name", None),
                "author_id": getattr(message.author, "id", None),
                "content": getattr(message, "content", None),
                "created_at": getattr(message, "created_at", None).isoformat() + "Z" if getattr(message, "created_at", None) else None,
                "reply_to": None
            }
            ref = getattr(message, "reference", None)
            if ref and getattr(ref, "message_id", None):
                rid = ref.message_id
                rcache = CACHE.get(rid)
                if rcache:
                    CACHE[mid]["reply_to"] = {"message_id": rid, "author_name": rcache.get("author_name"), "author_id": rcache.get("author_id"), "content": rcache.get("content")}
                else:
                    CACHE[mid]["reply_to"] = {"message_id": rid}
    except Exception:
        pass
    content = (message.content or "").strip()
    lowered = content.lower()
    invoked = None
    if lowered.startswith(".delogs") or lowered == ".delogs" or lowered.startswith("delogs") or lowered == "delogs":
        invoked = content
    if not invoked:
        return
    parts = invoked.split()
    page = 1
    if len(parts) >= 2:
        try:
            page = int(parts[1])
            if page < 1:
                page = 1
        except Exception:
            page = 1
    logs_path = os.path.join(api.get("plugins_dir") or ".", "delogs.json")
    logs = _safe_load(logs_path)
    ch_key = str(message.channel.id)
    lst = logs.get(ch_key, [])
    if not lst:
        try:
            await message.reply("```text\n[no deleted messages]\n```", mention_author=False)
        except Exception:
            try:
                await message.channel.send("```text\n[no deleted messages]\n```")
            except Exception:
                pass
        return
    lst_rev = list(reversed(lst))
    per_page = 10
    start = (page - 1) * per_page
    end = start + per_page
    page_items = lst_rev[start:end]
    lines = []
    for i, e in enumerate(page_items, start=start + 1):
        a_name = e.get("author_name") or "[unknown]"
        a_id = e.get("author_id") or "[unknown]"
        when = e.get("deleted_at") or "[unknown]"
        content_snip = e.get("content") or "[no content cached]"
        if len(content_snip) > 400:
            content_snip = content_snip[:400] + "...(truncated)"
        line = f"{i}. {a_name} ({a_id}) at {when}\n{content_snip}"
        if e.get("is_reply"):
            rt = e.get("reply_to") or {}
            rt_name = rt.get("author_name") or "[unknown]"
            rt_id = rt.get("author_id") or rt.get("message_id") or "[unknown]"
            line += f"\nreply to: {rt_name} ({rt_id})"
        lines.append(line)
    header = f"```text\ndeleted messages page {page} ({len(lst)} total)\n```"
    body = "```\n" + "\n\n".join(lines) + "\n```"
    out = header + "\n" + body
    try:
        await message.reply(out, mention_author=False)
    except Exception:
        try:
            await message.channel.send(out)
        except Exception:
            pass