from __future__ import annotations

import argparse
import asyncio
import io
import logging
import pathlib
import shutil
import tarfile
import zipfile
from types import TracebackType
from typing import Literal, Self, Type
from urllib.parse import unquote, urljoin

import aiohttp
import aiohttp_retry
from lxml import html

LOGGER = logging.getLogger(__name__)


class KHOutsiderError(Exception):
    """An Error type for problems occurring in imperative code in this module."""


class DirectoryOutput:
    """An output that stores files in a directory."""
    def __init__(self, output_directory: pathlib.Path, name: str) -> None:
        self.album_directory = output_directory / name

    async def __aenter__(self) -> Self:
        self.album_directory.mkdir(exist_ok=True)
        LOGGER.info("Created download directory for %s", self.album_directory.name)
        return self

    async def __aexit__(
        self,
        exc_type: Type[BaseException],
        exc_val: BaseException,
        exc_tb: TracebackType,
    ) -> Literal[False]:
        if exc_type is not None:
            shutil.rmtree(self.album_directory, ignore_errors=True)
        return False

    def open(self, name: str) -> io.BufferedWriter:
        """Open a file relative to the base directory."""
        return (self.album_directory / name).open(mode="wb")


class TarOutput(DirectoryOutput):
    """An output that stores files in a tar archive."""
    async def __aexit__(
        self,
        exc_type: Type[BaseException],
        exc_val: BaseException,
        exc_tb: TracebackType,
    ) -> Literal[False]:
        if exc_type is None:
            with tarfile.open(self.album_directory.with_suffix(".tar"), mode="w") as f:
                f.add(self.album_directory, self.album_directory.name)
            LOGGER.info("Created tar archive for %s", self.album_directory.name)
        shutil.rmtree(self.album_directory, ignore_errors=True)
        return False


class ZipOutput(DirectoryOutput):
    """An output that stores files in a zip archive."""
    async def __aexit__(
        self,
        exc_type: Type[BaseException],
        exc_val: BaseException,
        exc_tb: TracebackType,
    ) -> Literal[False]:
        if exc_type is None:
            with zipfile.ZipFile(
                self.album_directory.with_suffix(".zip"), mode="w"
            ) as f:
                f.mkdir(self.album_directory.name)
                for p in sorted(self.album_directory.iterdir()):
                    f.write(p, p.relative_to(self.album_directory.parent))
            LOGGER.info("Created zip archive for %s", self.album_directory.name)
        shutil.rmtree(self.album_directory, ignore_errors=True)
        return False


def get_song_link(download_doc: html.HtmlElement, prefer_flac: bool) -> str:
    """Gets the URL of the song file from the document."""
    audio_links = {
        y[y.rindex(".") + 1 :]: y
        for y in (
            x.getparent().get("href")
            for x in download_doc.find_class("songDownloadLink")
        )
    }
    if prefer_flac and "flac" in audio_links:
        audio_link = audio_links["flac"]
    else:
        try:
            audio_link = audio_links["mp3"]
        except KeyError:
            raise ValueError("No song links found in page.")
    return audio_link


async def download_file(
    url: str, session: aiohttp_retry.RetryClient, output: DirectoryOutput
) -> None:
    """Downloads the given url to an automatically named file on disk."""
    async with session.get(url) as response:
        if "content-disposition" in response.headers:
            header = response.headers["content-disposition"]
            filename = header.split("filename=")[1]
        else:
            filename = url.split("/")[-1]

        filename = unquote(filename)
        with output.open(filename) as file:
            # 10 MB chunks
            async for chunk in response.content.iter_chunked(1024 * 1024 * 10):
                file.write(chunk)
    LOGGER.info("Downloaded file in %s: %s", output.album_directory.name, filename)


async def process_download_page(
    url: str,
    session: aiohttp_retry.RetryClient,
    prefer_flac: bool,
    output: DirectoryOutput,
) -> None:
    """Glues the parsing and downloading together for use as a task."""
    async with session.get(url) as resp:
        # URL join just in case it's a relative link.
        download_doc = html.document_fromstring(await resp.text())
    try:
        audio_link = urljoin(url, get_song_link(download_doc, prefer_flac))
    except ValueError as err:
        raise KHOutsiderError(f"Could not find song links on {url}") from err
    await download_file(audio_link, session, output)


def get_track_count(album_doc: html.HtmlElement) -> int:
    """Gets the number of tracks on the album from the document."""
    try:
        info_paragraph = (
            album_doc.find(".//p[@align='left']").text_content().splitlines()
        )
    except IndexError:
        raise ValueError("No info paragraph found in page.")
    for line in info_paragraph:
        if "Number of Files" in line:
            return int(line.split(":")[-1])
    raise ValueError("Info Paragraph did not contain number of files.")


