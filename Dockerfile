FROM node:22-bookworm-slim AS javascript

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FFMPEG_PATH=/usr/bin/ffmpeg \
    IDLE_TIMEOUT=180

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 ca-certificates tini libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 bot

# Node y Python comparten Debian Bookworm y la arquitectura de destino.
COPY --from=javascript /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install -r requirements.txt \
    && python -m pip check \
    && python -c "import discord, yt_dlp, aiohttp, dotenv, davey, nacl" \
    && ffmpeg -version \
    && node --version

COPY --chown=bot:bot bot.py music.py ./

USER bot
# asyncio.run (utilizado por discord.py) gestiona SIGINT y cierra las conexiones.
STOPSIGNAL SIGINT
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "bot.py"]
