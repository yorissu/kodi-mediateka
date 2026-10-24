#
# test_plugin.py – runs the add-on's plugin calls against the fake website server (helpers.py).
#
# Each test is one or a few plugin:// calls as Kodi would make them, checking what the add-on
# lists, plays or tells the user. Run: python -m unittest discover -s tests
#

import urllib.parse

from helpers import KodiTestCase  # first: puts the Kodi stubs on sys.path

import xbmc
import xbmcgui


def q(**params):
	return urllib.parse.urlencode(params)


class Menus(KodiTestCase):

	def test_root(self):
		state = self.invoke()
		self.assertEqual(self.labels(), ['Live', 'Newest', 'TV shows', 'Radio shows', 'Categories', 'Archive', 'Search'])
		self.assertEqual(state['ends'], [True])
		self.assertEqual(self.server.requests, [])

	def test_live_channels_and_play(self):
		self.invoke(q(action='live'))
		self.assertIn('Televizija', self.labels())
		state = self.invoke(q(action='live_play', channel='LTV1'))
		ok, li = state['resolved'][0]
		self.assertTrue(ok)
		self.assertEqual(li.path, 'https://stream.example/ltv1/master.m3u8?Expires=1')
		self.assertEqual(li.mime, 'application/vnd.apple.mpegurl')

	def test_live_restricted(self):
		state = self.invoke(q(action='live_play', channel='WORLD'))
		self.assertEqual(state['resolved'][0][0], False)
		self.assertEqual(xbmcgui.NOTES, ['Not available in your country'])

	def test_newest_skips_non_recordings_and_pages(self):
		state = self.invoke(q(action='newest'))
		self.assertEqual(self.labels(), ['Quiz & friends', 'Next page (2)'])
		url, li, folder = state['items'][0]
		self.assertFalse(folder)
		self.assertEqual(url, 'plugin://plugin.video.mediateka/?action=play&id=2000000001')
		self.assertEqual(li.art['thumb'], 'https://www.lrt.lt/img/2026/10/01/1-2-756x425.jpg')
		self.assertEqual(li.tag.data['Mpaa'], 'N-14')
		self.assertEqual(li.tag.data['FirstAired'], '2026-10-23')
		self.assertEqual(state['content'], 'episodes')

	def test_themes_and_decades(self):
		self.invoke(q(action='themes'))
		self.assertEqual(self.labels(), ['Gyvenimas'])
		self.assertIn('term=%2Fgyvenimas', self.urls()[0])
		self.invoke(q(action='decades'))
		self.assertEqual(self.labels(), ['Heritage collection', 'nuo 2020', '1960 - 1970'])


class Shows(KodiTestCase):

	def test_index_letters_and_on_air(self):
		self.invoke(q(action='shows', kind='tv'))
		self.assertEqual(self.labels(), ['On air now', 'C  (1)', 'Q  (1)'])
		self.invoke(q(action='shows_list', kind='tv', letter='*'))
		self.assertEqual(self.labels(), ['Quiz'])
		self.invoke(q(action='shows_list', kind='tv', letter='C'))
		self.assertEqual(self.labels(), ['Cooking'])  # the off-site link is dropped

	def test_show_with_seasons(self):
		state = self.invoke(q(action='show', path='/mediateka/video/quiz'))
		self.assertEqual(self.labels(), ['All recordings', 'Season 2026', 'Season 2025'])
		self.assertEqual(state['content'], 'seasons')
		self.assertEqual(state['items'][1][1].tag.data['Plot'], 'A quiz.')

	def test_show_with_one_season_lists_recordings(self):
		state = self.invoke(q(action='show', path='/mediateka/video/cooking'))
		self.assertEqual(self.labels(), ['Quiz 2026-10-23', 'Next page (2)'])
		li = state['items'][0][1]
		self.assertEqual(li.tag.data['Duration'], 2991)
		self.assertEqual(li.tag.data['Plot'], 'Season 2026\n2026-10-23 21:19 TELEVIZIJA')
		self.invoke(q(action='recordings', path='/mediateka/video/cooking', page=2))
		self.assertEqual(self.labels(), ['Quiz 2026-10-23'])


class Search(KodiTestCase):

	def test_search_lists_shows_then_recordings_and_remembers(self):
		xbmcgui.ANSWERS['input'] = '  zinios '
		self.invoke(q(action='search_input'))
		self.assertEqual(xbmc.BUILTINS, ['Container.Update(plugin://plugin.video.mediateka/?action=results&q=zinios)'])
		self.invoke(q(action='results', q='zinios'))
		self.assertEqual(self.labels()[0], 'Žinios [show]')
		self.assertIn('Quiz & friends', self.labels())
		self.invoke(q(action='search'))
		self.assertEqual(self.labels(), ['[B]New search[/B]', 'zinios'])
		self.invoke(q(action='search_forget', q='zinios'))
		self.invoke(q(action='search'))
		self.assertEqual(self.labels(), ['[B]New search[/B]'])

	def test_nothing_found(self):
		self.invoke(q(action='results', q='nothing'))
		self.assertEqual(self.labels(), [])
		self.assertEqual(xbmcgui.NOTES, ['Nothing found'])


class Playback(KodiTestCase):

	def test_play(self):
		state = self.invoke(q(action='play', id='2000000001'))
		ok, li = state['resolved'][0]
		self.assertTrue(ok)
		self.assertEqual(li.path, 'https://stream-vod.example/quiz.mp4/playlist.m3u8')
		self.assertEqual(li.tag.data['Plot'], 'Players: A, B.')
		self.assertEqual(li.art['thumb'], 'https://www.lrt.lt/img/2026/10/01/1-2-1287x836.jpg')

	def test_missing_recording(self):
		state = self.invoke(q(action='play', id='999'))
		self.assertEqual(state['resolved'][0][0], False)
		self.assertEqual(xbmcgui.NOTES, ['This recording is not available'])

	def test_network_error_on_listing(self):
		self.server.errors['/api/json/search'] = 503
		state = self.invoke(q(action='newest'))
		self.assertEqual(state['ends'], [False])
		self.assertEqual(xbmcgui.NOTES, ['Could not reach Mediateka'])
		self.assertEqual(self.errors_logged(), [])


class Validation(KodiTestCase):

	def test_bad_parameters_never_reach_the_website(self):
		for query in (q(action='show', path='/mediateka/../etc'), q(action='show', path='https://evil.example/a/b'),
				q(action='play', id='12a'), q(action='live_play', channel='X'), q(action='theme', term='/a?b'),
				q(action='newest', page='0'), q(action='shows', kind='tv/../x'), q(action='nope')):
			state = self.invoke(query)
			self.assertEqual(self.server.requests, [], query)
			self.assertTrue(state['ends'] == [False] or state['resolved'][0][0] is False, query)
		self.assertEqual(self.errors_logged(), [])
