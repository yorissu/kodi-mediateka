#
# plugin.py – the add-on's brain: decides what each plugin call does and builds Kodi's listings.
#
# How a Kodi plugin works
#	Kodi calls default.py with a plugin:// URL, e.g. plugin://plugin.video.mediateka/?action=show&path=/mediateka/video/panorama.
#	run() reads the "action" and its parameters, checks them and calls the matching Plugin method.
#	A listing method adds items (each with its own plugin:// URL for when it is selected) and ends
#	with endOfDirectory; playback ends with setResolvedUrl. Every item URL is built with kodi.url().
#
# The menu structure, mirroring www.lrt.lt/mediateka
#	root          Live, Newest, TV shows, Radio shows, Categories, Archive by decade, Search
#	live          the TV and radio channels; live_play fetches a fresh signed stream address
#	newest        the newest video recordings, paged
#	shows         a show index (TV or radio): "On air now", then one folder per letter
#	show          a show: its seasons (plus "All recordings"), or straight to the recordings when
#	              it has only one season
#	recordings    the recordings of a show or season, paged
#	themes/theme  the website's themes (Gyvenimas, Filmai...) and their recordings
#	decades       the archive: the heritage collection (heritage) and one folder per decade (decade)
#	search        the search menu with recent searches; results list matching shows first
#	play          resolves a recording for Kodi's player
#
# Watched state and resume points are Kodi's own: recordings are playable items with a stable
# plugin:// URL, so Kodi remembers them like any library file.
#
# Safety
#	Any add-on, skin or remote control app can call plugin:// URLs, so nothing in them is trusted:
#	the routes table validates every parameter (ids must be numeric, paths plain paths, channels
#	from the fixed list...) before the website is asked anything, and rejected calls are only logged.
#	Errors never surface as a Kodi script error: run() catches them, tells the user in a
#	notification and always gives Kodi the answer it is waiting for.
#

import sys
import traceback
import unicodedata
import urllib.parse

import xbmc
import xbmcgui
import xbmcplugin

from . import kodi
from .cache import Cache, read_json, write_json
from .api import Api, ApiError, CHANNELS, CHANNEL_IDS, SHOW_INDEXES, valid_path, valid_term

# Recent searches kept, and the longest search text sent to the website.
HISTORY_SIZE = 15
MAX_QUERY = 100
# Shows listed above the recordings in search results, at most.
MAX_SHOW_HITS = 50
# Highest page number accepted from a URL.
MAX_PAGE = 1000
HLS = 'application/vnd.apple.mpegurl'


# A plugin URL with parameters we did not produce (another add-on, a stale favourite).
class BadRequest(Exception):
	pass


# Lower case without accents, so "zinios" finds "Žinios" in show titles.
def fold(text):
	return ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))


