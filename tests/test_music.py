import asyncio
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from music import Player, Resolver, Track, normalize_query, extract, SafeFormatter, SourceError


class DiagnosticTests(unittest.TestCase):
    def test_logging_redacts_urls_and_token_in_traceback(self):
        with patch.dict(os.environ, {"DISCORD_TOKEN": "test-secret-token"}):
            error = RuntimeError("test-secret-token https://audio.example/?signature=secret")
            record = logging.LogRecord("music", logging.ERROR, "test", 1, "fallo", (),
                                       (RuntimeError, error, None))
            text = SafeFormatter("%(message)s").format(record)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("test-secret-token", text)
        self.assertNotIn("signature=secret", text)

    def test_cookies_are_copied_and_temporary_file_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "source.txt"
            original.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            copied = None

            def make_ydl(options):
                nonlocal copied
                copied = Path(options["cookiefile"])
                self.assertNotEqual(copied, original)
                self.assertEqual(copied.read_text(), original.read_text())
                ydl = Mock()
                ydl.extract_info.return_value = {"title": "song", "webpage_url": "page", "url": "audio"}
                context = Mock()
                context.__enter__ = Mock(return_value=ydl)
                context.__exit__ = Mock(return_value=False)
                return context

            with patch.dict(os.environ, {"YTDLP_COOKIES_FILE": str(original)}), patch("music.yt_dlp.YoutubeDL", side_effect=make_ydl):
                self.assertEqual(extract("ytsearch1:test")["url"], "audio")
            self.assertFalse(copied.exists())
            self.assertEqual(original.read_text(), "# Netscape HTTP Cookie File\n")

    def test_bot_challenge_has_actionable_error(self):
        import yt_dlp
        context = Mock()
        context.__enter__ = Mock(side_effect=yt_dlp.utils.DownloadError("Sign in to confirm you’re not a bot"))
        context.__exit__ = Mock(return_value=False)
        with patch.dict(os.environ, {"YTDLP_COOKIES_FILE": ""}), patch("music.yt_dlp.YoutubeDL", return_value=context):
            with self.assertRaisesRegex(SourceError, "YTDLP_COOKIES_FILE"):
                extract("ytsearch1:test")


class QueryTests(unittest.TestCase):
    def test_search_and_youtube(self):
        self.assertEqual(normalize_query("artista canción"), ("ytsearch1:artista canción", False))
        self.assertFalse(normalize_query("https://youtu.be/abc")[1])

    def test_spotify_localized_url_and_uri(self):
        track_id = "a" * 22
        canonical = f"https://open.spotify.com/track/{track_id}"
        self.assertEqual(normalize_query(f"spotify:track:{track_id}"), (canonical, True))
        self.assertEqual(normalize_query(f"{canonical}?si=123"), (canonical, True))
        self.assertEqual(normalize_query(f"https://open.spotify.com/intl-es/track/{track_id}"), (canonical, True))

    def test_reject_untrusted_inputs_and_collections(self):
        for value in ["", "file:///etc/passwd", "https://localhost/a", "https://youtube.com.evil.org/a",
                      "https://user:pass@youtube.com/a", "https://youtube.com:8080/a",
                      "https://open.spotify.com/playlist/abc", "spotify:album:abc"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_query(value)


class PlayerTests(unittest.IsolatedAsyncioTestCase):
    def make_player(self):
        self.voice = Mock()
        self.voice.disconnect = AsyncMock()
        self.voice.is_connected.return_value = True
        self.voice.disconnect.side_effect = lambda **kwargs: setattr(self.voice.is_connected, "return_value", False)
        self.resolver = Mock()
        self.resolver.stream = AsyncMock(return_value="https://audio.example/test")
        self.channel = Mock()
        self.channel.send = AsyncMock()
        return Player(self.voice, self.resolver, idle_timeout=0.03)

    async def test_failed_track_does_not_prevent_next_track(self):
        player = self.make_player()
        self.resolver.stream.side_effect = [ValueError("unavailable"), "https://audio.example/test"]
        self.voice.play.side_effect = lambda source, after: after(None)
        first = Track("bad", "https://youtu.be/first", "a")
        second = Track("good", "https://youtu.be/second", "b")
        player.queue.put_nowait((first, self.channel))
        player.queue.put_nowait((second, self.channel))
        with patch("music.discord.FFmpegOpusAudio"):
            await asyncio.wait_for(player.task, 1)
        self.assertEqual(self.resolver.stream.await_count, 2)
        self.voice.play.assert_called_once()
        self.voice.disconnect.assert_awaited_once()
        self.assertTrue(player.closed)

    async def test_stop_during_extraction_cannot_start_audio_later(self):
        player = self.make_player()
        entered = asyncio.Event()

        async def slow_stream(track):
            entered.set()
            await asyncio.Event().wait()

        self.resolver.stream.side_effect = slow_stream
        player.queue.put_nowait((Track("song", "https://youtu.be/test", "a"), self.channel))
        await asyncio.wait_for(entered.wait(), 1)
        await player.close()
        self.voice.play.assert_not_called()
        self.voice.disconnect.assert_awaited_once()
        self.assertTrue(player.closed)

    async def test_spotify_resolves_using_title_without_streaming_spotify(self):
        session = Mock()
        response = Mock()
        response.json = AsyncMock(return_value={"title": "Some Song"})
        session.get.return_value.__aenter__ = AsyncMock(return_value=response)
        session.get.return_value.__aexit__ = AsyncMock(return_value=False)
        resolver = Resolver(session)
        with patch("music.extract", return_value={"title": "Video", "webpage_url": "https://youtu.be/test", "url": "audio"}) as extractor:
            track, spotify = await resolver.resolve("spotify:track:" + "a" * 22, "user")
        extractor.assert_called_once_with("ytsearch1:Some Song audio")
        self.assertTrue(spotify)
        self.assertEqual(track.url, "https://youtu.be/test")


if __name__ == "__main__":
    unittest.main()
