import bpy

###########################################################################
# Panels with a tab category in the extension preferences are registered separately
# from the rest of their module's classes so the category can be changed without
# reloading the extension.
#
# Each panel class must define "category_preference", the name of the string property
# in RenderKitPreferences that holds its tab category.

def register_panel(cls):
	# Unregister first, allowing the category of an already visible panel to be replaced
	unregister_panel(cls)
	category = getattr(bpy.context.preferences.addons[__package__].preferences, cls.category_preference, "")
	if len(category) > 0:
		cls.bl_category = category
		bpy.utils.register_class(cls)

def unregister_panel(cls):
	try:
		bpy.utils.unregister_class(cls)
	except RuntimeError:
		pass

def register_panels(panels):
	# Subpanels are removed before their parent, and registered after it
	unregister_panels(panels)
	for cls in panels:
		register_panel(cls)

def unregister_panels(panels):
	for cls in reversed(panels):
		unregister_panel(cls)
