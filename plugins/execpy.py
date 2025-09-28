# plugins/execpy.py
import asyncio
import os
import tempfile
import subprocess
import resource
import discord

exec_outputs = {}
reply_groups = {}

def find_venv_python(vfs_root):
    candidate_unix = os.path.join(vfs_root, 'bin', 'python')
    candidate_win = os.path.join(vfs_root, 'Scripts', 'python.exe')
    if os.path.exists(candidate_unix) and os.access(candidate_unix, os.X_OK):
        return candidate_unix
    if os.path.exists(candidate_win) and os.access(candidate_win, os.X_OK):
        return candidate_win
    return os.sys.executable

def preexec_limits():
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    except Exception:
        pass

def run_py_code_sync(code, vfs_root):
    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.py', dir=vfs_root if vfs_root else None)
    os.close(tmp_fd)
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(code)
        python_exec = find_venv_python(vfs_root or '')
        cmd = [python_exec, tmp_path]
        try:
            completed = subprocess.run(cmd, cwd=vfs_root or None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=6, preexec_fn=preexec_limits)
            out = completed.stdout
            err = completed.stderr
        except subprocess.TimeoutExpired:
            out = ''
            err = 'Execution timed out'
        except Exception as e:
            out = ''
            err = 'Run error: ' + str(e)
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
    combined = ''
    if out:
        combined += out
    if err:
        if combined:
            combined += '\n'
        combined += err
    if not combined:
        combined = '[no output]'
    if len(combined) > 1900:
        combined = combined[:1900] + '\n...output truncated...'
    return combined

async def build_combined_reply_for(reply_id, channel, vfs_root):
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
        if lower.startswith('execpy '):
            code = content[7:]
            out = run_py_code_sync(code, vfs_root)
            parts.append(f'--- input {in_id} (py) ---\n{out}')
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

async def attach_input_to_reply(input_msg, reply_msg, vfs_root):
    exec_outputs[input_msg.id] = (input_msg.channel.id, reply_msg.id)
    s = reply_groups.get(reply_msg.id)
    if s is None:
        reply_groups[reply_msg.id] = {input_msg.id}
    else:
        s.add(input_msg.id)
    await build_combined_reply_for(reply_msg.id, input_msg.channel, vfs_root)

async def on_message(message, api):
    if message.author.id != api['bot'].user.id and message.content.lower().startswith('execpy '):
        return
    if message.content.lower().startswith('execpy '):
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
        await attach_input_to_reply(message, reply_msg, api.get('plugins_dir'))