#
# helpers.py – shared plumbing for the test suite: fake Kodi, a fake website and a plugin runner.
#
# The tests run on any PC with plain Python, without Kodi and without network access:
#	- the folder kodi_stubs/ is put on sys.path, so "import xbmc" etc. load small stand-ins
#	  that record what the add-on does (listed items, notifications, resolved playback)
#	- FakeSite replaces urllib.request.urlopen and answers the add-on's requests from the
#	  synthetic data below; tests can change routes or inject errors per path
#	- KodiTestCase gives every test a fresh temporary profile folder and clean stub state, and
#	  invoke() runs the add-on exactly like Kodi: fresh modules, sys.argv = [url, handle, query]
#
# All data here is made up and only mirrors the shape of real website responses.
# When the website changes a response format, update the matching structure here first, then the code.
#

import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDON = os.path.join(ROOT, 'plugin.video.mediateka')
# Import paths: the Kodi stubs, the add-on (for "resources.lib...") and the repo root (build scripts).
for path in (os.path.join(HERE, 'kodi_stubs'), ADDON, ROOT):
	if path not in sys.path:
		sys.path.insert(0, path)

import xbmc  # noqa: E402  (stub)
import xbmcaddon  # noqa: E402
import xbmcgui  # noqa: E402
import xbmcplugin  # noqa: E402
import xbmcvfs  # noqa: E402

# --- synthetic website data ---
# Two search hits (one an Epika title the add-on must skip), a show with two seasons (Quiz) and
# one with a single season (Cooking), the TV show index with letters and an unsafe link, and the
# playback answers for recording 2000000001 and two live channels.

SEARCH_ITEMS = [
	{'id': 1, 'title': 'Quiz &amp; friends', 'url': '/mediateka/irasas/2000000001/quiz', 'photo': '/img/2026/10/01/1-2-{WxH}.jpg',
		'date': '2026.10.23 21:19', 'category_title': 'Quiz', 'age_restriction': 'N-14'},
	{'id': 2, 'title': 'Epika film', 'url': '/mediateka/irasas/2000000002/film', 'is_epika': 1},
	{'id': 3, 'title': 'News article', 'url': '/naujienos/lietuvoje/2/3/x'},
]
FACETS = {
	'themes': [
		{'title': 'Lituanica', 'key': 'category_term_1', 'value': '/lituanica', 'count': 0},
		{'title': 'Mediateka / Gyvenimas', 'key': 'term', 'value': '/gyvenimas', 'count': 20},
		{'title': 'Mediateka / Empty', 'key': 'term', 'value': '/empty', 'count': 0},
	],
	'year_interval': [{'title': '1960 - 1970', 'value': 3, 'count': 5}, {'title': 'nuo 2020', 'value': 9, 'count': 9}],
}
ARTICLE = {'title': 'Quiz 2026-10-23', 'url': '/mediateka/irasas/2000000001/quiz', 'img_path_prefix': '/img/2026/10/01/1-2-',
	'img_path_postfix': '.jpg', 'media_duration_sec': 2991, 'item_date': '2026.10.23 21:19', 'category_title': 'Quiz',
	'lrt_season_title': '2026', 'channel_title': 'TELEVIZIJA'}
QUIZ_INFO = {'category_title': 'Quiz', 'description': '<p>A quiz.</p>', 'category_images': {'img1': {'img_path': '/img/2025/01/01/9-9-{WxH}.jpg'}},
	'season_info': [
		{'season_title': '2025', 'season_url': '/mediateka/video/quiz/2025'},
		{'season_title': '2026', 'season_url': '/mediateka/video/quiz/2026'},
		{'season_title': 'bad', 'season_url': '/mediateka/video/../etc'},
	]}
COOKING_INFO = {'category_title': 'Cooking', 'season_info': [{'season_title': '2024', 'season_url': '/mediateka/video/cooking/2024'}]}
SHOW_INDEX = '''<div>
<h3 class="tag-list-block__title">C</h3>
<ul><li class="tag-list-block__item" ><a class="tag-list-block__link" href="/mediateka/video/cooking" target="_blank" title="Cooking"><span>Cooking</span></a></li>
<li class="tag-list-block__item" ><a class="tag-list-block__link" href="https://evil.example/x" target="_blank" title="Evil"><span>Evil</span></a></li></ul>
<h3 class="tag-list-block__title">Q</h3>
<ul><li class="tag-list-block__item" data-current-season><a class="tag-list-block__link" href="/mediateka/video/quiz" target="_blank" title="Quiz"><span>Quiz</span></a></li></ul>
</div>'''
RADIO_INDEX = '''<h3 class="tag-list-block__title">Ž</h3>
<li class="tag-list-block__item" ><a class="tag-list-block__link" href="/radioteka/laida/zinios" title="Žinios">Žinios</a></li>'''
MEDIA = {'id': '2000000001', 'title': 'Quiz 2026-10-23', 'content': '<p>Players: A, B.</p>', 'date': '2026.10.23 21:19',
	'playlist_item': {'file': 'https://stream-vod.example/quiz.mp4/playlist.m3u8', 'category': 'Quiz', 'image': '/img/2026/10/01/1-2-1287x836.jpg'}}
