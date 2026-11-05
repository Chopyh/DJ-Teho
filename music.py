"""Resolución de canciones y reproducción independiente por servidor."""
import asyncio
from dataclasses import dataclass
import logging
import os
import re
import shlex
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import discord
import yt_dlp

log = logging.getLogger(__name__)


def safe_message(message):
    message = re.sub(r"https?://\S+", "[URL omitida]", str(message))
    token = os.getenv("DISCORD_TOKEN", "")
    if token:
        message = message.replace(token, "[TOKEN omitido]")
    return message


class SafeFormatter(logging.Formatter):
    def format(self, record):
        return safe_message(super().format(record))


class ExtractorLogger:
    def debug(self, message):
        pass

    def warning(self, message):
        log.warning("yt-dlp: %s", safe_message(message))

    def error(self, message):
        log.error("yt-dlp: %s", safe_message(message))


class SourceError(RuntimeError):
    pass


@dataclass(repr=False)
class StreamSource:
    url: str
    headers: dict


def ffmpeg_input_options(headers):
    # Solo cabeceras de reproducción; nunca enviar cookies de Google a otros dominios.
    allowed = {"user-agent", "referer", "origin", "accept", "accept-language"}
    lines = []
    for key, value in headers.items():
        if key.lower() in allowed and not any(c in str(value) for c in "\r\n\x00"):
            lines.append(f"{key}: {value}\r\n")
    options = "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    if lines:
        options += " -headers " + shlex.quote("".join(lines))
    return options


class FFmpegLog:
    """Destino sin fileno: discord.py recoge stderr en un hilo y lo envía aquí."""
    def __init__(self):
        self.pending = b""
        self.failed = False

    def write(self, data):
        self.pending += data
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            self.emit(line)
        if not data and self.pending:
            self.emit(self.pending)
            self.pending = b""
        return len(data)

    def emit(self, line):
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            self.failed = True  # FFmpeg se ejecuta con loglevel=error.
            log.error("FFmpeg: %s", safe_message(text))


def validate_cookie_file():
    path = os.getenv("YTDLP_COOKIES_FILE", "").strip()
    if path:
        try:
            with Path(path).open(encoding="utf-8") as file:
                header = file.readline().lstrip("\ufeff").strip()
        except OSError:
            raise ValueError("YTDLP_COOKIES_FILE no existe o no es legible por el usuario del contenedor.") from None
        if header not in {"# Netscape HTTP Cookie File", "# HTTP Cookie File"}:
            raise ValueError("El archivo de cookies debe tener formato Netscape, no JSON.")
    return path


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


def extract(query: str, *, validate_stream=False) -> dict:
    options = {
        "format": "bestaudio/best", "noplaylist": True, "quiet": True,
        "no_warnings": False, "socket_timeout": 20, "retries": 2,
        "cachedir": False, "js_runtimes": {"deno": {}, "node": {}},
        "logger": ExtractorLogger(),
    }
    if validate_stream:
        # yt-dlp prueba la descarga y descarta formatos que devuelven errores.
        # La lista es perezosa: no hace falta comprobar todos los formatos.
        options["check_formats"] = True
    # yt-dlp reescribe su cookiefile al cerrar: cada extracción usa una copia
    # privada y escribible para admitir secretos montados en solo lectura.
    with tempfile.TemporaryDirectory(prefix="jalan-yt-") as directory:
        cookie_file = validate_cookie_file()
        if cookie_file:
            copied = Path(directory) / "cookies.txt"
            shutil.copyfile(cookie_file, copied)
            copied.chmod(0o600)
            options["cookiefile"] = str(copied)
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(query, download=False)
                if info and "entries" in info:
                    info = next((item for item in info["entries"] if item), None)
                if not info or not info.get("webpage_url") or not info.get("url"):
                    raise ValueError("No se ha encontrado una canción reproducible.")
                if validate_stream:
                    log.info("Audio validado por yt-dlp: formato=%s protocolo=%s extractor=%s",
                             info.get("format_id", "?"), info.get("protocol", "?"), info.get("extractor", "?"))
                return info
        except yt_dlp.utils.DownloadError as error:
            text = str(error).lower()
            if "confirm" in text and ("not a bot" in text or "not a robot" in text):
                raise SourceError(
                    "YouTube pide verificar la sesión. Revisa YTDLP_COOKIES_FILE en Dokploy; "
                    "si ya está configurado, la sesión puede haber caducado o YouTube seguir rechazando el servidor."
                ) from None
            if validate_stream and ("403" in text or "requested format is not available" in text):
                raise SourceError(
                    "YouTube no ha ofrecido un formato de audio accesible al validar la descarga. "
                    "Consulta los logs de yt-dlp; no es un fallo de la conexión de voz."
                ) from None
            raise SourceError("YouTube no ha permitido obtener el audio. Consulta los logs de yt-dlp.") from None


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

    async def stream(self, track: Track) -> StreamSource:
        # Resolver al empezar cada canción evita usar enlaces de audio caducados.
        async with self.slots:
            info = await asyncio.to_thread(extract, track.url, validate_stream=True)
        return StreamSource(info["url"], info.get("http_headers") or {})


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
            log.exception("No se pudo enviar una notificación de reproducción.")

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
                    stream = await self.resolver.stream(track)
                    ffmpeg_log = FFmpegLog()
                    source = discord.FFmpegOpusAudio(
                        stream.url, executable=self.ffmpeg,
                        before_options=ffmpeg_input_options(stream.headers),
                        options="-vn -loglevel error",
                        stderr=ffmpeg_log,
                    )

                    def after(error):
                        if error:
                            log.error("Error de audio: %s", safe_message(error))
                        loop.call_soon_threadsafe(self.finished.set)

                    self.voice.play(source, after=after)
                    source = None  # VoiceClient se ocupa de liberar el proceso.
                    await self.notify(channel, f"▶️ {track.title[:250]}\n{track.url}")
                    await self.finished.wait()
                    if ffmpeg_log.failed:
                        raise SourceError("FFmpeg no ha podido leer el audio. Consulta los errores de FFmpeg en los logs.")
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    log.exception("Fallo de reproducción: servidor=%s canción=%s",
                                  getattr(getattr(self.voice, "guild", None), "id", "?"), track.title[:150])
                    detail = str(error) if isinstance(error, SourceError) else "No he podido reproducir esa canción."
                    await self.notify(channel, detail + " Continuaré con la siguiente.")
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
