#
# lrt.py – the client for LRT's website API: everything the add-on asks www.lrt.lt goes through here.
# api.py – the client for the website's API: everything the add-on asks www.lrt.lt goes through here.
# About the API
#	www.lrt.lt is a Next.js site that renders most pages on the server, but its older JSON
#	endpoints are still served and are what the site's players use. they are not documented;
#	they were worked out from the website and yt-dlp's extractor for it and can change without
#	notice: if listings or playback break after a website update, look here.
#		api/json/search                     recordings: newest, by theme (term), by date range
#		                                    (dfrom/dto), the heritage archive (heritage=1) or by
#		                                    text (q); type=3 means video
#		api/json/category?term=<path>       one show or season: its info (seasons) and recordings
#		mediateka/tv-laidos, radijo-laidos  the A-Z show indexes, plain HTML lists
#		servisai/stream_url/vod/media_info  a recording's HLS address and description
#		servisai/stream_url/live/get_live_url.php   a channel's live HLS address, signed for the
#		                                    caller's IP and valid for a limited time
#	No login is needed. Streams are plain HLS without DRM.
#
# What the module does
#	Builds the URLs, sends the requests, turns failures into ApiError, caches responses (cache.py)
#	and turns the website's records into one uniform shape (recording(), show()) whatever endpoint they
#	came from, because the same field has different names in different endpoints.
#
# It uses only the Python standard library and doesn't import Kodi, so the tests and a plain
# Python on a PC can use it directly.
#

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://www.lrt.lt'
USER_AGENT = 'Mozilla/5.0 (X11; Linux aarch64) Kodi plugin.video.mediateka'
# Seconds to wait for the website before giving up with a network error.
TIMEOUT = 20
# Larger responses are refused; the show index, the largest, is about 1 MB.
MAX_RESPONSE = 16 * 1024 * 1024
# Search type for video recordings (2 is audio, 1 articles).
VIDEO = 3

# Live channels: (get_live_url id, name, 'tv' or 'radio'). Ids are the website's, names as on the website.
CHANNELS = (
	('LTV1', 'Televizija', 'tv'),
	('LTV2', 'Plius', 'tv'),
	('WORLD', 'Lituanica', 'tv'),
	('LR', 'Radijas', 'radio'),
	('Klasika', 'Klasika', 'radio'),
	('Opus', 'Opus', 'radio'),
	('LRT100', 'Radijas 100', 'radio'),
)
CHANNEL_IDS = {c[0] for c in CHANNELS}
# The two show indexes: kind -> page.
SHOW_INDEXES = {'tv': '/mediateka/tv-laidos', 'radio': '/mediateka/radijo-laidos'}

# Image sizes the website's image server has; any other size is a 404.
THUMB = '756x425'
FANART = '1287x836'

# Paths of recordings, shows and seasons. They come back to the add-on in plugin:// URLs, so they
# must be plain paths: letters, digits, "-" and "_" only, no dots, no query.
RECORDING = re.compile(r'^/(?:mediateka|radioteka)/irasas/(\d+)(?:/|$)')
PATH = re.compile(r'^(?:/[A-Za-z0-9_-]+){2,4}$')
TERM = re.compile(r'^/[a-z0-9-]+(?:/[a-z0-9-]+)?$')

# Cache lifetimes in seconds.
INDEX_TTL = 24 * 3600
FACETS_TTL = 24 * 3600
LIST_TTL = 30 * 60
NEWEST_TTL = 10 * 60


# Any failure talking to the website. status is the HTTP status, 0 for network problems and odd answers.
class ApiError(Exception):
	def __init__(self, status, message=''):
		Exception.__init__(self, '%s %s' % (status, message))
		self.status = status


