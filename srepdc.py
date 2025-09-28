import discord
from discord.ext import commands
import os
import importlib.util
import asyncio

bot = commands.Bot(command_prefix='.', selfbot=True)

PLUGINS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plugins')
os.makedirs(PLUGINS_DIR, exist_ok=True)

PLUGINS = {}

def load_plugins():
    for fname in os.listdir(PLUGINS_DIR):
        if not fname.lower().endswith('.py'):
            continue
        path = os.path.join(PLUGINS_DIR, fname)
        name = os.path.splitext(fname)[0]
        try:
            spec = importlib.util.spec_from_file_location(f'plugins.{name}', path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            PLUGINS[name] = mod
        except Exception:
            continue

@bot.event
async def on_ready():
    load_plugins()
    print(f'Logged in as {bot.user}\nLoaded {len(PLUGINS)} plugins.')

@bot.event
async def on_message(message):
    for mod in list(PLUGINS.values()):
        handler = getattr(mod, 'on_message', None)
        if callable(handler):
            try:
                asyncio.create_task(handler(message, {'bot': bot, 'plugins_dir': PLUGINS_DIR}))
            except Exception:
                pass
    await bot.process_commands(message)

if __name__ == '__main__':
    token = 'x'
    bot.run(token)