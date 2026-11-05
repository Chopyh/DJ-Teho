"""Resolución de canciones y reproducción independiente por servidor."""
import asyncio
from dataclasses import dataclass
import logging
import re
from urllib.parse import urlparse

import aiohttp
import discord
import yt_dlp

log = logging.getLogger(__name__)


@dataclass
class Track:
    title: str
    url: str
    requester: str


def normalize_query(query: str) -> tuple[str, bool]:
    query = query.strip().strip("<>")
    if not query or len(query) > 500:
        raise ValueError("Introduce un nombre o enlace de hasta 500 caracteres.")
    if query.startswith("spotify:"):
        match = re.fullmatch(r"spotify:track:([A-Za-z0-9]{22})", query)
        if not match:
            raise ValueError("De Spotify se admiten canciones individuales, no álbumes ni playlists.")
        return f"https://open.spotify.com/track/{match[1]}", True
    parsed = urlparse(query)
    if parsed.scheme or "://" in query:
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ValueError("Usa un enlace HTTPS de YouTube o Spotify.")
        host = (parsed.hostname or "").lower()
        if host == "open.spotify.com":
            if not re.fullmatch(r"/(?:intl-[a-z-]+/)?track/[A-Za-z0-9]{22}/?", parsed.path):
                raise ValueError("De Spotify se admiten enlaces completos de canciones individuales.")
            return f"https://open.spotify.com/track/{parsed.path.rstrip('/').split('/')[-1]}", True
        if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}:
            raise ValueError("Solo se admiten enlaces de YouTube y Spotify, o búsquedas por nombre.")
        # Rechazar puertos alternativos y mantener la extracción limitada a YouTube.
        if parsed.port not in {None, 80, 443}:
            raise ValueError("El enlace tiene un puerto no admitido.")
        return query, False
    return f"ytsearch1:{query}", False


def extract(query: str) -> dict:
    options = {
        "format": "bestaudio/best", "noplaylist": True, "quiet": True,
        "no_warnings": True, "socket_timeout": 20, "retries": 2,
        "cachedir": False, "js_runtimes": {"deno": {}, "node": {}},
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(query, download=False)
        if info and "entries" in info:
            info = next((item for item in info["entries"] if item), None)
        if not info or not info.get("webpage_url") or not info.get("url"):
            raise ValueError("No se ha encontrado una canción reproducible.")
        return info


class Resolver:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.slots = asyncio.Semaphore(2)

    async def resolve(self, query: str, requester: str) -> tuple[Track, bool]:
        query, spotify = normalize_query(query)
        if spotify:
            async with self.session.get(
                "https://open.spotify.com/oembed", params={"url": query},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                response.raise_for_status()
                data = await response.json()
            title = data.get("title")
            if not title:
                raise ValueError("Spotify no ha devuelto el título. Prueba con título y artista.")
            query = f"ytsearch1:{title} audio"
        async with self.slots:
            info = await asyncio.to_thread(extract, query)
        return Track(info["title"], info["webpage_url"], requester), spotify

    async def stream(self, track: Track) -> str:
        # Resolver al empezar cada canción evita usar enlaces de audio caducados.
        async with self.slots:
            info = await asyncio.to_thread(extract, track.url)
        return info["url"]


class Player:
    def __init__(self, voice, resolver, ffmpeg="ffmpeg", idle_timeout=180):
        self.voice = voice
        self.resolver = resolver
        self.ffmpeg = ffmpeg
        self.idle_timeout = idle_timeout
        self.queue = asyncio.Queue(maxsize=100)
        self.current = None
        self.closed = False
        self.finished = asyncio.Event()
        self.task = asyncio.create_task(self.run())

    async def notify(self, channel, message):
        try:
            await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.warning("No se pudo enviar una notificación de reproducción.")

    async def run(self):
        loop = asyncio.get_running_loop()
        try:
            while not self.closed:
                try:
                    track, channel = await asyncio.wait_for(self.queue.get(), self.idle_timeout)
                except asyncio.TimeoutError:
                    return
                self.current = track
                self.finished.clear()
                source = None
                try:
                    url = await self.resolver.stream(track)
                    source = discord.FFmpegOpusAudio(
                        url, executable=self.ffmpeg,
                        before_options="-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                        options="-vn -loglevel error",
                    )

                    def after(error):
                        if error:
                            log.warning("La reproducción ha terminado con un error de audio.")
                        loop.call_soon_threadsafe(self.finished.set)

                    self.voice.play(source, after=after)
                    source = None  # VoiceClient se ocupa de liberar el proceso.
                    await self.notify(channel, f"▶️ {track.title[:250]}\n{track.url}")
                    await self.finished.wait()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # No registrar URLs de streaming ni respuestas que puedan contener credenciales.
                    log.warning("No se pudo reproducir una canción.")
                    await self.notify(channel, "No he podido reproducir esa canción. Continuaré con la siguiente.")
                finally:
                    if source:
                        source.cleanup()
                    self.current = None
                    self.queue.task_done()
        finally:
            self.closed = True
            self.voice.stop()
            if self.voice.is_connected():
                await self.voice.disconnect(force=True)

    def skip(self):
        if self.current is None:
            raise ValueError("No hay ninguna canción en reproducción.")
        if not (self.voice.is_playing() or self.voice.is_paused()):
            raise ValueError("La canción aún se está preparando. Prueba de nuevo en unos segundos.")
        self.voice.stop()

    async def close(self):
        self.closed = True
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        # Una tarea cancelada antes de su primer turno no ejecuta su finally.
        if self.voice.is_connected():
            self.voice.stop()
            await self.voice.disconnect(force=True)
