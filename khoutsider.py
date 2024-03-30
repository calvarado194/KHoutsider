import argparse
import asyncio
import os
import sys
from urllib.parse import unquote, urljoin

import aiohttp
import aiohttp_retry
from lxml import etree
from lxml.cssselect import CSSSelector


HTML_PARSER = etree.HTMLParser()


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
    url: str, session: aiohttp_retry.RetryClient, verbose: bool = False
) -> None:
    """Downloads the given url to an automatically named file on disk."""
    try:
        async with session.get(url) as response:
            if "content-disposition" in response.headers:
                header = response.headers["content-disposition"]
                filename = header.split("filename=")[1]
            else:
                filename = url.split("/")[-1]

            filename = unquote(filename)
            with open(filename, mode="wb") as file:
                # 10 MB chunks
                async for chunk in response.content.iter_chunked(1024 * 1024 * 10):
                    file.write(chunk)
                if verbose:
                    print(f"Downloaded file {filename}")
    except BaseException:
        # Clean up after ourselves
        try:
            os.unlink(filename)
        except FileNotFoundError:
            pass
        # Let the exception bubble up
        raise


async def process_download_page(
    url: str,
    session: aiohttp_retry.RetryClient,
    prefer_flac: bool,
    verbose: bool = False,
) -> None:
    """Glues the parsing and downloading together for use as a task."""
    async with session.get(url) as resp:
        # URL join just in case it's a relative link.
        download_doc = etree.fromstring(await resp.text(), HTML_PARSER)
        try:
            audio_link = urljoin(url, get_song_link(download_doc, prefer_flac))
        except ValueError as err:
            raise KHOutsiderError(f"Could not find song links on {url}") from err
    await download_file(audio_link, session, verbose)


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


async def download_album(url: str, prefer_flac: bool, verbose: bool = False) -> None:
    """Top level imperative code for downloading an album."""

    def print_if_verbose(*msg: str) -> None:
        if verbose:
            print(*msg, file=sys.stderr)

    retry_options = aiohttp_retry.JitterRetry(attempts=5)
    async with aiohttp_retry.RetryClient(
        raise_for_status=True, retry_options=retry_options
    ) as session:
        try:
            async with session.get(url) as resp:
                print_if_verbose("Obtained list URL...")
                album_doc = etree.fromstring(await resp.text(), HTML_PARSER)
        except aiohttp.ClientError as err:
            print("An error occurred in fetching the album at", url)
            print(err)
            return
        print_if_verbose("Obtained URL for ", album_doc.findtext(".//h2"))
        try:
            track_count = get_track_count(album_doc)
        except ValueError as err:
            print_if_verbose("Could not find album info for", url)
            print(err)
        print_if_verbose(f"{track_count} songs available")
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
                            verbose,
                        )
                    )
        except ExceptionGroup as err:
            print(
                "The following errors occurred while trying to download songs from the album at",
                url,
            )
            match, rest = err.split((aiohttp.ClientError, KHOutsiderError))
            if match is not None:
                for exc in match.exceptions:
                    print(exc)
            if rest is not None:
                print("Unexpected errors occurred. Raising.")
                raise rest


def main() -> None:
    """Driver code for running as a shell script."""
    parser = argparse.ArgumentParser(
        prog="KHOutsider",
        description="Automatically download a full album from KHInsider",
        epilog="Enjoy the tunes!",
    )

    parser.add_argument("url", help="URL with the album tracklist")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--prefer-flac",
        action="store_true",
        help="download FLAC files over MP3 if available",
    )

    args = parser.parse_args()

    asyncio.run(download_album(args.url, args.verbose))


if __name__ == "__main__":
    main()
