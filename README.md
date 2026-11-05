# Jalan Bot

Bot personal de música para Discord con comandos slash y una cola independiente por servidor. Reproduce búsquedas y vídeos individuales de YouTube mediante yt-dlp y FFmpeg, sin guardar canciones en disco.

Los enlaces completos de **canciones individuales de Spotify** se consultan mediante oEmbed, sin claves de Spotify. El título se busca en YouTube: no se reproduce audio de Spotify y la coincidencia puede ser otra versión o artista. Si ocurre, usa `/play título artista` o el enlace exacto de YouTube. No se importan playlists, álbumes ni enlaces cortos de Spotify. Los enlaces de YouTube con una playlist reproducen únicamente el vídeo individual, si lo hay.

## Preparación en Windows

Necesitas Python 3.12 o posterior, FFmpeg y Node.js 22+ o Deno para la extracción de YouTube.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Instala el ejecutable de FFmpeg y añádelo a PATH, o indica su ruta completa en `FFMPEG_PATH` dentro de `.env`. Verifica `ffmpeg -version` y `node --version` (o `deno --version`). El paquete de Python llamado ffmpeg no sustituye al ejecutable.

1. Crea una aplicación en el [portal de desarrolladores de Discord](https://discord.com/developers/applications) y configura su usuario Bot.
2. Copia su token a `DISCORD_TOKEN` en `.env`. Mantén ese archivo privado; no pegues el token en el chat.
3. En OAuth2 / URL Generator selecciona `bot` y `applications.commands`. Permisos: View Channels, Send Messages, Connect y Speak. No necesita Administrator ni Message Content Intent.
4. Abre la URL generada e invita el bot al servidor.
5. Opcionalmente activa el modo desarrollador de Discord, copia el ID del servidor y guárdalo en `DISCORD_GUILD_ID` para registrar ahí los comandos inmediatamente. Sin ese ID se registran globalmente y pueden tardar en aparecer. Si cambias entre ambos modos, los comandos antiguos pueden permanecer registrados hasta que los elimines.
6. Arranca el proceso y entra en un canal de voz normal:

```powershell
.\.venv\Scripts\python.exe bot.py
```

## Uso

| Comando | Acción |
| --- | --- |
| `/play busqueda: título artista` | Busca y añade una canción |
| `/play busqueda: https://www.youtube.com/watch?v=...` | Añade ese vídeo |
| `/play busqueda: https://open.spotify.com/track/...` | Busca en YouTube el título publicado por Spotify |
| `/pause` / `/resume` | Pausa / reanuda |
| `/skip` | Salta la canción actual |
| `/queue` | Muestra la canción actual y hasta diez pendientes |
| `/stop` | Elimina la cola y desconecta |

Los controles requieren estar en el mismo canal de voz que el bot. Máximo 100 canciones pendientes por servidor. Tras 180 segundos sin canciones se desconecta; ajustable con `IDLE_TIMEOUT`. La cola está en memoria y se pierde al reiniciar. El ordenador debe permanecer encendido y el proceso en ejecución.

## Verificación y problemas frecuentes

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q bot.py music.py
```

- **YouTube no responde o pide iniciar sesión:** actualiza yt-dlp con `.\.venv\Scripts\python.exe -m pip install -U "yt-dlp[default]"` y comprueba Node/Deno. No hay garantía de acceso a todos los vídeos; no se incluye evasión de restricciones, DRM o bloqueos.
- **Entra al canal pero no se oye:** comprueba Speak, FFmpeg y que el bot no esté silenciado por el servidor. Se usa discord.py 2.7.1+ por su soporte de voz DAVE.
- **Comandos ausentes:** comprueba los scopes de invitación y `DISCORD_GUILD_ID`.
- **Spotify falla o devuelve una coincidencia incorrecta:** usa título y artista o un enlace de YouTube.

La extracción de YouTube depende de métodos no oficiales y puede romperse. El uso personal no cambia las condiciones de las plataformas ni los derechos sobre la música.

Referencias: [discord.py](https://discordpy.readthedocs.io/en/stable/), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [oEmbed de Spotify](https://developer.spotify.com/documentation/embeds/reference/oembed).
