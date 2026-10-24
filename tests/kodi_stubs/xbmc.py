
#
# kodi_stubs/xbmc.py – test stand-in for Kodi's xbmc module (only what the add-on uses).
#
# Records log lines (LOG) and builtins such as Container.Refresh (BUILTINS).
#

LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR = 0, 1, 2, 3
LOG = []
BUILTINS = []


def log(msg, level=LOGDEBUG):
	assert isinstance(msg, str)
	LOG.append((level, msg))


def executebuiltin(command, wait=False):
	BUILTINS.append(command)

