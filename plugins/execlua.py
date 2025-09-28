# plugins/execlua.py
import asyncio
import os
import io
import tempfile
from lupa import LuaRuntime
import discord

lua = LuaRuntime(unpack_returned_tuples=True, register_eval=False)
lua.globals()['os'] = os
lua.globals()['io'] = io

exec_outputs = {}
reply_groups = {}

async def run_lua_code(code):
    output_buffer = []
    def lua_print(*args):
        output_buffer.append(' '.join(str(a) for a in args))
    lua.globals()['print'] = lua_print
    try:
        result = lua.execute(code)
        if result is not None:
            output_buffer.append(str(result))
    except Exception as e:
        output_buffer.append('Lua Error: ' + str(e))
    out = '\n'.join(output_buffer)
    if len(out) > 1900:
        out = out[:1900] + '\n...output truncated...'
    return out

async def build_combined_reply_for(reply_id, channel):
    inputs = reply_groups.get(reply_id, set())
    parts = []
    for in_id in list(inputs):
        try:
            in_msg = await channel.fetch_message(in_id)
        except Exception:
            inputs.discard(in_id)
            exec_outputs.pop(in_id, None)
            continue
        content = in_msg.content
        lower = content.lower()
        if lower.startswith('execlua '):
            code = content[8:]
            out = await run_lua_code(code)
            parts.append(f'--- input {in_id} (lua) ---\n{out}')
    final = '\n\n'.join(parts) if parts else '[no tracked inputs]'
    if len(final) > 1900:
        final = final[:1900] + '\n...output truncated...'
    header = f'```text\nExecution requested by: {channel.guild.me if getattr(channel, "guild", None) else "bot"}\n```'
    body = f'```text\n{final}\n```'
    try:
        ch = await channel.fetch_message(reply_id) if hasattr(channel, 'fetch_message') else await channel.fetch_message(reply_id)
        await ch.edit(content=header + '\n' + body)
    except Exception:
        try:
            chobj = channel if hasattr(channel, 'send') else None
            if chobj:
                await chobj.send(header + '\n' + body)
        except Exception:
            pass
    reply_groups[reply_id] = inputs

async def attach_input_to_reply(input_msg, reply_msg):
    exec_outputs[input_msg.id] = (input_msg.channel.id, reply_msg.id)
    s = reply_groups.get(reply_msg.id)
    if s is None:
        reply_groups[reply_msg.id] = {input_msg.id}
    else:
        s.add(input_msg.id)
    await build_combined_reply_for(reply_msg.id, input_msg.channel)

async def on_message(message, api):
    if message.author.id != api['bot'].user.id and message.content.lower().startswith('execlua '):
        return
    if message.content.lower().startswith('execlua '):
        ref = getattr(message, 'reference', None)
        reply_msg = None
        if ref and getattr(ref, 'message_id', None):
            try:
                referenced = await message.channel.fetch_message(ref.message_id)
                if referenced.author.id == api['bot'].user.id:
                    reply_msg = referenced
            except Exception:
                reply_msg = None
        if reply_msg is None:
            header = f'```text\nExecution requested by: {message.author} ({message.author.id})\n```'
            placeholder = '```text\n[processing]\n```'
            try:
                sent = await message.reply(header + '\n' + placeholder, mention_author=False)
                reply_msg = sent
            except Exception:
                try:
                    sent = await message.channel.send(header + '\n' + placeholder)
                    reply_msg = sent
                except Exception:
                    reply_msg = None
        if reply_msg is None:
            return
        await attach_input_to_reply(message, reply_msg)