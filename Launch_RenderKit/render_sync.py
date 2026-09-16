import bpy

###########################################################################
# Copies Render and/or Output property categories from the active scene to target scenes.
# Excludes frame range/current frame, stamp/metadata, view layers, the compositor tree, and all pointer datablocks (world, camera, node tree, colour curves)

# Output category scene.render attributes
RENDER_OUTPUT_ATTRS = (
	'resolution_x', 'resolution_y', 'resolution_percentage',
	'pixel_aspect_x', 'pixel_aspect_y',
	'fps', 'fps_base',
	'filepath',
	'use_file_extension', 'use_overwrite', 'use_placeholder', 'use_render_cache',
	'use_compositing', 'use_sequencer', 'dither_intensity',
	'use_multiview', 'views_format',
)

# Excluded scene.render attributes (likely scene-content-specific)
RENDER_NEVER_ATTRS = (
#	'use_border', 'use_crop_to_border',
#	'border_min_x', 'border_max_x', 'border_min_y', 'border_max_y',
	'use_single_layer',
)

# Render Kit's own scene.render_kit_settings attributes
RENDER_KIT_ATTRS = (
	# Autosave images
	'file_location', 'file_name_type', 'file_name_custom', 'file_format',
	# Autosave videos
	'autosave_video_prores', 'autosave_video_prores_quality', 'autosave_video_prores_location',
	'autosave_video_mp4', 'autosave_video_mp4_quality', 'autosave_video_mp4_location',
	'autosave_video_custom', 'autosave_video_custom_command', 'autosave_video_custom_location',
	# Batch rendering (general options only)
	'batch_type', 'batch_range',
	# Render node bake settings (general options only)
	'node_filepath', 'node_filename', 'node_overwrite',
	'node_colorspace', 'node_postprocess', 'node_format', 'node_render_device',
	'node_resolution_x', 'node_resolution_y', 'node_samples', 'node_margin',
)



###########################################################################
# Property copy helpers

def copy_struct(src, dst, include=None, exclude=frozenset(), skip_stamp=False):
	"""Copy writable scalar/enum/bool properties from src to dst.
	include: an ordered sequence of identifiers to copy (skips introspection).
	exclude: identifiers to skip when introspecting.
	skip_stamp: also skip stamp/metadata properties when introspecting.
	Each assignment is guarded so a renamed or invalid value is a no-op."""
	if include is not None:
		ids = [i for i in include if i not in exclude]
	else:
		ids = []
		for prop in src.bl_rna.properties:
			pid = prop.identifier
			if pid == 'rna_type' or prop.is_readonly:
				continue
			if prop.type in {'POINTER', 'COLLECTION'}:
				continue
			if pid in exclude:
				continue
			if skip_stamp and (pid.startswith('stamp') or pid.startswith('use_stamp')):
				continue
			ids.append(pid)
	for pid in ids:
		try:
			setattr(dst, pid, getattr(src, pid))
		except Exception:
			pass


def sync_render_properties(src, dst):
	# scene.render minus output, border/region, and stamp/metadata
	exclude = set(RENDER_OUTPUT_ATTRS) | set(RENDER_NEVER_ATTRS)
	copy_struct(src.render, dst.render, exclude=exclude, skip_stamp=True)
	
	# Engine selection is copied above; copy engine-specific settings regardless
	# of the active engine so a later engine switch already has matched values
	copy_struct(src.eevee, dst.eevee)
	if hasattr(src, 'cycles') and hasattr(dst, 'cycles'):
		copy_struct(src.cycles, dst.cycles)
	if hasattr(src, 'cycles_curves') and hasattr(dst, 'cycles_curves'):
		copy_struct(src.cycles_curves, dst.cycles_curves)
	
	# Colour management: display device gates the view transform, which in turn
	# gates the available looks, so set those first before the generic walk
	copy_struct(src.display_settings, dst.display_settings)
	copy_struct(src.view_settings, dst.view_settings, include=('view_transform',))
	copy_struct(src.view_settings, dst.view_settings)
	copy_struct(src.sequencer_colorspace_settings, dst.sequencer_colorspace_settings)


