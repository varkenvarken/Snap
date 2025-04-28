#  utils.py
#  a collection of utility functions to be used in Blender add-ons
#
#  (c) 2018 Michel Anders
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.

def load_icons():
	"""
	Load all .png files in the icons subdir of the current package and
	create a preview collection of them that can be used as a source of
	custom icons.
	
	'Current package' means the directory that this file, utils.py, is
	in. Normally in a multi file package that would be in the same dir
	as __init__.py
	"""
	import os
	import bpy
	import bpy.utils

	try: # if anything goes wrong, for example because we are not running 2.75+ we just ignore it
		import bpy.utils.previews
		pcoll = bpy.utils.previews.new()

		# path to the folder where the icon is
		# the path is calculated relative to this py file inside the addon folder
		my_icons_dir = os.path.join(os.path.dirname(__file__), "icons")
		for root, dirs, files in os.walk(my_icons_dir):
			for filename in files:
				if filename.endswith('.png'):
					iconname = filename.replace('.png','_icon')
					pcoll.load(iconname, os.path.join(root, filename), 'IMAGE')
		return pcoll
	except Exception as e:
		print(e)
		return None
