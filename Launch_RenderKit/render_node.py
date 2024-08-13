import bpy
import os
import time
from .render_variables import replaceVariables, OutputVariablePopup
from .utility_time import secondsToStrings, secondsToReadable, readableToSeconds



class RENDERKIT_OT_render_node(bpy.types.Operator):
	bl_idname = "node.render_node"
	bl_label = "Render Node"
	bl_options = {'REGISTER', 'UNDO'}
	
	def invoke(self, context, event):
			return context.window_manager.invoke_props_dialog(self)
	
	def draw(self, context):
		try:
			layout = self.layout
			layout.label(text="Blender will be unresponsive while rendering")
		except Exception as exc:
			print(str(exc) + ' | Error in Render Kit: Render Node confirmation header')
	
	def execute(self, context):
		prefs = context.preferences.addons[__package__].preferences
		settings = context.scene.render_kit_settings
		
		# Check for active mesh object
		obj = context.active_object
		if not obj or obj.type != 'MESH':
			self.report({'ERROR'}, "Render Kit — Render Node no active mesh object selected")
			return {'CANCELLED'}
		
		# Check for active node
		source_node = context.active_node
		if not source_node:
			self.report({'ERROR'}, "Render Kit — Render Node no active node selected")
			return {'CANCELLED'}
		
		# Check if the selected output exists
		if settings.node_output not in [output.name for output in source_node.outputs]:
			self.report({'ERROR'}, f"Render Kit — Render Node output '{settings.node_output}' not found")
			return {'CANCELLED'}
		
		# Check for selected UV map
		uvmap = obj.data.uv_layers.get(settings.node_uvmap)
		if not uvmap:
			self.report({'ERROR'}, f"Render Kit — Render Node UV map '{settings.node_uvmap}' not found")
			return {'CANCELLED'}
		
		# Push undo state (because attempting to manually restore everything was way too frustrating)
		bpy.ops.ed.undo_push()
		
		# Get active scene
		scene = context.scene
		
		# Get the output socket by name
		original_node_output = {output.name: output for output in source_node.outputs}
		output_socket = original_node_output[settings.node_output]
		
		# Set active UV map
		obj.data.uv_layers.active = uvmap
		
		# Create render image
		bpy.ops.image.new(
			name='RenderKit_RenderNodeImage',
			width=1024,
			height=1024,
			color=(0.0, 0.0, 0.0, 0.0),
			alpha=True,
			generated_type='BLANK',
			float=True,
			use_stereo_3d=False,
			tiled=False)
		image = bpy.data.images["RenderKit_RenderNodeImage"]
		if settings.node_format != 'OPEN_EXR':
			image.use_half_precision = True
		
		# Get active node tree
		node_tree = obj.active_material.node_tree
		
		# Create temporary emission, output, and image nodes for rendering
		emission_node = node_tree.nodes.new(type='ShaderNodeEmission')
		
		output_node = node_tree.nodes.new(type='ShaderNodeOutputMaterial')
		output_node.select = True
		node_tree.nodes.active = output_node
		
		image_node = node_tree.nodes.new(type='ShaderNodeTexImage')
		image_node.image = image
		image_node.select = True
		node_tree.nodes.active = image_node
		
		# Connect source node output socket to output node input socket
		if output_socket:
			node_tree.links.new(output_socket, emission_node.inputs[0])
			node_tree.links.new(emission_node.outputs[0], output_node.inputs[0])
		else:
			self.report({'ERROR'}, "Render Kit — Render Node could not find the selected output socket")
			return {'CANCELLED'}
		
		# Store original settings
		# TODO: REMOVE THIS BLOCK IF POSSIBLE
		original_engine = scene.render.engine
		original_film = scene.render.film_transparent
		original_device = scene.cycles.device
		original_samples = scene.cycles.samples
		original_bake = scene.cycles.bake_type
		original_margin = scene.render.bake.margin
		original_clear = scene.render.bake.use_clear
		original_selectedtoactive = scene.render.bake.use_selected_to_active
		original_splitmaterials = scene.render.bake.use_split_materials
		
		# Set bake settings
		scene.render.engine = 'CYCLES'
		scene.render.film_transparent = True
		scene.cycles.device = 'GPU' if settings.node_render_device == 'GPU' else 'CPU'
		scene.cycles.samples = settings.node_samples
		scene.cycles.bake_type = 'EMIT'
		scene.render.bake.margin = settings.node_margin
		scene.render.bake.use_clear = False
		scene.render.bake.use_selected_to_active = False
		scene.render.bake.use_split_materials = False
		
		# Start render time
		settings.start_date = str(time.time())
		
		# Render to image
		bpy.ops.object.bake(type='EMIT')
		
		# Calculate render time and check for serial number
		render_time = round(time.time() - float(settings.start_date), 2)
		if '{serial}' in file_path:
			settings.output_file_serial_used = True
		
		# Replace variables again (this time with render time and serial number)
		file_path = replaceVariables(file_path, rendertime=render_time, serial=settings.output_file_serial)
		
		# Increment the output serial number if it was used in the output path
		if settings.output_file_serial_used:
			settings.output_file_serial += 1
		
		# Set target file location and name
		file_path = settings.node_filepath
		file_path += '.' + settings.node_format.replace("OPEN_EXR", "EXR").lower()
		file_path = replaceVariables(file_path, socket=settings.node_output)
		
		# Get absolute path
		abs_path = bpy.path.abspath(file_path)
		abs_dir = os.path.dirname(abs_path)
		if not os.path.exists(abs_dir):
			os.makedirs(abs_dir)
		# Save texture file
		image.filepath_raw = abs_path
		image.file_format = settings.node_format
		image.save()
		
		# Remove temporary nodes and image data
		# TODO: REMOVE THIS BLOCK IF POSSIBLE
		node_tree.nodes.remove(emission_node)
		node_tree.nodes.remove(output_node)
		node_tree.nodes.remove(image_node)
		bpy.data.images.remove(image)
		
		# Restore original settings
		# TODO: REMOVE THIS BLOCK IF POSSIBLE
		scene.render.engine = original_engine
		scene.render.film_transparent = original_film
		scene.cycles.device = original_device
		scene.cycles.samples = original_samples
		scene.cycles.bake_type = original_bake
		scene.render.bake.margin = original_margin
		scene.render.bake.use_clear = original_clear
		scene.render.bake.use_selected_to_active = original_selectedtoactive
		scene.render.bake.use_split_materials = original_splitmaterials
		
		# Reselect original active node
		source_node.select = True
		node_tree.nodes.active = source_node
		
		# Provide success feedback
		self.report({'INFO'}, f"Node render saved to {file_path}")
		if prefs.rendernode_confirm:
			self.show_completion_popup(context, file_path, render_time)
		
		return {'FINISHED'}
	
	def show_completion_popup(self, context, filepath, rendertime):
		def draw(self, context):
			self.layout.label(text=filepath)
		context.window_manager.popup_menu(draw, title="Render Node Completed " + secondsToReadable(rendertime), icon='NODE_TEXTURE') # NODE NODE_SEL NODETREE NODE_TEXTURE SHADING_RENDERED SHADING_TEXTURE



