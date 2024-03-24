import argparse
import asyncio
from urllib.parse import unquote, urljoin
import sys

import aiohttp
from lxml import etree
from lxml.cssselect import CSSSelector


async def download_file(
    url: str, session: aiohttp.ClientSession, verbose: bool = False
) -> None:
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


DOWNLOAD_LINK_SELECTOR = CSSSelector(".songDownloadLink")


async def process_download_page(
    url: str, session: aiohttp.ClientSession, html_parser: etree.HTMLParser
) -> None:
    try:
        async with session.get(url) as resp:
            download_doc = etree.fromstring(await resp.text(), html_parser)
    except aiohttp.ClientError as err:
        raise
    audio_links = {
        y[y.rindex(".") + 1 :]: y
        for y in (
            x.getparent().get("href") for x in DOWNLOAD_LINK_SELECTOR(download_doc)
        )
    }
    if args.prefer_flac and "flac" in audio_links:
        audio_link = audio_links["flac"]
    else:
        audio_link = audio_links["mp3"]
    await download_file(audio_link, session, args.verbose)


INFO_SELECTOR = CSSSelector('p[align="left"]')
DOWNLOAD_PAGE_SELECTOR = CSSSelector("#songlist .playlistDownloadSong")


async def main(args: argparse.Namespace) -> None:

    def print_if_verbose(*msg: str) -> None:
        if args.verbose:
            print(*msg, file=sys.stderr)

    html_parser = etree.HTMLParser()

    url = args.url

    async with aiohttp.ClientSession(raise_for_status=True) as session:
        try:
            async with session.get(url) as resp:
                print_if_verbose("Obtained list URL...")
                album_doc = etree.fromstring(await resp.text(), html_parser)
        except aiohttp.ClientError as err:
            raise
        print_if_verbose("Obtained URL for ", album_doc.findtext(".//h2"))
        info_paragraph = etree.tostring(
            INFO_SELECTOR(album_doc)[0], method="text", encoding="unicode"
        ).splitlines()
        for line in info_paragraph:
            if "Number of Files" in line:
                track_count = int(line.split(":")[-1])
                break
        print_if_verbose(f"{track_count} songs available")
        async with asyncio.TaskGroup() as tg:
            for download_page_url in DOWNLOAD_PAGE_SELECTOR(album_doc):
                tg.create_task(
                    process_download_page(
                        urljoin(
                            url,
                            min(x.get("href") for x in download_page_url.findall("a")),
                        ),
                        session,
                        html_parser,
                    )
                )


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
