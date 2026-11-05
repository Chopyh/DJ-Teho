import asyncio
import logging
import os
import shutil
import sys

import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv

from music import (Player, Resolver, SafeFormatter, SourceError, validate_cookie_file,
                   AudioDiagnostics, test_tone, prepare_voice)

load_dotenv()


class MusicBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.tree = app_commands.CommandTree(self)
        self.players = {}
        self.locks = {}

    async def setup_hook(self):
        self.session = aiohttp.ClientSession()
        self.resolver = Resolver(self.session)
        guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def close(self):
        await asyncio.gather(*(p.close() for p in self.players.values()), return_exceptions=True)
        if hasattr(self, "session"):
            await self.session.close()
        await super().close()


bot = MusicBot()


@bot.event
async def on_ready():
    logging.getLogger("discord").info("Jalan Bot conectado como %s (ID: %s)", bot.user, bot.user.id)


@bot.event
async def on_voice_state_update(member, before, after):
    if bot.user and member.id == bot.user.id and before.channel and not after.channel:
        player = bot.players.get(member.guild.id)
        if player and not player.closed:
            await player.close()


def member_channel(interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        raise ValueError("Usa este comando dentro de un servidor.")
    if not interaction.user.voice or not interaction.user.voice.channel:
        raise ValueError("Entra primero en un canal de voz.")
    channel = interaction.user.voice.channel
    if isinstance(channel, discord.StageChannel):
        raise ValueError("Usa un canal de voz normal, no un escenario.")
    voice = interaction.guild.voice_client
    if voice and voice.is_connected() and voice.channel.id != channel.id:
        raise ValueError("Debes estar en el mismo canal de voz que el bot.")
    return channel


def active_player(interaction):
    member_channel(interaction)
    player = bot.players.get(interaction.guild_id)
    if not player or player.closed:
        raise ValueError("No hay ninguna cola activa. Usa /play.")
    return player


@bot.tree.command(name="play", description="Reproduce un nombre, vídeo de YouTube o canción de Spotify")
@app_commands.guild_only()
@app_commands.describe(busqueda="Nombre y artista o enlace completo de una canción")
async def play(interaction: discord.Interaction, busqueda: str):
    member_channel(interaction)
    await interaction.response.defer(thinking=True)
    track, spotify = await bot.resolver.resolve(busqueda, interaction.user.display_name)
    lock = bot.locks.setdefault(interaction.guild_id, asyncio.Lock())
    async with lock:
        channel = member_channel(interaction)  # Volver a comprobar tras la búsqueda.
        player = bot.players.get(interaction.guild_id)
        if player is None or player.closed or not player.voice.is_connected():
            if player and not player.closed:
                await player.close()
            voice = interaction.guild.voice_client
            if not voice or not voice.is_connected():
                voice = await channel.connect(timeout=30, reconnect=True)
            player = Player(voice, bot.resolver, os.getenv("FFMPEG_PATH", "ffmpeg"),
                            int(os.getenv("IDLE_TIMEOUT", "180")))
            bot.players[interaction.guild_id] = player
        try:
            player.queue.put_nowait((track, interaction.channel))
        except asyncio.QueueFull:
            raise ValueError("La cola está llena (100 canciones).") from None
    message = f"Añadida: **{discord.utils.escape_markdown(track.title[:250])}**\n{track.url}"
    if spotify:
        message += "\nCoincidencia de YouTube a partir del título de Spotify; comprueba que sea la versión que querías."
    await interaction.followup.send(message)


@bot.tree.command(name="pause", description="Pausa la reproducción")
@app_commands.guild_only()
async def pause(interaction: discord.Interaction):
    player = active_player(interaction)
    if not player.voice.is_playing():
        raise ValueError("No hay audio sonando ahora mismo.")
    player.voice.pause()
    await interaction.response.send_message("⏸️ En pausa.")


@bot.tree.command(name="testaudio", description="Prueba la voz con un tono de tres segundos, sin YouTube")
@app_commands.guild_only()
async def testaudio(interaction: discord.Interaction):
    member_channel(interaction)
    await interaction.response.defer()
    lock = bot.locks.setdefault(interaction.guild_id, asyncio.Lock())
    async with lock:
        channel = member_channel(interaction)
        player = bot.players.get(interaction.guild_id)
        if player and not player.closed:
            raise ValueError("Usa /stop antes de /testaudio para detener la cola.")
        voice = interaction.guild.voice_client
        if voice and voice.is_connected():
            raise ValueError("Ya hay una conexión de voz activa. Espera a que termine la prueba.")
        voice = await channel.connect(timeout=30, reconnect=True)
        source = None
        try:
            state = interaction.guild.me.voice
            logging.getLogger(__name__).info(
                "Prueba de voz: servidor=%s canal=%s mute_servidor=%s suprimido=%s discord.py=%s",
                interaction.guild_id, channel.id, getattr(state, "mute", None),
                getattr(state, "suppress", None), discord.__version__,
            )
            source = AudioDiagnostics(test_tone())
            diagnostic = source
            loop = asyncio.get_running_loop()

            async def finish(error):
                logging.getLogger(__name__).info("Fin de prueba de voz: paquetes_generados=%s error=%s",
                                                diagnostic.frames, error is not None)
                if error:
                    logging.getLogger(__name__).error("Error en prueba de voz: %s", error)
                await voice.disconnect(force=True)

            def after(error):
                loop.call_soon_threadsafe(lambda: asyncio.create_task(finish(error)))

            await prepare_voice(voice)
            voice.play(source, after=after)
            source = None
        except BaseException:
            if source:
                source.cleanup()
            await voice.disconnect(force=True)
            raise
    await interaction.followup.send("🔊 Prueba: un tono suave de tres segundos, sin YouTube. Al terminar me desconecto.")


@bot.tree.command(name="resume", description="Reanuda la reproducción")
@app_commands.guild_only()
async def resume(interaction: discord.Interaction):
    player = active_player(interaction)
    if not player.voice.is_paused():
        raise ValueError("La reproducción no está pausada.")
    player.voice.resume()
    await interaction.response.send_message("▶️ Reanudada.")


@bot.tree.command(name="skip", description="Salta la canción actual")
@app_commands.guild_only()
async def skip(interaction: discord.Interaction):
    active_player(interaction).skip()
    await interaction.response.send_message("⏭️ Canción saltada.")


@bot.tree.command(name="queue", description="Muestra la canción actual y las próximas diez")
@app_commands.guild_only()
async def queue(interaction: discord.Interaction):
    player = active_player(interaction)
    title = player.current.title if player.current else "Preparando reproducción…"
    lines = [f"Ahora: {title[:150]}"]
    pending = list(player.queue._queue)
    lines += [f"{i}. {track.title[:100]}" for i, (track, _) in enumerate(pending[:10], 1)]
    lines.append(f"Pendientes: {len(pending)}")
    await interaction.response.send_message(discord.utils.escape_markdown("\n".join(lines)))


@bot.tree.command(name="stop", description="Vacía la cola y desconecta el bot")
@app_commands.guild_only()
async def stop(interaction: discord.Interaction):
    await interaction.response.defer()
    lock = bot.locks.setdefault(interaction.guild_id, asyncio.Lock())
    async with lock:
        player = active_player(interaction)
        await player.close()
        bot.players.pop(interaction.guild_id, None)
    await interaction.followup.send("⏹️ Cola eliminada y bot desconectado.")


@bot.tree.error
async def on_command_error(interaction, error):
    original = getattr(error, "original", error)
    if not isinstance(original, ValueError):
        logging.getLogger(__name__).error(
            "Fallo de comando: comando=%s servidor=%s interacción=%s",
            getattr(interaction.command, "name", "?"), interaction.guild_id, interaction.id,
            exc_info=(type(original), original, original.__traceback__),
        )
    if isinstance(original, (ValueError, SourceError)):
        message = str(original)
    elif isinstance(original, discord.Forbidden):
        message = "Me faltan permisos. Comprueba Ver canal, Conectar, Hablar y Enviar mensajes."
    else:
        message = "No he podido completar la operación. Comprueba el enlace, la conexión y las dependencias."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def main():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(SafeFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise SystemExit("LOG_LEVEL debe ser DEBUG, INFO, WARNING, ERROR o CRITICAL.")
    logging.basicConfig(level=level, handlers=[handler], force=True)
    logging.getLogger("discord.http").setLevel(logging.INFO)
    try:
        cookie_file = validate_cookie_file()
    except ValueError as error:
        raise SystemExit(str(error)) from None
    logging.getLogger(__name__).info("Inicio del bot; cookies de YouTube: %s", "configuradas" if cookie_file else "no configuradas")
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("Falta DISCORD_TOKEN. Copia .env.example a .env y configura el token.")
    if not shutil.which(os.getenv("FFMPEG_PATH", "ffmpeg")):
        raise SystemExit("No se encuentra FFmpeg. Instálalo o configura FFMPEG_PATH en .env.")
    if int(os.getenv("IDLE_TIMEOUT", "180")) < 10:
        raise SystemExit("IDLE_TIMEOUT debe ser de al menos 10 segundos.")
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
