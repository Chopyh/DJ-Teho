import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from music import Player, Resolver, Track, normalize_query


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