# Everything one plugin call needs: the cache and the website client, plus one method per action.
# A new Plugin is created for every call (see run()).
class Plugin:
	def __init__(self):
		self.cache = Cache(kodi.profile_path('cache'))
		self.api = Api(self.cache, log=kodi.log)
		self.page_size = min(max(kodi.setting_int('page_size') or 50, 25), 100)
		self.history_path = kodi.profile_path('search_history.json')

	# --- helpers ---

	# Adds a folder entry (a menu, a show, a season...) to the current listing.
	def folder(self, label, link, art=None, plot=None, menu=None):
		li = xbmcgui.ListItem(label, offscreen=True)
		if art:
			li.setArt({k: v for k, v in art.items() if v})
		if plot:
			li.getVideoInfoTag().setPlot(plot)
		if menu:
			li.addContextMenuItems(menu)
		xbmcplugin.addDirectoryItem(kodi.HANDLE, link, li, True)

	# Finishes the listing. content lets skins pick fitting views; sort adds Kodi's sort options;
	# cache=False for listings that change (search menu).
	def end(self, content=None, sort=False, cache=True):
		if content:
			xbmcplugin.setContent(kodi.HANDLE, content)
		if sort:
			for method in (xbmcplugin.SORT_METHOD_UNSORTED, xbmcplugin.SORT_METHOD_LABEL,
					xbmcplugin.SORT_METHOD_DATE, xbmcplugin.SORT_METHOD_DURATION):
				xbmcplugin.addSortMethod(kodi.HANDLE, method)
		xbmcplugin.endOfDirectory(kodi.HANDLE, cacheToDisc=cache)

	# For menu entries that run an action instead of opening a folder (New search): Kodi opened
	# them as a folder and waits for a listing, so it is answered with "no listing", which keeps
	# Kodi on the current screen. Calls from settings buttons have no listing (HANDLE -1).
	@staticmethod
	def no_listing():
		if kodi.HANDLE >= 0:
			xbmcplugin.endOfDirectory(kodi.HANDLE, succeeded=False, cacheToDisc=False)

	# Playable list item for one recording from api.recording(). The plot shows what lists know
	# (show, season, channel, date); the full description is added when the recording is played.
	# in_show leaves the show's name out of the plot when the listing is that show.
	@staticmethod
	def recording_item(rec, in_show=False):
		li = xbmcgui.ListItem(rec['title'], offscreen=True)
		art = {'thumb': rec['thumb'], 'icon': rec['thumb'], 'fanart': rec['fanart'], 'landscape': rec['fanart']}
		li.setArt({k: v for k, v in art.items() if v})
		tag = li.getVideoInfoTag()
		tag.setTitle(rec['title'])
		tag.setMediaType('episode' if rec['show'] else 'video')
		lines = []
		if rec['show'] and not in_show:
			lines.append(rec['show'] + (' (%s)' % rec['season'] if rec['season'] else ''))
		elif rec['season']:
			lines.append(kodi.translate(30024) % rec['season'])
		when = ' '.join(p for p in (rec['date'][:16], rec['channel']) if p)
		if when:
			lines.append(when)
		tag.setPlot('\n'.join(lines))
		if rec['show']:
			tag.setTvShowTitle(rec['show'])
		if rec['date']:
			tag.setFirstAired(rec['date'][:10])
			tag.setDateAdded(rec['date'])
		if rec['duration']:
			tag.setDuration(rec['duration'])
		if rec['age']:
			tag.setMpaa(rec['age'])
		tag.setUniqueIDs({'mediateka': rec['id']}, 'mediateka')
		li.setProperty('IsPlayable', 'true')
		return kodi.url(action='play', id=rec['id']), li, False

	def add_recordings(self, records, in_show=False):
		entries = [self.recording_item(r, in_show) for r in records]
		xbmcplugin.addDirectoryItems(kodi.HANDLE, entries, len(entries))

	def next_page(self, page, **params):
		self.folder(kodi.translate(30010) % page, kodi.url(page=page, **params))

	def show_folder(self, title, path, art=None, plot=None):
		self.folder(title, kodi.url(action='show', path=path), art, plot)

	# --- menus ---

	def root(self):
		self.folder(kodi.translate(30000), kodi.url(action='live'))
		self.folder(kodi.translate(30001), kodi.url(action='newest'))
		self.folder(kodi.translate(30002), kodi.url(action='shows', kind='tv'))
		self.folder(kodi.translate(30003), kodi.url(action='shows', kind='radio'))
		self.folder(kodi.translate(30004), kodi.url(action='themes'))
		self.folder(kodi.translate(30005), kodi.url(action='decades'))
		self.folder(kodi.translate(30006), kodi.url(action='search'))
		self.cache.prune(7 * 24 * 3600)
		self.end()

	# TV channels, then radio. Live items always resolve a fresh address (live_play).
	def live(self):
		for channel, name, kind in CHANNELS:
			li = xbmcgui.ListItem(name, offscreen=True)
			tag = li.getVideoInfoTag()
			tag.setTitle(name)
			tag.setPlot(kodi.translate(30025 if kind == 'tv' else 30026))
			li.setProperty('IsPlayable', 'true')
			xbmcplugin.addDirectoryItem(kodi.HANDLE, kodi.url(action='live_play', channel=channel), li, False)
		self.end('videos', cache=False)

	def newest(self, page=1):
		records, total = self.api.search(page, self.page_size)
		self.add_recordings(records)
		if page * self.page_size < total:
			self.next_page(page + 1, action='newest')
		self.end('episodes', sort=True)

	# --- shows ---

	# A show index: "On air now" first, then the letters as the website groups them (0-9, A, Ą...).
	def shows(self, kind):
		shows = self.api.shows(kind)
		self.folder(kodi.translate(30007), kodi.url(action='shows_list', kind=kind, letter='*'))
		letters = []
		for letter, title, path, on_air in shows:
			if letter and letter not in letters:
				letters.append(letter)
		for letter in letters:
			count = sum(1 for s in shows if s[0] == letter)
			self.folder('%s  (%d)' % (letter, count), kodi.url(action='shows_list', kind=kind, letter=letter))
		self.end()

	# The shows of one letter, or letter '*' for the shows on air now.
	def shows_list(self, kind, letter):
		for first, title, path, on_air in self.api.shows(kind):
			if (on_air if letter == '*' else first == letter):
				self.show_folder(title, path)
		self.end('tvshows', sort=letter == '*')

	# One show. With several seasons: "All recordings" and the seasons, newest first, with the
	# show's artwork and description; with one season or none: its recordings directly.
	def show(self, path):
		info, records, next_page = self.api.category(path, 1, self.page_size)
		if info and len(info['seasons']) > 1:
			art = {'thumb': info['thumb'], 'icon': info['thumb'], 'fanart': info['fanart']}
			self.folder(kodi.translate(30009), kodi.url(action='recordings', path=path), art, info['plot'])
			for label, season_path in info['seasons']:
				self.folder(kodi.translate(30024) % label, kodi.url(action='recordings', path=season_path), art, info['plot'])
			self.end('seasons')
			return
		self.add_recordings(records, in_show=True)
		if next_page:
			self.next_page(next_page, action='recordings', path=path)
		self.end('episodes', sort=True)

	# The recordings of a show or season, newest first.
	def recordings(self, path, page=1):
		info, records, next_page = self.api.category(path, page, self.page_size)
		self.add_recordings(records, in_show=True)
		if next_page and next_page > page:
			self.next_page(next_page, action='recordings', path=path)
		self.end('episodes', sort=True)

	# --- themes and decades ---

	def themes(self):
		for title, term in self.api.facets()[0]:
			self.folder(title, kodi.url(action='theme', term=term))
		self.end()

	def theme(self, term, page=1):
		records, total = self.api.search(page, self.page_size, term=term)
		self.add_recordings(records)
		if page * self.page_size < total:
			self.next_page(page + 1, action='theme', term=term)
		self.end('episodes', sort=True)

	# The archive: the heritage collection, then every decade with recordings, newest first.
	def decades(self):
		self.folder(kodi.translate(30008), kodi.url(action='heritage'))
		for title, value in reversed(self.api.facets()[1]):
			self.folder(title, kodi.url(action='decade', value=value))
		self.end()

	def decade(self, value, page=1):
		records, total = self.api.search(page, self.page_size, decade=value)
		self.add_recordings(records)
		if page * self.page_size < total:
			self.next_page(page + 1, action='decade', value=value)
		self.end('episodes', sort=True)

	def heritage(self, page=1):
		records, total = self.api.search(page, self.page_size, heritage=True)
		self.add_recordings(records)
		if page * self.page_size < total:
			self.next_page(page + 1, action='heritage')
		self.end('episodes', sort=True)

	# --- search ---

	def history(self):
		saved = read_json(self.history_path, [])
		return [q for q in saved if isinstance(q, str) and q][:HISTORY_SIZE] if isinstance(saved, list) else []

	def remember(self, query):
		try:
			write_json(self.history_path, [query] + [q for q in self.history() if q != query][:HISTORY_SIZE - 1])
		except OSError as err:
			kodi.log('search history not saved: %s' % err, xbmc.LOGWARNING)

	# The search menu: New search, then recent searches (each with "Remove" in its context menu).
	def search(self):
		self.folder('[B]%s[/B]' % kodi.translate(30011), kodi.url(action='search_input'))
		for query in self.history():
			remove = (kodi.translate(30012), 'RunPlugin(%s)' % kodi.url(action='search_forget', q=query))
			self.folder(query, kodi.url(action='results', q=query), menu=[remove])
		self.end(cache=False)

	# Asks for the text and opens the results in place of the search menu.
	def search_input(self):
		query = xbmcgui.Dialog().input(kodi.translate(30006), type=xbmcgui.INPUT_ALPHANUM).strip()[:MAX_QUERY]
		self.no_listing()
		if query:
			self.remember(query)
			xbmc.executebuiltin('Container.Update(%s)' % kodi.url(action='results', q=query))

	def search_forget(self, q):
		try:
			write_json(self.history_path, [x for x in self.history() if x != q])
		except OSError:
			pass
		xbmc.executebuiltin('Container.Refresh')

	# Results: on the first page the TV and radio shows whose title contains the text, then the
	# recordings the website's search finds, newest first.
	def results(self, q, page=1):
		if page == 1:
			needle = fold(q)
			hits = 0
			for kind in SHOW_INDEXES:
				try:
					shows = self.api.shows(kind)
				except ApiError as err:
					kodi.log('show index failed: %s' % err, xbmc.LOGWARNING)
					continue
				for letter, title, path, on_air in shows:
					if hits < MAX_SHOW_HITS and needle in fold(title):
						self.show_folder(kodi.translate(30013) % title, path)
						hits += 1
		records, total = self.api.search(page, self.page_size, q=q)
		self.add_recordings(records)
		if page * self.page_size < total:
			self.next_page(page + 1, action='results', q=q)
		if page == 1 and not total and not hits:
			kodi.notify(kodi.translate(30023))
		self.end('episodes')

	# --- playback ---

	def play(self, id):
		media = self.api.media(id)
		li = xbmcgui.ListItem(media['title'], path=media['url'], offscreen=True)
		li.setMimeType(HLS)
		li.setContentLookup(False)
		if media['thumb']:
			li.setArt({'thumb': media['thumb'], 'fanart': media['thumb']})
		tag = li.getVideoInfoTag()
		tag.setTitle(media['title'])
		tag.setPlot(media['plot'])
		if media['show']:
			tag.setTvShowTitle(media['show'])
		xbmcplugin.setResolvedUrl(kodi.HANDLE, True, li)

	def live_play(self, channel):
		link = self.api.live(channel)
		name = next(c[1] for c in CHANNELS if c[0] == channel)
		li = xbmcgui.ListItem(name, path=link, offscreen=True)
		li.setMimeType(HLS)
		li.setContentLookup(False)
		li.getVideoInfoTag().setTitle(name)
		xbmcplugin.setResolvedUrl(kodi.HANDLE, True, li)

	# --- settings buttons ---

	def clear_cache(self):
		self.cache.clear()
		kodi.notify(kodi.translate(30105))

	def clear_history(self):
		try:
			write_json(self.history_path, [])
		except OSError:
			pass
		kodi.notify(kodi.translate(30107))