async def download_album(
    url: str,
    prefer_flac: bool,
    output_directory: pathlib.Path,
    output_format: Literal["directory", "tar", "zip"],
) -> None:
    """Top level imperative code for downloading an album."""

    retry_options = aiohttp_retry.JitterRetry(attempts=5)
    async with aiohttp_retry.RetryClient(
        raise_for_status=True, retry_options=retry_options
    ) as session:
        try:
            async with session.get(url) as resp:
                album_doc = html.document_fromstring(await resp.text())
            LOGGER.info("Obtained list URL for %s", url)
        except aiohttp.ClientError as err:
            LOGGER.error("An error occurred in fetching the album at %s: %s", url, err)
            return
        album_name = album_doc.findtext(".//h2")
        if album_name is None:
            LOGGER.error("Could not find album name for %s", url)
            return
        LOGGER.info("Obtained URL for %s", album_name)
        try:
            track_count = get_track_count(album_doc)
            LOGGER.info("%s songs available for %s", track_count, album_name)
        except ValueError as err:
            LOGGER.warning("Could not find album info for %s: %s", album_name, err)
        match output_format:
            case "directory":
                output_type = DirectoryOutput
            case "tar":
                output_type = TarOutput
            case "zip":
                output_type = ZipOutput
        try:
            async with (
                output_type(output_directory, album_name) as output,
                asyncio.TaskGroup() as tg,
            ):
                download_page_urls = album_doc.find_class("playlistDownloadSong")
                if len(download_page_urls) == 0:
                    raise KHOutsiderError(f"No songs found on {url}")
                for download_page_url in download_page_urls:
                    tg.create_task(
                        process_download_page(
                            urljoin(url, download_page_url.find("a").get("href")),
                            session,
                            prefer_flac,
                            output,
                        )
                    )
        except ExceptionGroup as err:
            LOGGER.error(
                "Errors occurred while trying to download songs from %s",
                album_name,
            )
            match, rest = err.split((aiohttp.ClientError, KHOutsiderError))
            if match is not None:
                for exc in match.exceptions:
                    LOGGER.error(str(exc))
            if rest is not None:
                LOGGER.error("Unexpected errors occurred. Raising.")
                raise rest


async def download_albums(
    urls: list[str],
    prefer_flac: bool,
    output_directory: pathlib.Path,
    output_format: Literal["directory", "tar", "zip"],
) -> None:
    """Concurrently download multiple albums."""
    # gather is fragile. doesn't handle KeyboardInterrupt or SystemExit well, etc.
    # TaskGroup would be better, but currently cancels all tasks in itself on error.
    # if one album fails to download, we still want to be trying on the others.
    # if gh-101581 ever gets resolved, probably switch to the result of that.
    # https://github.com/python/cpython/issues/101581
    exceptions = [
        x
        for x in await asyncio.gather(
            *(
                download_album(url, prefer_flac, output_directory, output_format)
                for url in urls
            ),
            return_exceptions=True,
        )
        if isinstance(x, BaseException)
    ]
    if len(exceptions) != 0:
        raise BaseExceptionGroup(
            "Unexpected errors occured while downloading albums.", exceptions
        )


def main() -> None:
    """Driver code for running as a shell script."""
    parser = argparse.ArgumentParser(
        prog="KHOutsider",
        description="Automatically download a full album from KHInsider",
        epilog="Enjoy the tunes!",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "urls", nargs="+", metavar="url", help="URL with the album tracklist"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--prefer-flac",
        action="store_true",
        help="Download FLAC files over MP3 if available.",
    )
    parser.add_argument(
        "-o",
        "--output-directory",
        default=".",
        type=pathlib.Path,
        help="The directory to store albums in.",
    )
    parser.add_argument(
        "--output-format",
        action="store",
        default="directory",
        choices=["directory", "tar", "zip"],
        help="How to output the album on disk. Directory of files, or as a zip or tar.",
    )

    args = parser.parse_args()

    logging.basicConfig(format="%(levelname)s %(message)s")
    root_logger = logging.getLogger()
    if args.verbose:
        root_logger.setLevel(logging.INFO)

    if not args.output_directory.is_dir():
        root_logger.error("Output directory %s does not exist.", args.output_directory)
        return

    asyncio.run(
        download_albums(
            args.urls, args.prefer_flac, args.output_directory, args.output_format
        )
    )


if __name__ == "__main__":
    main()
