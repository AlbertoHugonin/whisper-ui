from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from whisper_ui.core.exceptions import DownloadError
from whisper_ui.pipeline.download import DownloadStage


class TestDownloadStageWithMock:
    @pytest.fixture
    def download_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "downloads"
        d.mkdir()
        return d

    @pytest.fixture
    def context(self, download_dir: Path) -> dict[str, Any]:
        return {
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "download_dir": str(download_dir),
            "input_path": "",
        }

    def _make_mock_ydl(
        self,
        download_dir: Path,
        duration: int = 120,
        title: str = "Test Video",
        extra_info: dict[str, Any] | None = None,
    ):
        """Create a mock YoutubeDL that simulates a successful download."""
        mock_ydl_instance = MagicMock()

        def extract_info(url, download=True):
            info = {"duration": duration, "title": title, **(extra_info or {})}
            if download:
                # Simulate file creation
                (Path(download_dir) / "video.mp4").write_bytes(b"fake video")
            return info

        mock_ydl_instance.extract_info = extract_info
        mock_ydl_instance.__enter__ = lambda self: self
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)
        return mock_ydl_instance

    def test_successful_download(self, context, download_dir):
        mock_ydl = self._make_mock_ydl(download_dir)
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = mock_ydl

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            stage = DownloadStage()
            result = stage.execute(context)

        assert result["input_path"] == str(download_dir / "video.mp4")
        assert result["video_title"] == "Test Video"

    def test_live_stream_rejected_before_download_pass(self, context, download_dir):
        # A live stream reports duration=None, which must not bypass the
        # duration cap as 0; the rejection has to happen on the metadata
        # probe, before any download pass starts.
        mock_ydl_instance = MagicMock()

        def extract_info(url, download=True):
            assert not download, "live stream must be rejected before the download pass"
            return {"duration": None, "title": "Live", "is_live": True, "live_status": "is_live"}

        mock_ydl_instance.extract_info = extract_info
        mock_ydl_instance.__enter__ = lambda self: self
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = mock_ydl_instance

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            stage = DownloadStage()
            with pytest.raises(DownloadError, match=r"[Ll]ive"):
                stage.execute(context)

    def test_network_error_raises_download_error(self, context, download_dir):
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info.side_effect = Exception("Network unreachable")
        mock_ydl_instance.__enter__ = lambda self: self
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)

        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = mock_ydl_instance

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            stage = DownloadStage()
            with pytest.raises(DownloadError, match="Failed to download"):
                stage.execute(context)


class TestGoogleDriveDownload:
    @pytest.fixture
    def download_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "downloads"
        d.mkdir()
        return d

    @pytest.fixture
    def context(self, download_dir: Path) -> dict[str, Any]:
        return {
            "source_url": "https://drive.google.com/uc?export=download&id=1AbCdEfGhIjKlMnOpQrStUvWxYz",
            "download_dir": str(download_dir),
            "input_path": "",
        }

    def test_successful_gdrive_download(self, context, download_dir):
        resolved_file = str(download_dir / "meeting_recording.m4a")

        def mock_download(url, output, quiet=True):
            out_path = Path(output)
            if out_path.is_dir():
                out_path = out_path / "meeting_recording.m4a"
            out_path.write_bytes(b"fake audio content")
            return str(out_path)

        mock_gdown = MagicMock()
        mock_gdown.download = mock_download

        with patch.dict("sys.modules", {"gdown": mock_gdown}):
            stage = DownloadStage()
            result = stage.execute(context)

        assert result["input_path"] == resolved_file
        assert result["video_title"] == "meeting_recording"

    def test_gdrive_file_exceeding_size_cap_raises_and_deletes(self, context, download_dir):
        # Drive files carry no duration metadata, so the byte cap is the only
        # guard against an oversized file filling the disk.
        def mock_download(url, output, quiet=True):
            out_path = Path(output) / "audio.mp3"
            out_path.write_bytes(b"x" * 100)
            return str(out_path)

        mock_gdown = MagicMock()
        mock_gdown.download = mock_download

        with patch.dict("sys.modules", {"gdown": mock_gdown}):
            stage = DownloadStage(max_file_size=10)
            with pytest.raises(DownloadError, match="exceeds the maximum allowed"):
                stage.execute(context)
        assert not (download_dir / "audio.mp3").exists()


class TestTwitterDownload:
    @pytest.fixture
    def download_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "downloads"
        d.mkdir()
        return d

    @pytest.fixture
    def context(self, download_dir: Path) -> dict[str, Any]:
        return {
            "source_url": "https://x.com/i/status/2052048266687332852",
            "download_dir": str(download_dir),
            "input_path": "",
        }

    def _make_mock_ydl(self, download_dir: Path, duration: int = 120, title: str = "X Post"):
        mock_ydl_instance = MagicMock()

        def extract_info(url, download=True):
            info = {"duration": duration, "title": title}
            if download:
                (Path(download_dir) / "video.mp4").write_bytes(b"fake video")
            return info

        mock_ydl_instance.extract_info = extract_info
        mock_ydl_instance.__enter__ = lambda self: self
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)
        return mock_ydl_instance

    def test_successful_twitter_download(self, context, download_dir):
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = self._make_mock_ydl(download_dir)

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            result = DownloadStage().execute(context)

        assert result["input_path"] == str(download_dir / "video.mp4")
        assert result["video_title"] == "X Post"

    def test_transient_guest_token_error_retries_then_succeeds(self, context, download_dir):
        # X intermittently rejects anonymous guest tokens. The first attempt
        # fails with "Bad guest token"; a fresh-client retry fetches a new token
        # and succeeds, so the user never sees the error.
        calls = {"n": 0}

        def extract_info(url, download=True):
            calls["n"] += 1
            if calls["n"] == 1:
                raise Exception(
                    "ERROR: [twitter] 123: Error(s) while querying API: Bad guest token; please report this issue"
                )
            info = {"duration": 120, "title": "X Post"}
            if download:
                (Path(download_dir) / "video.mp4").write_bytes(b"fake video")
            return info

        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info = extract_info
        mock_ydl_instance.__enter__ = lambda self: self
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = mock_ydl_instance

        with (
            patch.dict("sys.modules", {"yt_dlp": mock_module}),
            patch("whisper_ui.pipeline.download.time.sleep"),
        ):
            result = DownloadStage().execute(context)

        assert result["input_path"] == str(download_dir / "video.mp4")
        # First attempt failed at the metadata probe, second attempt built a
        # fresh client and completed: two YoutubeDL instances in total.
        assert mock_module.YoutubeDL.call_count == 2

    def test_restricted_error_fails_fast_without_retry(self, context, download_dir):
        # A login wall is permanent: it must raise on the first attempt, never
        # burning retry budget on a request that cannot succeed.
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info.side_effect = Exception(
            "Sorry, you are not authorized to view this tweet. Log in."
        )
        mock_ydl_instance.__enter__ = lambda self: self
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = mock_ydl_instance

        with (
            patch.dict("sys.modules", {"yt_dlp": mock_module}),
            patch("whisper_ui.pipeline.download.time.sleep") as mock_sleep,
            pytest.raises(DownloadError, match="無法下載此貼文"),
        ):
            DownloadStage().execute(context)

        assert mock_module.YoutubeDL.call_count == 1
        mock_sleep.assert_not_called()