# --- parameter checks for the routes table ---

def page(value):
	number = int(value) if isinstance(value, str) and value.isdigit() else 0
	if not 1 <= number <= MAX_PAGE:
		raise BadRequest('page %r' % value)
	return number


def digits(value):
	if not (isinstance(value, str) and value.isdigit() and len(value) <= 12):
		raise BadRequest('id %r' % value)
	return value


def path(value):
	if not valid_path(value):
		raise BadRequest('path %r' % value)
	return value


def term(value):
	if not valid_term(value):
		raise BadRequest('term %r' % value)
	return value


def kind(value):
	if value not in SHOW_INDEXES:
		raise BadRequest('kind %r' % value)
	return value


def letter(value):
	if not isinstance(value, str) or not 1 <= len(value) <= 4 or '/' in value:
		raise BadRequest('letter %r' % value)
	return value


def channel(value):
	if value not in CHANNEL_IDS:
		raise BadRequest('channel %r' % value)
	return value


def decade(value):
	number = int(value) if isinstance(value, str) and value.isdigit() else 0
	if not 1 <= number <= 9:
		raise BadRequest('decade %r' % value)
	return number


def query(value):
	value = (value or '').strip()
	if not value or len(value) > MAX_QUERY:
		raise BadRequest('query %r' % value)
	return value


