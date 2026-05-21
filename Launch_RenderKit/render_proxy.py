import bpy

###########################################################################
# Render Proxy Animation primary functionality classes

class render_proxy_start(bpy.types.Operator):
	bl_idname = "render.proxy_animation"
	bl_label = "Render Proxy Animation"
	bl_description = "Temporarily reduce render quality for quickly creating animation proxies"
	
	def execute(self, context):
		prefs = context.preferences.addons[__package__].preferences
		
		# Save original render engine settings
		original_renderEngine = bpy.context.scene.render.engine
		original_renderSamples = bpy.context.scene.eevee.taa_render_samples
		
		# Save original file format settings
		original_format = bpy.context.scene.render.image_settings.file_format
		original_colormode = bpy.context.scene.render.image_settings.color_mode
		original_colordepth = bpy.context.scene.render.image_settings.color_depth
		
		# Save original resolution multiplier settings
		original_resolutionMultiplier = bpy.context.scene.render.resolution_percentage
		
		# Save original nodal compositing settings
		original_compositing = bpy.context.scene.render.use_compositing
		
		# Override render engine settings
		bpy.context.scene.render.engine = str(prefs.proxy_renderEngine)
		bpy.context.scene.eevee.taa_render_samples = prefs.proxy_renderSamples
		
		# Override original file format settings
		if prefs.proxy_format == 'JPEG':
			bpy.context.scene.render.image_settings.file_format = 'JPEG'
		elif prefs.proxy_format == 'PNG':
			bpy.context.scene.render.image_settings.file_format = 'PNG'
		elif prefs.proxy_format == 'OPEN_EXR_MULTILAYER':
			bpy.context.scene.render.image_settings.file_format = 'OPEN_EXR_MULTILAYER'
		
		# Override original resolution multiplier settings
		bpy.context.scene.render.resolution_percentage = prefs.proxy_resolutionMultiplier
		
		# Override original nodal compositing settings
		if prefs.proxy_compositing == "ON":
			bpy.context.scene.render.use_compositing = True
		elif prefs.proxy_compositing == "OFF":
			bpy.context.scene.render.use_compositing = False
		
		
		
		# Render
		bpy.ops.render.render(animation=True, use_viewport=True)
		
		
		
		# Restore original render engine settings
		bpy.context.scene.render.engine = original_renderEngine
		bpy.context.scene.eevee.taa_render_samples = original_renderSamples
		
		# Restore original file format settings
		bpy.context.scene.render.image_settings.file_format = original_format
		bpy.context.scene.render.image_settings.color_mode = original_colormode
		bpy.context.scene.render.image_settings.color_depth = original_colordepth
		
		# Restore original resolution multiplier settings
		bpy.context.scene.render.resolution_percentage = original_resolutionMultiplier
		
		# Restore original nodal compositing settings
		bpy.context.scene.render.use_compositing = original_compositing
		
		return {'FINISHED'}

###########################################################################
# Menu UI rendering class

def render_proxy_menu_item(self,context):
	try:
		if context.preferences.addons[__package__].preferences.proxy_enable:
			layout = self.layout
			layout.operator(render_proxy_start.bl_idname, text="Render Proxy Animation", icon='RENDER_ANIMATION')
	except Exception as exc:
		print(str(exc) + " Render Kit | Error in Topbar Mt Render when adding to menu")



###########################################################################
# Addon registration functions
# •Define classes being registered
# •Define keymap array
# •Registration function
# •Unregistration function

classes = (render_proxy_start,)

keymaps = []

def register():
	# Register classes
	for cls in classes:
		bpy.utils.register_class(cls)

	# Add menu item
	bpy.types.TOPBAR_MT_render.prepend(render_proxy_menu_item)

	# Add keymaps for proxy rendering
	wm = bpy.context.window_manager
	kc = wm.keyconfigs.addon
	if kc:
		km = wm.keyconfigs.addon.keymaps.new(name='Screen Editing', space_type='EMPTY')
		kmi = km.keymap_items.new(render_proxy_start.bl_idname, 'RET', 'PRESS', ctrl=True, alt=True, shift=True)
		keymaps.append((km, kmi))
	if kc:
		km = wm.keyconfigs.addon.keymaps.new(name='Screen Editing', space_type='EMPTY')
		kmi = km.keymap_items.new(render_proxy_start.bl_idname, 'RET', 'PRESS', oskey=True, alt=True, shift=True)
		keymaps.append((km, kmi))
	if kc:
		km = wm.keyconfigs.addon.keymaps.new(name='Screen Editing', space_type='EMPTY')
		kmi = km.keymap_items.new(render_proxy_start.bl_idname, 'RET', 'PRESS', ctrl=True, alt=True, shift=True)
		keymaps.append((km, kmi))
	if kc:
		km = wm.keyconfigs.addon.keymaps.new(name='Screen Editing', space_type='EMPTY')
		kmi = km.keymap_items.new(render_proxy_start.bl_idname, 'RET', 'PRESS', oskey=True, alt=True, shift=True)
		keymaps.append((km, kmi))

def unregister():
	# Remove keymaps
	for km, kmi in keymaps:
		km.keymap_items.remove(kmi)
	keymaps.clear()

	# Remove menu item
	bpy.types.TOPBAR_MT_render.remove(render_proxy_menu_item)

	# Deregister classes
	for cls in reversed(classes):
		bpy.utils.unregister_class(cls)

if __package__ == "__main__":
	register()
