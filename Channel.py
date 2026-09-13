# Channel.py
# Public channel catalog for the existing Telegram bot.
# NOTE: URLs are retained exactly as supplied by the project owner.
# Only use sources you are authorized/permitted to access.

PUBLIC_CHANNELS = {
    "Pogo.Airtel": "https://ranapkx.site/RANAPK33k/TVD/play.php?id=372993",
    "DISCOVERY.KIDS": "https://ranapkx.site/RANAPK33k/TVD/play.php?id=372928",
    "Pogo.SunDirect": "https://bdix.spidy.online/MAC/SBHGOLD/play.php?id=281410",
    "Wow.kidz": "https://yuppparoriglin.akamaized.net/181224/smil:wowkidzhindi.smil/playlist.m3u8?hdnts=st=1735898689~exp=1835898688~acl=*~hmac=f5fe24724fe05481e3841f9eb5ab8efdee0a3dd83645ae9dcf45703f525bab7b",
    "MINIX": "https://vodzong.mjunoon.tv:8087/streamtest/157-1M/chunks.m3u8",
    "POGO.FW": "https://playlistgen-rkdyiptv.pages.dev/api/rkdyiptv/playlist.m3u?action=stream&d=eyJpIjoiMTE2NjYiLCJwIjoiNGYzMWMyNDIxNWY0ZmRlOCIsImUiOjE3ODg1MDYwMTc5MTksInMiOjIwNjk5LCJoIjoiOWIyMDNiYTFmZWY3MzNlYzlkNjQifQ",
    "DISCOVERYKIDS.2": "http://line.sweetv.xyz/play/live.php?mac=00:1A:79:00:03:B2&stream=1540017&extension=ts&play_token=eFCOqzrsPI",
    "DISNEY.CHANNEL": "http://103.155.18.191:8000/play/a01q/index.m3u8",
    "NICK.2": "http://103.155.18.191:8000/play/a04c/index.m3u8",
    "CARTOON.NETWORK": "http://202.70.146.135:8000/play/a0a8/index.m3u8",
    "CARTOON.NETWORK.HD+": "http://202.70.146.135:8000/play/a0a3/index.m3u8",
    "DISCOVERYKIDS.3": "http://202.70.146.135:8000/play/a0a6/index.m3u8",
    "Epic kids": "https://cc-t8lqe1o99pszu.akamaized.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-t8lqe1o99pszu/playlist.m3u8",
    "Pogo.Tplay": "http://66.102.126.10:8000/play/a00d/index.m3u8",
    "Pogo.Tplay2": "http://202.70.146.135:8000/play/a0a7/index.m3u8",
    'Sony Entertainment Television SD': 'https://cloudplay-sonyliv.pages.dev/set.m3u8',
    'Sony Entertainment Television HD': 'https://cloudplay-sonyliv.pages.dev/sethd.m3u8',
    'Sony SAB SD': 'https://cloudplay-sonyliv.pages.dev/sab.m3u8',
    'Sony SAB HD': 'https://cloudplay-sonyliv.pages.dev/sabhd.m3u8',
    'Sony PAL SD': 'https://cloudplay-sonyliv.pages.dev/pal.m3u8',
    'Sony Marathi SD': 'https://cloudplay-sonyliv.pages.dev/marathi.m3u8',
    'Sony AATH SD': 'https://cloudplay-sonyliv.pages.dev/aath.m3u8',
    'Sony Telugu SD': 'https://cloudplay-sonyliv.pages.dev/telugu.m3u8',
    'Sony VIZHA SD': 'https://cloudplay-sonyliv.pages.dev/vizha.m3u8',
    'Sony MAX SD': 'https://cloudplay-sonyliv.pages.dev/max.m3u8',
    'Sony MAX HD': 'https://cloudplay-sonyliv.pages.dev/maxhd.m3u8',
    'Sony MAX 1 SD': 'https://cloudplay-sonyliv.pages.dev/max1.m3u8',
    'Sony MAX 2 SD': 'https://cloudplay-sonyliv.pages.dev/max2.m3u8',
    'Sony WAH SD': 'https://cloudplay-sonyliv.pages.dev/wah.m3u8',
    'Sony PIX SD': 'https://cloudplay-sonyliv.pages.dev/pix.m3u8',
    'Sony PIX HD': 'https://cloudplay-sonyliv.pages.dev/pixhd.m3u8',
    'Sony.YAY': 'https://cloudplay-sonyliv.pages.dev/yay.m3u8',
    'Sony Sports TEN 1 SD': 'https://cloudplay-sonyliv.pages.dev/ten1.m3u8',
    'Sony Sports TEN 1 HD': 'https://cloudplay-sonyliv.pages.dev/ten1hd.m3u8',
    'Sony Sports TEN 2 SD': 'https://cloudplay-sonyliv.pages.dev/ten2.m3u8',
    'Sony Sports TEN 2 HD': 'https://cloudplay-sonyliv.pages.dev/ten2hd.m3u8',
    'Sony Sports TEN 3 SD': 'https://cloudplay-sonyliv.pages.dev/ten3.m3u8',
    'Sony Sports TEN 3 HD': 'https://cloudplay-sonyliv.pages.dev/ten3hd.m3u8',
    'Sony Sports TEN 4 SD': 'https://cloudplay-sonyliv.pages.dev/ten4.m3u8',
    'Sony Sports TEN 4 HD': 'https://cloudplay-sonyliv.pages.dev/ten4hd.m3u8',
    'Sony Sports TEN 5 SD': 'https://cloudplay-sonyliv.pages.dev/ten5.m3u8',
    'Sony Sports TEN 5 HD': 'https://cloudplay-sonyliv.pages.dev/ten5hd.m3u8',
    'Sony BBC Earth SD': 'https://cloudplay-sonyliv.pages.dev/bbcearth.m3u8',
    'Sony BBC Earth HD': 'https://cloudplay-sonyliv.pages.dev/bbcearthhd.m3u8',
}

def get_public_channels():
    """Return a copy so callers cannot mutate the catalog accidentally."""
    return dict(PUBLIC_CHANNELS)

def get_channel_url(name: str):
    """Return a channel URL by case-insensitive name, or None."""
    key = name.strip().casefold()
    for channel, url in PUBLIC_CHANNELS.items():
        if channel.casefold() == key:
            return url
    return None