def sync_output_properties(src, dst):
	# Explicit scene.render output scalars (resolution, frame rate, path, etc.)
	copy_struct(src.render, dst.render, include=RENDER_OUTPUT_ATTRS)
	
	# Image format: media type and file format gate the remaining options, so copy them first, then walk the rest
	copy_struct(src.render.image_settings, dst.render.image_settings, include=('media_type', 'file_format'))
	copy_struct(src.render.image_settings, dst.render.image_settings, exclude={'media_type', 'file_format'})
	
	# FFmpeg container and codec settings
	copy_struct(src.render.ffmpeg, dst.render.ffmpeg)


def sync_render_kit_properties(src, dst):
	copy_struct(src.render_kit_settings, dst.render_kit_settings, include=RENDER_KIT_ATTRS)



###########################################################################
# Operator

class render_sync_settings(bpy.types.Operator):
	bl_idname = "render.sync_scene_settings"
	bl_label = "Apply to Scenes"
	bl_description = "Copy the enabled property categories from the current scene to all checked target scenes"
	bl_options = {'REGISTER', 'UNDO'}
	
	@classmethod
	def poll(cls, context):
		settings = context.scene.render_kit_settings
		if not (settings.sync_render or settings.sync_output or settings.sync_renderkit):
			return False
		return any(scn != context.scene and scn.render_kit_settings.sync_target for scn in bpy.data.scenes)
	
	def execute(self, context):
		src = context.scene
		settings = src.render_kit_settings
		count = 0
		for dst in bpy.data.scenes:
			if dst == src or not dst.render_kit_settings.sync_target:
				continue
			if settings.sync_render:
				sync_render_properties(src, dst)
			if settings.sync_output:
				sync_output_properties(src, dst)
			if settings.sync_renderkit:
				sync_render_kit_properties(src, dst)
			count += 1
		self.report({'INFO'}, "Synced settings to " + str(count) + (" scene" if count == 1 else " scenes"))
		return {'FINISHED'}



###########################################################################
# Panel

class RENDER_PT_render_sync(bpy.types.Panel):
	bl_label = "Render Settings Sync"
	bl_space_type = 'PROPERTIES'
	bl_region_type = 'WINDOW'
	bl_context = "scene"
	bl_options = {'DEFAULT_CLOSED'}
	
	@classmethod
	def poll(cls, context):
		return context.preferences.addons[__package__].preferences.sync_enable and len(bpy.data.scenes) > 1
	
	def draw(self, context):
		settings = context.scene.render_kit_settings
		layout = self.layout
		layout.use_property_decorate = False
		
		# Source scene
		layout.label(text='Copy from "' + context.scene.name + '"', icon='SCENE_DATA')
		
		# Category toggles
		col = layout.column(align=True)
		col.prop(settings, 'sync_render')
		col.prop(settings, 'sync_output')
		col.prop(settings, 'sync_renderkit')
		
		# Target scenes (opt-in per scene, active scene excluded as the source)
		layout.separator()
		layout.label(text="Apply to Scenes:")
		targets = layout.column(align=True)
		target_count = 0
		for scn in bpy.data.scenes:
			if scn == context.scene:
				continue
			targets.prop(scn.render_kit_settings, 'sync_target', text=scn.name)
			if scn.render_kit_settings.sync_target:
				target_count += 1
		
		# Output path overwrite warning
		if settings.sync_output and target_count > 1 and '{{scene}}' not in context.scene.render.filepath:
			warning = layout.box().column(align=True)
			warning.label(text="Output paths will be copied exactly.", icon='ERROR')
			warning.label(text="Add {{scene}} to the path to avoid overwrites.")
		
		# Apply button
		layout.separator()
		row = layout.row()
		row.scale_y = 1.5
		row.enabled = (settings.sync_render or settings.sync_output or settings.sync_renderkit) and target_count > 0
		row.operator(
			'render.sync_scene_settings',
			text="Apply to " + str(target_count) + (" Scene" if target_count == 1 else " Scenes"),
			icon='CHECKMARK')



###########################################################################
# Addon registration functions
# •Define classes being registered
# •Registration function
# •Unregistration function

classes = (render_sync_settings, RENDER_PT_render_sync)

def register():
	# Register classes
	for cls in classes:
		bpy.utils.register_class(cls)

def unregister():
	# Deregister classes
	for cls in reversed(classes):
		bpy.utils.unregister_class(cls)

if __package__ == "__main__":
	register()