class RENDERKIT_PT_render_node(bpy.types.Panel):
	bl_space_type = 'NODE_EDITOR'
	bl_region_type = 'UI'
	bl_category = 'Node'
	bl_label = "Render Node to Image"
	
	@classmethod
	def poll(cls, context):
		prefs = context.preferences.addons[__package__].preferences
		obj = context.active_object
		return (prefs.rendernode_enable and context.space_data.tree_type == 'ShaderNodeTree' and context.object.active_material is not None and obj and obj.type == 'MESH' and context.active_node)
		# context.scene.node_tree.type ?
	
	def draw(self, context):
		settings = context.scene.render_kit_settings
		
		layout = self.layout
		
		layout.prop(settings, "node_render_device", expand=True)
		
		grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=True)
		grid.prop(settings, "node_resolution_x", text='X')
		grid.prop(settings, "node_resolution_y", text='Y')
		grid.prop(settings, "node_samples")
		grid.prop(settings, "node_margin")
		
#		layout.prop(settings, "node_color_space")#, expand=True)
		layout.prop(settings, "node_format", expand=True)
		
		# Naming variables popup and output serial number
		ops = layout.operator(OutputVariablePopup.bl_idname, text = "Variable List", icon = "LINENUMBERS_OFF")
		ops.postrender = True
		ops.noderender = True
		ops.autoclose = True
		input = layout.row()
		if not '{serial}' in settings.node_filepath:
			input.active = False
			input.enabled = False
		input.prop(settings, 'output_file_serial')
		
		# Primary items that might actually need to change
		layout.prop(settings, "node_filepath")
		layout.prop_search(settings, "node_uvmap", context.active_object.data, "uv_layers")
		layout.prop_search(settings, "node_output", context.active_node, "outputs")
		
		# Render node button
		layout.operator(RENDERKIT_OT_render_node.bl_idname)



# Class Registration

#def menu_func(self, context):
#	self.layout.operator(RENDERKIT_OT_render_node.bl_idname)

def register():
	bpy.utils.register_class(RENDERKIT_OT_render_node)
	bpy.utils.register_class(RENDERKIT_PT_render_node)
#	bpy.types.NODE_MT_context_menu.append(menu_func)

def unregister():
	bpy.utils.unregister_class(RENDERKIT_OT_render_node)
	bpy.utils.unregister_class(RENDERKIT_PT_render_node)
#	bpy.types.NODE_MT_context_menu.remove(menu_func)

if __name__ == "__main__":
	register()
	