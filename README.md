# KHoutsider

A Python script to download whole soundtracks and albums from [KHInsider](https://downloads.khinsider.com/)

## Usage

`python3 khoutsider.py <album-url>` will navigate to the provided album name and download it on MP3 format for you.

You can supply multiple album URLs and all of them will be downloaded simultaneously.

The albums will be downloaded into the directory specified with `--output-directory` (default `.`).

The albums can be stored as flat files (`directory`), tar (`tar`) or zip (`zip`) using `--output-format` (default `directory`).

If you wish to instead obtain the album in FLAC, you can provide `--prefer-flac`. Note that not all albums have FLAC downloads available.

You can provide `--progress-bar` to display a progress bar for the downloads.

You can run `--help` for a quick refresher on usage.

## License

Licensed with the Be Gay Do Crime license.

## Credits

Maintained by me (ikuyo).

Original code cleanup by [gotyaoi](https://github.com/gotyaoi)