# action -> (method name, {parameter: check}, is it played rather than listed). Parameters not
# in the table are ignored; a missing required parameter or a failed check is a BadRequest.
# 'page' is optional everywhere it appears and defaults to 1.
ROUTES = {
	'': ('root', {}, False),
	'live': ('live', {}, False),
	'live_play': ('live_play', {'channel': channel}, True),
	'newest': ('newest', {'page': page}, False),
	'shows': ('shows', {'kind': kind}, False),
	'shows_list': ('shows_list', {'kind': kind, 'letter': letter}, False),
	'show': ('show', {'path': path}, False),
	'recordings': ('recordings', {'path': path, 'page': page}, False),
	'themes': ('themes', {}, False),
	'theme': ('theme', {'term': term, 'page': page}, False),
	'decades': ('decades', {}, False),
	'decade': ('decade', {'value': decade, 'page': page}, False),
	'heritage': ('heritage', {'page': page}, False),
	'search': ('search', {}, False),
	'search_input': ('search_input', {}, None),
	'search_forget': ('search_forget', {'q': query}, None),
	'results': ('results', {'q': query, 'page': page}, False),
	'play': ('play', {'id': digits}, True),
	'clear_cache': ('clear_cache', {}, None),
	'clear_history': ('clear_history', {}, None),
}
OPTIONAL = {'page'}


