# Jalan Bot

Bot personal de música para Discord con comandos slash y una cola independiente por servidor. Reproduce búsquedas y vídeos individuales de YouTube mediante yt-dlp y FFmpeg, sin guardar canciones en disco.

Los enlaces completos de **canciones individuales de Spotify** se consultan mediante oEmbed, sin claves de Spotify. El título se busca en YouTube: no se reproduce audio de Spotify y la coincidencia puede ser otra versión o artista. Si ocurre, usa `/play título artista` o el enlace exacto de YouTube. No se importan playlists, álbumes ni enlaces cortos de Spotify. Los enlaces de YouTube con una playlist reproducen únicamente el vídeo individual, si lo hay.

## Desplegar en Dokploy

El `Dockerfile` incluye Python, FFmpeg, Node.js 22 y las dependencias de voz. No necesitas instalar esas herramientas en el servidor. El proceso se ejecuta con un usuario sin privilegios y escribe los logs en la salida del contenedor.

1. Sube el proyecto a un repositorio Git accesible desde Dokploy, sin incluir `.env` ni `.venv`.
2. En tu proyecto de Dokploy crea una **Application** y conecta ese repositorio y la rama que quieras desplegar.
3. Selecciona **Build Type → Dockerfile** y configura:
   - **Dockerfile Path:** `Dockerfile`
   - **Docker Context Path:** `.`
   - **Docker Build Stage:** vacío (usa la etapa final).
4. En **Environment** configura estas variables de ejecución y guarda:

   ```dotenv
   DISCORD_TOKEN=tu_token_real
   DISCORD_GUILD_ID=id_de_tu_servidor_de_discord
   FFMPEG_PATH=/usr/bin/ffmpeg
   IDLE_TIMEOUT=180
   ```

   `DISCORD_GUILD_ID` es opcional. El token se configura en el entorno de ejecución de Dokploy, nunca como Build Argument ni dentro del Dockerfile. `.dockerignore` excluye los archivos locales del contexto de construcción.

5. Mantén **una sola réplica**. No configures dominio, puertos publicados ni comprobaciones de salud HTTP: este bot no es un servidor web. No necesita base de datos ni volúmenes.
6. Pulsa **Deploy**. Revisa los logs: al conectarse debe aparecer `Jalan Bot conectado como ...`. En Discord entra en un canal de voz y prueba `/play`.

El servidor debe permitir conexiones salientes HTTPS/WebSocket, DNS y UDP para la voz de Discord. No se necesita publicar un puerto entrante del contenedor. Si tu proveedor restringe UDP saliente, el bot podría conectarse a Discord sin llegar a reproducir audio.

Al reiniciar o desplegar se pierde la cola en memoria y se interrumpe la reproducción. Evita que dos despliegues del mismo bot se ejecuten simultáneamente; si usas actualizaciones con solapamiento, configura la parada del anterior antes de iniciar el nuevo. Mantén habilitado el reinicio del servicio ante fallos.

Para actualizar yt-dlp, vuelve a construir sin caché la imagen en Dokploy: una reconstrucción que reutilice la capa de instalación conservará la versión anterior. No hace falta modificar el contenedor en ejecución.

Referencias: [Dockerfile en Dokploy](https://docs.dokploy.com/docs/core/applications/build-type), [aplicaciones y variables de entorno](https://docs.dokploy.com/docs/core/applications).

### Prueba con Docker fuera de Dokploy

Con Docker instalado, configura primero `.env` y ejecuta desde la raíz del repositorio:

```sh
docker build -t jalan-bot .
docker run -d --name jalan-bot --restart unless-stopped --env-file .env jalan-bot
docker logs -f jalan-bot
```

En Linux deja `FFMPEG_PATH=ffmpeg` o `/usr/bin/ffmpeg`, nunca una ruta de Windows. El contenedor usa SIGINT para que Python cierre las conexiones al detenerlo, y Tini recoge los procesos hijos de FFmpeg.

## Preparación local en Windows

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

## Logs en Dokploy

Abre la sección de logs de la aplicación para consultar la salida del contenedor. `LOG_LEVEL=INFO` muestra fecha, nivel, componente, conexión y errores. Para más detalle configura `LOG_LEVEL=DEBUG` y vuelve a desplegar. Los errores de comandos incluyen el comando, ID del servidor e ID de interacción; los de reproducción incluyen la canción y el traceback. Las advertencias de yt-dlp también se registran.

El logger de Python oculta las URLs y el valor de `DISCORD_TOKEN`. Los errores de FFmpeg pasan por el logger con el prefijo `FFmpeg:` y sus URLs también se ocultan; revisa los logs antes de compartirlos. No se guardan archivos de log dentro de la imagen: consulta la retención configurada en tu servidor/Dokploy.

La reproducción conserva las cabeceras de yt-dlp (User-Agent, Referer y otras cabeceras de reproducción), sin reenviar cookies ni Authorization a FFmpeg. Un aviso de formatos SABR omitidos no es necesariamente fatal si hay otro formato disponible. Si la extracción funciona pero FFmpeg devuelve 403, el servidor de audio ha rechazado la descarga: las cabeceras son un posible factor, pero también pueden intervenir restricciones de YouTube o del formato seleccionado.

### YouTube: «Sign in to confirm you're not a bot»

Significa que YouTube está rechazando las peticiones y pide verificar la sesión; no es un fallo del token de Discord. Primero reconstruye sin caché para actualizar yt-dlp. Si persiste, puedes proporcionar una sesión propia mediante cookies:

1. Exporta **solo las cookies de youtube.com** en formato Netscape, siguiendo la [guía de yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies). No uses JSON ni un archivo con las cookies de todas tus páginas.
2. Guarda el archivo de forma privada en el servidor, fuera del repositorio. En los montajes de Dokploy añade un archivo (bind mount) hacia `/run/secrets/youtube-cookies.txt`, en **solo lectura**. El usuario del contenedor tiene UID `10001`: asegúrate de que puede leer el archivo sin hacerlo público.
3. Añade en Environment:

   ```dotenv
   YTDLP_COOKIES_FILE=/run/secrets/youtube-cookies.txt
   LOG_LEVEL=INFO
   ```

4. Despliega y comprueba `cookies de YouTube: configuradas` en los logs. Esa línea confirma que el archivo es legible y tiene la cabecera esperada, no que YouTube acepte la sesión. Prueba `/play`.

Las cookies equivalen a credenciales de sesión: no las pegues en el chat, en variables con su contenido, en logs ni en Git. yt-dlp advierte de posibles restricciones de cuenta al usar sesiones; consulta su guía antes de hacerlo. El bot copia el archivo a un directorio temporal por extracción y elimina la copia al terminar, sin modificar el original montado. Las cookies pueden caducar o no resolver el bloqueo de la IP del servidor. Si ocurre, revisa o renueva la sesión; no se garantiza acceso a todos los vídeos.

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
