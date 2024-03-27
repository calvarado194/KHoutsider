import argparse
import asyncio
import os
import sys
from urllib.parse import unquote, urljoin

import aiohttp
import aiohttp_retry
from lxml import etree
from lxml.cssselect import CSSSelector


class KHOutsiderError(Exception):
    pass


async def download_file(
    url: str, session: aiohttp_retry.RetryClient, verbose: bool = False
) -> None:
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


DOWNLOAD_LINK_SELECTOR = CSSSelector(".songDownloadLink")


async def process_download_page(
    url: str, session: aiohttp_retry.RetryClient, html_parser: etree.HTMLParser
) -> None:
    async with session.get(url) as resp:
        download_doc = etree.fromstring(await resp.text(), html_parser)
    audio_links = {
        y[y.rindex(".") + 1 :]: y
        for y in (
            x.getparent().get("href") for x in DOWNLOAD_LINK_SELECTOR(download_doc)
        )
    }
    if args.prefer_flac and "flac" in audio_links:
        audio_link = audio_links["flac"]
    else:
        try:
            audio_link = audio_links["mp3"]
        except KeyError:
            raise KHOutsiderError(f"Could not find download links on {url}")
    # URL join just in case it's a relative link.
    await download_file(urljoin(url, audio_link), session, args.verbose)


INFO_SELECTOR = CSSSelector('p[align="left"]')
DOWNLOAD_PAGE_SELECTOR = CSSSelector("#songlist .playlistDownloadSong")


async def main(args: argparse.Namespace) -> None:

    def print_if_verbose(*msg: str) -> None:
        if args.verbose:
            print(*msg, file=sys.stderr)

    html_parser = etree.HTMLParser()

    url = args.url

    retry_options = aiohttp_retry.JitterRetry(attempts=5)
    async with aiohttp_retry.RetryClient(
        raise_for_status=True, retry_options=retry_options
    ) as session:
        try:
            async with session.get(url) as resp:
                print_if_verbose("Obtained list URL...")
                album_doc = etree.fromstring(await resp.text(), html_parser)
        except aiohttp.ClientError as err:
            print("An error occurred in fetching the album at", url)
            print(err)
            return
        print_if_verbose("Obtained URL for ", album_doc.findtext(".//h2"))
        try:
            info_paragraph = etree.tostring(
                INFO_SELECTOR(album_doc)[0], method="text", encoding="unicode"
            ).splitlines()
            for line in info_paragraph:
                if "Number of Files" in line:
                    track_count = int(line.split(":")[-1])
                    break
            print_if_verbose(f"{track_count} songs available")
        except (IndexError, NameError):
            print_if_verbose("Could not find album info for", url)
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
                            html_parser,
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


if __name__ == "__main__":
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

    asyncio.run(main(args))
