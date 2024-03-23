import argparse
import asyncio
import functools

import aiohttp
from requests.utils import unquote
from requests_html import HTMLResponse, HTMLSession, AsyncHTMLSession


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


async def main(args: argparse.Namespace) -> None:

    def print_if_verbose(msg: str) -> None:
        if args.verbose:
            print(msg)

    url = args.url

    session = HTMLSession()
    r = session.get(url)
    print_if_verbose("Obtained list URL...")
    asession = AsyncHTMLSession(loop=asyncio.get_running_loop())

    async def get_url(url: str) -> HTMLResponse:
        return await asession.get(url)

    track_links = []
    if not r.raise_for_status():
        print_if_verbose("Obtained URL for " + r.html.find("h2", first=True).text)
        info_paragraph = r.html.find('p[align="left"]', first=True).text.splitlines()
        for line in info_paragraph:
            if "Number of Files" in line:
                track_count = int(line.split(":")[-1])

        print_if_verbose(f"{track_count} songs available")

        table = [
            min(x.absolute_links)
            for x in r.html.find("#songlist .playlistDownloadSong")
        ]
        track_links = [functools.partial(get_url, url) for url in table]

    r = []
    async with asyncio.TaskGroup() as tg:
        for track in track_links:
            r.append(tg.create_task(track()))
    flac_possible = True
    async with aiohttp.ClientSession() as download_session, asyncio.TaskGroup() as tg:
        for result in (t.result() for t in r):
            if args.prefer_flac and flac_possible:
                audio = [x for x in list(result.html.absolute_links) if ".flac" in x]
                if not audio and flac_possible:
                    flac_possible = False
                    print("No FLAC files available, defaulting to mp3...")
                    audio = result.html.find("audio", first=True).attrs["src"]
                else:
                    audio = audio[-1]
            else:
                audio = result.html.find("audio", first=True).attrs["src"]
            tg.create_task(download_file(audio, download_session, args.verbose))


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
