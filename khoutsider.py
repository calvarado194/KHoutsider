import argparse
import asyncio
import aiohttp
import nest_asyncio
nest_asyncio.apply()
from requests.utils import unquote

from requests_html import HTMLSession, AsyncHTMLSession

async def download_file(url, verbose:bool = False):
  async with aiohttp.ClientSession() as session:
    async with session.get(url) as response:
      if "content-disposition" in response.headers:
        header = response.headers["content-disposition"]
        filename = header.split("filename=")[1]
      else:
        filename = url.split("/")[-1]

      filename = unquote(filename)
      with open(filename, mode="wb") as file:
        while True:
          chunk = await response.content.read()
          if not chunk:
            break
          file.write(chunk)
        if verbose:
          print(f"Downloaded file {filename}")

async def main(args):
  def print_if_verbose(msg):
    if args.verbose:
      print(msg)

  url = args.url

  session = HTMLSession()
  r = session.get(url)
  print_if_verbose("Obtained list URL...")
  asession = AsyncHTMLSession()

  async def get_url(url):
    return await asession.get(url)

  track_links = []
  tasks = []
  if not r.raise_for_status():
    print_if_verbose("Obtained URL for " + r.html.find('h2', first=True).text)
    info_paragraph = r.html.find('p[align="left"]',first=True).text.splitlines()
    for line in info_paragraph:
      if 'Number of Files' in line:
        track_count = int(line.split(':')[-1])

    print_if_verbose(f"{track_count} songs available")

    table = [min(x.absolute_links) for x in r.html.find('#songlist .playlistDownloadSong')]
    track_links = [lambda url=url: get_url(url) for url in table]

  r = asession.run(*track_links)
  flac_possible = True
  for result in r:
    if args.prefer_flac and flac_possible:
      audio = [x for x in list(result.html.absolute_links) if '.flac' in x]
      if not audio and flac_possible:
        flac_possible = False
        print("No FLAC files available, defaulting to mp3...")
        audio = result.html.find('audio', first=True).attrs['src']
      else:
        audio = audio[-1]
    else:
      audio = result.html.find('audio', first=True).attrs['src']
    tasks.append(download_file(audio,args.verbose))

  await asyncio.gather(*tasks)
  
if __name__ == "__main__":
  parser = argparse.ArgumentParser(
                      prog='KHOutsider',
                      description='Automatically download a full album from KHInsider',
                      epilog='Enjoy the tunes!')
                        
  parser.add_argument('url', help="URL with the album tracklist")
  parser.add_argument('-v', '--verbose',
                        action='store_true') 
  parser.add_argument('--prefer-flac',
                      action='store_true',
                      help="download FLAC files over MP3 if available")

  args = parser.parse_args()

  asyncio.run(main(args))
  