LIVE = {
	'LTV1': {'response': {'data': {'content': 'https://stream.example/ltv1/master.m3u8?Expires=1', 'content2': 'https://stream.example/old.m3u8', 'restriction': ''}}},
	'WORLD': {'response': {'data': {'content': '', 'content2': '', 'restriction': 'LT'}}},
}


def search(query):
	if query.get('count') == '1':
		return {'items': [], 'total_found': 0, 'facets': FACETS}
	if query.get('q') == 'nothing':
		return {'items': [], 'total_found': '0'}
	return {'items': SEARCH_ITEMS, 'total_found': '120'}


def category(query):
	term = query.get('term')
	if term == '/mediateka/video/quiz':
		return {'category_info': QUIZ_INFO, 'articles': [ARTICLE], 'next_page': 2}
	if term.startswith('/mediateka/video/quiz/') or term == '/mediateka/video/cooking':
		return {'category_info': COOKING_INFO, 'articles': [ARTICLE, {'title': 'no url'}], 'next_page': 2 if query.get('page') == '1' else None}
	return {'category_info': None, 'articles': [], 'next_page': None}


def media(query):
	return MEDIA if query.get('url', '').startswith('/mediateka/irasas/2000000001/') else ''


# Fake www.lrt.lt. Installed as urllib.request.urlopen, so the add-on's real HTTP code runs.
#	routes    path -> response data (dict, or str for HTML), or a function of the query parameters
#	errors    path -> HTTP status, to make a request fail
#	requests  every requested URL, for assertions
# Unknown paths answer 404, other hosts fail like a network error.
class FakeSite:

	def __init__(self):
		self.requests = []
		self.routes = {
			'/api/json/search': search,
			'/api/json/category': category,
			'/mediateka/tv-laidos': SHOW_INDEX,
			'/mediateka/radijo-laidos': RADIO_INDEX,
			'/servisai/stream_url/vod/media_info/': media,
			'/servisai/stream_url/live/get_live_url.php': lambda q: LIVE.get(q.get('channel'), ''),
		}
		self.errors = {}

	def urlopen(self, req, timeout=None):
		url = req.full_url if isinstance(req, urllib.request.Request) else req
		parts = urllib.parse.urlsplit(url)
		self.requests.append(url)
		if parts.hostname != 'www.lrt.lt':
			raise urllib.error.URLError('no route to %s' % url)
		if parts.path in self.errors:
			raise urllib.error.HTTPError(url, self.errors[parts.path], 'error', {}, io.BytesIO(b''))
		if parts.path not in self.routes:
			raise urllib.error.HTTPError(url, 404, 'not found', {}, io.BytesIO(b''))
		body = self.routes[parts.path]
		body = body(dict(urllib.parse.parse_qsl(parts.query))) if callable(body) else body
		return Response(body.encode('utf-8') if isinstance(body, str) else json.dumps(copy.deepcopy(body)).encode('utf-8'))

	def paths(self):
		return [urllib.parse.urlsplit(u).path for u in self.requests]


# Minimal HTTP response with what api.py reads: read() and use as a context manager.
class Response:
	def __init__(self, data):
		self.stream = io.BytesIO(data)

	def read(self, size=-1):
		return self.stream.read(size)

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False


# Fresh profile folder, fake server and Kodi stub state for every test.
class KodiTestCase(unittest.TestCase):

	def setUp(self):
		self.tmp = tempfile.mkdtemp(prefix='mediateka-test-')
		xbmcvfs.ROOT[0] = self.tmp
		self.server = FakeSite()
		self._urlopen = urllib.request.urlopen
		urllib.request.urlopen = self.server.urlopen
		xbmcplugin.reset()
		xbmcgui.NOTES.clear()
		xbmcgui.ANSWERS.update(yesno=False, input='')
		xbmc.BUILTINS.clear()
		xbmc.LOG.clear()
		xbmcaddon.SETTINGS.clear()
		xbmcaddon.SETTINGS.update(xbmcaddon.DEFAULTS)

	def tearDown(self):
		urllib.request.urlopen = self._urlopen
		shutil.rmtree(self.tmp, ignore_errors=True)

	# Runs the add-on like Kodi does: sys.argv = [base URL, handle, ?query] and freshly imported
	# modules (kodi.py reads sys.argv and settings at import). Returns xbmcplugin.STATE, which
	# holds only this call's results.
	def invoke(self, query='', handle='1'):
		xbmcplugin.reset()
		for name in [m for m in sys.modules if m == 'resources' or m.startswith('resources.')]:
			del sys.modules[name]
		sys.argv = ['plugin://plugin.video.mediateka/', handle, '?' + query]
		from resources.lib import plugin
		plugin.run()
		return xbmcplugin.STATE

	# Labels of the listed entries, the quickest way to compare a listing.
	def labels(self, state=None):
		return [li.label for url, li, folder in (state or xbmcplugin.STATE)['items']]

	def urls(self, state=None):
		return [url for url, li, folder in (state or xbmcplugin.STATE)['items']]

	def errors_logged(self):
		return [m for level, m in xbmc.LOG if level >= xbmc.LOGERROR]
