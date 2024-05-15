import argparse
import asyncio
import logging
import pathlib
import shutil
from urllib.parse import unquote, urljoin

import aiohttp
import aiohttp_retry
from lxml import etree
from lxml.cssselect import CSSSelector


HTML_PARSER = etree.HTMLParser()
LOGGER = logging.getLogger(__name__)


class KHOutsiderError(Exception):
    """An Error type for problems occurring in imperative code in this module."""


DOWNLOAD_LINK_SELECTOR = CSSSelector(".songDownloadLink")


def get_song_link(download_doc: etree._Element, prefer_flac: bool) -> str:
    """Gets the URL of the song file from the document."""
    audio_links = {
        y[y.rindex(".") + 1 :]: y
        for y in (
            x.getparent().get("href") for x in DOWNLOAD_LINK_SELECTOR(download_doc)
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
    url: str, session: aiohttp_retry.RetryClient, album_directory: pathlib.Path
) -> None:
    """Downloads the given url to an automatically named file on disk."""
    async with session.get(url) as response:
        if "content-disposition" in response.headers:
            header = response.headers["content-disposition"]
            filename = header.split("filename=")[1]
        else:
            filename = url.split("/")[-1]

        filename = unquote(filename)
        with open(album_directory / filename, mode="wb") as file:
            # 10 MB chunks
            async for chunk in response.content.iter_chunked(1024 * 1024 * 10):
                file.write(chunk)
    LOGGER.info("Downloaded file in %s: %s", album_directory.name, filename)


async def process_download_page(
    url: str,
    session: aiohttp_retry.RetryClient,
    prefer_flac: bool,
    album_directory: pathlib.Path,
) -> None:
    """Glues the parsing and downloading together for use as a task."""
    async with session.get(url) as resp:
        # URL join just in case it's a relative link.
        download_doc = etree.fromstring(await resp.text(), HTML_PARSER)
    try:
        audio_link = urljoin(url, get_song_link(download_doc, prefer_flac))
    except ValueError as err:
        raise KHOutsiderError(f"Could not find song links on {url}") from err
    await download_file(audio_link, session, album_directory)


INFO_SELECTOR = CSSSelector('p[align="left"]')


def get_track_count(album_doc: etree._Element) -> int:
    """Gets the number of tracks on the album from the document."""
    try:
        info_paragraph = etree.tostring(
            INFO_SELECTOR(album_doc)[0], method="text", encoding="unicode"
        ).splitlines()
    except IndexError:
        raise ValueError("No info paragraph found in page.")
    for line in info_paragraph:
        if "Number of Files" in line:
            return int(line.split(":")[-1])
    raise ValueError("Info Paragraph did not contain number of files.")


DOWNLOAD_PAGE_SELECTOR = CSSSelector("#songlist .playlistDownloadSong")


async def download_album(
    url: str, prefer_flac: bool, output_directory: pathlib.Path
) -> None:
    """Top level imperative code for downloading an album."""

    retry_options = aiohttp_retry.JitterRetry(attempts=5)
    async with aiohttp_retry.RetryClient(
        raise_for_status=True, retry_options=retry_options
    ) as session:
        try:
            async with session.get(url) as resp:
                album_doc = etree.fromstring(await resp.text(), HTML_PARSER)
            LOGGER.info("Obtained list URL for %s", url)
        except aiohttp.ClientError as err:
            LOGGER.error("An error occurred in fetching the album at %s: %s", url, err)
            return
        album_name = album_doc.findtext(".//h2")
        if album_name is None:
            LOGGER.error("Could not find album name for %s", url)
            return
        album_directory = output_directory / album_name
        album_directory.mkdir(exist_ok=True)
        LOGGER.info("Obtained URL for %s", album_name)
        try:
            track_count = get_track_count(album_doc)
            LOGGER.info("%s songs available for %s", track_count, album_name)
        except ValueError as err:
            LOGGER.warning("Could not find album info for %s: %s", album_name, err)
        try:
            async with asyncio.TaskGroup() as tg:
                download_page_urls = DOWNLOAD_PAGE_SELECTOR(album_doc)
                if len(download_page_urls) == 0:
                    raise KHOutsiderError(f"No songs found on {url}")
                for download_page_url in download_page_urls:
                    tg.create_task(
                        process_download_page(
                            urljoin(
                                url,
                                # is min necesssary here?
                                # are there any pages that have multilple links here?
                                # is min the right way to choose among them?
                                min(
                                    x.get("href")
                                    for x in download_page_url.findall("a")
                                ),
                            ),
                            session,
                            prefer_flac,
                            album_directory,
                        )
                    )
        except ExceptionGroup as err:
            # Clean up after ourselves
            shutil.rmtree(album_directory, ignore_errors=True)
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
    urls: list[str], prefer_flac: bool, output_directory: pathlib.Path
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
            *(download_album(url, prefer_flac, output_directory) for url in urls),
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
        help="download FLAC files over MP3 if available",
    )
    parser.add_argument(
        "-o",
        "--output-directory",
        default=".",
        type=pathlib.Path,
        help="The directory to store albums in",
    )

    args = parser.parse_args()

    logging.basicConfig(format="%(levelname)s %(message)s")
    root_logger = logging.getLogger()
    if args.verbose:
        root_logger.setLevel(logging.INFO)

    if not args.output_directory.is_dir():
        root_logger.error("Output directory %s does not exist.", args.output_directory)
        return

    asyncio.run(download_albums(args.urls, args.prefer_flac, args.output_directory))


if __name__ == "__main__":
    main()