# Date range for the website's decade number (see Api.facets): 1 -> before 1950, 2 -> the 1950s ...
# 9 -> 2020 onwards. Dates as the dfrom/dto search parameters want them.
def decade_range(decade):
	if decade <= 1:
		return '1900-01-01', '1949-12-31'
	start = 1930 + 10 * decade
	return '%d-01-01' % start, ('2099-12-31' if decade >= 9 else '%d-12-31' % (start + 9))


def valid_path(path):
	return isinstance(path, str) and bool(PATH.match(path))


def valid_term(term):
	return isinstance(term, str) and bool(TERM.match(term))


# Numeric id of a recording from its website path, e.g. /mediateka/irasas/2000902155/panorama.
def recording_id(url):
	match = RECORDING.match(url) if isinstance(url, str) else None
	return match.group(1) if match else None


def _int(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


def _text(value):
	return html.unescape(value).strip() if isinstance(value, str) else ''


# Picture in one size from the website's naming scheme /img/2026/10/23/2519918-459798-{WxH}.jpg. Records
# carry either the template (img, photo) or its two halves (img_path_prefix + img_path_postfix).
def image(record, size, prefix='img_path_prefix', postfix='img_path_postfix'):
	template = None
	for key in ('img', 'photo', 'img_path', 'image'):
		value = record.get(key)
		if isinstance(value, str) and '{WxH}' in value:
			template = value
			break
	if not template and isinstance(record.get(prefix), str) and record[prefix]:
		template = record[prefix] + '{WxH}' + (record.get(postfix) or '.jpg')
	if not template or not template.startswith('/img/'):
		return None
	return BASE + template.replace('{WxH}', size)


# 'YYYY-MM-DD HH:MM:SS' for Kodi from the website's "2026.10.23 21:19", or '' when it doesn't look like one.
def kodi_date(value):
	match = re.match(r'^(\d{4})\.(\d\d)\.(\d\d) (\d\d):(\d\d)', value) if isinstance(value, str) else None
	return '%s-%s-%s %s:%s:00' % match.groups() if match else ''


# One recording in a uniform shape, from a search item or a category article; None when the
# record isn't a playable recording (articles, Epika titles, records without an id).
#	id, title, show, season, channel, date (Kodi format), duration (seconds or None), audio,
#	age ('N-14'... or ''), subtitles (bool), thumb, fanart
def recording(record):
	if not isinstance(record, dict) or record.get('is_epika'):
		return None
	rid = recording_id(record.get('url'))
	title = _text(record.get('title'))
	if not rid or not title:
		return None
	age = record.get('age_restriction')
	if not isinstance(age, str) or not re.match(r'^N-\d+$', age):
		age = 'N-18' if record.get('n18') or record.get('n-18') else ''
	return {
		'id': rid,
		'title': title,
		'show': _text(record.get('category_title')),
		'season': _text(record.get('lrt_season_title')),
		'channel': _text(record.get('channel_title')),
		'date': kodi_date(record.get('item_date') or record.get('date')),
		'duration': _int(record.get('media_duration_sec') or record.get('duration_sec')) or None,
		'audio': bool(record.get('is_audio')) or '/radioteka/' in record['url'],
		'age': age,
		'subtitles': bool(record.get('has_subtitles') or record.get('cc')),
		'thumb': image(record, THUMB),
		'fanart': image(record, FANART),
	}


# A show's info from a category response: title, description, artwork and its seasons, newest
# first, as [(label, path)]. Seasons are named after their year or number ("2024", "3 sezonas").
def show(info):
	if not isinstance(info, dict):
		return None
	seasons = []
	for season in info.get('season_info') or []:
		if isinstance(season, dict) and valid_path(season.get('season_url')):
			seasons.append((_text(season.get('season_title')) or season['season_url'].rsplit('/', 1)[1], season['season_url']))
	# Numeric titles (years, season numbers) newest first, the rest after them by name.
	seasons.sort(key=lambda s: (0, -_int(re.match(r'\d*', s[0]).group() or 0)) if re.match(r'\d', s[0]) else (1, s[0]))
	images = info.get('category_images')
	img = images.get('img1') if isinstance(images, dict) and isinstance(images.get('img1'), dict) else {}
	return {
		'title': _text(info.get('category_title')),
		'plot': plain(info.get('description')),
		'thumb': image(img, THUMB),
		'fanart': image(img, FANART),
		'seasons': seasons,
	}


# The website's HTML descriptions as plain text with line breaks, for Kodi's plot.
def plain(text):
	if not isinstance(text, str) or not text:
		return ''
	text = re.sub(r'(?i)<br\s*/?>|</p>\s*', '\n', text)
	text = re.sub(r'<[^>]+>', '', text)
	text = html.unescape(text)
	return re.sub(r'\n{3,}', '\n\n', text).strip()


# The A-Z show index page as [(letter, title, path, on_air)]. The page is a list of
# <h3 class="tag-list-block__title">A</h3> headers, each followed by links; links of shows with a
# current season carry data-current-season.
SHOW_INDEX = re.compile(
	r'<h3 class="tag-list-block__title">([^<]*)</h3>'
	r'|<li class="tag-list-block__item"\s*(data-current-season)?\s*>\s*<a class="tag-list-block__link" href="([^"]+)"[^>]*\btitle="([^"]*)"')


def parse_show_index(page):
	shows = []
	letter = ''
	for header, current, path, title in SHOW_INDEX.findall(page or ''):
		if header:
			letter = _text(header)
		elif valid_path(path) and _text(title):
			shows.append((letter, _text(title), path, bool(current)))
	return shows


class Api:
	def __init__(self, cache=None, log=None):
		self.cache = cache
		self.log = log or (lambda msg: None)

	# GET from www.lrt.lt, answered from the cache when ttl allows. Returns the parsed JSON (or the
	# text, json=False). Raises ApiError on network problems, HTTP errors and non-JSON answers.
	def get(self, path, params=None, ttl=0, json_body=True):
		url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
		key = 'GET ' + url
		if ttl and self.cache:
			cached = self.cache.get(key, ttl)
			if cached is not None:
				return cached
		self.log('GET %s' % url)
		request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'})
		try:
			with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
				data = response.read(MAX_RESPONSE + 1)
		except urllib.error.HTTPError as err:
			raise ApiError(err.code, url)
		except (urllib.error.URLError, OSError, ValueError) as err:
			raise ApiError(0, '%s: %s' % (url, err))
		if len(data) > MAX_RESPONSE:
			raise ApiError(0, 'response too large: %s' % url)
		text = data.decode('utf-8', 'replace')
		if json_body:
			if not text.strip():
				raise ApiError(404, 'empty answer: %s' % url)
			try:
				result = json.loads(text)
			except ValueError:
				raise ApiError(0, 'not JSON: %s' % url)
		else:
			result = text
		if ttl and self.cache:
			self.cache.set(key, result)
		return result

	# --- recordings ---

	# One page of video recordings, newest first: (recordings, total). Filters: q (text), term
	# (theme such as /gyvenimas), decade (number 1-9 from facets(), searched as a date range:
	# the API ignores its own year_interval parameter) and heritage (the archive collection).
	def search(self, page=1, count=50, q=None, term=None, decade=None, heritage=False):
		params = {'type': VIDEO, 'order': 'desc', 'count': count, 'page': page}
		if q:
			params['q'] = q
		if term:
			params['term'] = term
		if decade:
			params['dfrom'], params['dto'] = decade_range(decade)
		if heritage:
			params['heritage'] = 1
		data = self.get('/api/json/search', params, ttl=NEWEST_TTL if not q else LIST_TTL)
		found = data.get('items') if isinstance(data, dict) else None
		total = _int(data.get('total_found')) if isinstance(data, dict) else 0
		return [r for r in map(recording, found or []) if r], total or 0

	# Search facets for video: themes [(title, term)] and decades [(title, number)], only those
	# with recordings. Decade numbers are the website's: 1 before 1950, 2 the 1950s ... 9 since 2020.
	# The themes are what the website calls "Mediateka / Gyvenimas" etc.
	def facets(self):
		data = self.get('/api/json/search', {'type': VIDEO, 'count': 1}, ttl=FACETS_TTL)
		facets = data.get('facets') if isinstance(data, dict) else None
		facets = facets if isinstance(facets, dict) else {}
		themes = []
		for entry in facets.get('themes') or []:
			if isinstance(entry, dict) and entry.get('key') == 'term' and valid_term(entry.get('value')) and _int(entry.get('count')):
				themes.append((_text(entry.get('title')).replace('Mediateka / ', ''), entry['value']))
		decades = []
		for entry in facets.get('year_interval') or []:
			value = _int(entry.get('value')) if isinstance(entry, dict) else None
			if value and _int(entry.get('count')):
				decades.append((_text(entry.get('title')), value))
		return themes, decades

	# A show or season page: (show info from show(), recordings, next page or None). path is the
	# show's or season's website path, e.g. /mediateka/video/panorama or .../panorama/2026.
	def category(self, path, page=1, count=50):
		data = self.get('/api/json/category', {'term': path, 'page': page, 'count': count}, ttl=LIST_TTL)
		if not isinstance(data, dict):
			return None, [], None
		records = [r for r in map(recording, data.get('articles') or []) if r]
		next_page = _int(data.get('next_page')) if records else None
		return show(data.get('category_info')), records, next_page

	# The A-Z show index, kind 'tv' or 'radio', from parse_show_index().
	def shows(self, kind):
		key = 'shows ' + kind
		if self.cache:
			cached = self.cache.get(key, INDEX_TTL)
			if cached is not None:
				return [tuple(s) for s in cached]
		shows = parse_show_index(self.get(SHOW_INDEXES[kind], json_body=False))
		if shows and self.cache:
			self.cache.set(key, shows)
		return shows

	# --- playback ---

	# A recording's stream and description: {'url', 'title', 'plot', 'show', 'date', 'thumb'}.
	# Never cached: it is fetched once, when the recording is played.
	def media(self, rid):
		data = self.get('/servisai/stream_url/vod/media_info/', {'url': '/mediateka/irasas/%s/x' % rid})
		item = data.get('playlist_item') if isinstance(data, dict) else None
		link = item.get('file') if isinstance(item, dict) else None
		if not isinstance(link, str) or not link.startswith('https://'):
			raise ApiError(404, 'no stream for %s' % rid)
		return {
			'url': link,
			'title': _text(data.get('title')) or _text(item.get('title')),
			'plot': plain(data.get('content')),
			'show': _text(item.get('category')),
			'date': kodi_date(data.get('date')),
			'thumb': image({'img': (item.get('image') or '').replace(FANART, '{WxH}')}, FANART),
		}

	# A live channel's current stream address. The website signs it for this IP for a few hours, so it is
	# fetched on every play. 'content' is the main stream (with closed captions on the main TV channel);
	# 'content2' an older stream kept as a fallback. Raises ApiError(451) when the website says the
	# channel is restricted (rights for some broadcasts are limited to Lithuania).
	def live(self, channel):
		if channel not in CHANNEL_IDS:
			raise ApiError(400, 'unknown channel %r' % channel)
		data = self.get('/servisai/stream_url/live/get_live_url.php', {'channel': channel})
		info = data.get('response', {}).get('data') if isinstance(data, dict) and isinstance(data.get('response'), dict) else None
		info = info if isinstance(info, dict) else {}
		for key in ('content', 'content2'):
			link = info.get(key)
			if isinstance(link, str) and link.startswith('https://'):
				return link
		if info.get('restriction'):
			raise ApiError(451, 'restricted: %s' % info['restriction'])
		raise ApiError(404, 'no stream for channel %s' % channel)