# Message for a failed call: what the website's status means for the user.
def error_message(err):
	if isinstance(err, ApiError) and err.status == 451:
		return kodi.translate(30022)
	if isinstance(err, ApiError) and err.status == 404:
		return kodi.translate(30021)
	return kodi.translate(30020)


def run():
	params = dict(urllib.parse.parse_qsl(sys.argv[2].lstrip('?'))) if len(sys.argv) > 2 else {}
	action = params.get('action', '')
	route = ROUTES.get(action)
	playable = route[2] if route else False
	try:
		if not route:
			raise BadRequest('unknown action %r' % action)
		method, checks, playable = route
		args = {}
		for name, check in checks.items():
			if name in params:
				args[name] = check(params[name])
			elif name not in OPTIONAL:
				raise BadRequest('missing %s' % name)
		getattr(Plugin(), method)(**args)
	except BadRequest as err:
		kodi.log('rejected %s: %s' % (sys.argv[2] if len(sys.argv) > 2 else '', err), xbmc.LOGWARNING)
		fail(playable)
	except ApiError as err:
		kodi.log('%s failed: %s' % (action or 'root', err), xbmc.LOGWARNING)
		kodi.notify(error_message(err), xbmcgui.NOTIFICATION_WARNING)
		fail(playable)
	except Exception:
		kodi.log('%s crashed:\n%s' % (action or 'root', traceback.format_exc()), xbmc.LOGERROR)
		kodi.notify(kodi.translate(30020), xbmcgui.NOTIFICATION_ERROR)
		fail(playable)


# Gives Kodi the answer it waits for after a failure: a failed playback or a failed listing.
# Actions (playable None) and calls without a listing handle need neither.
def fail(playable):
	if kodi.HANDLE < 0 or playable is None:
		return
	if playable:
		xbmcplugin.setResolvedUrl(kodi.HANDLE, False, xbmcgui.ListItem(offscreen=True))
	else:
		xbmcplugin.endOfDirectory(kodi.HANDLE, succeeded=False, cacheToDisc=False)
